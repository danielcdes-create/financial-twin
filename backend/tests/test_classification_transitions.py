"""Explicit user clears and required-field transitions; only in-memory fakes."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app import main
from app.source_tokens import source_token
from test_ingestion import FakeDB
from test_transaction_review import ReviewCollection

if original := os.environ.get('CLASSIFICATION_ORIGINAL'):
    spec = importlib.util.spec_from_file_location('app.classification_before', original)
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)


class ClassificationTransitionsTests(unittest.TestCase):
    def setUp(self):
        self.platform = {'_id': ObjectId(), 'nome_padrao': 'Plataforma sintética'}
        self.row = {'_id': ObjectId(), 'descricao_original': 'Compra sintética',
                    'valor': Decimal128('-10.00'), 'tipo': 'compra', 'fluxo': 'saida',
                    'categoria': 'Alimentação', 'subcategoria': 'Anterior',
                    'metodo_pagamento': 'pix', 'localidade': 'Local anterior',
                    'detalhe': 'Detalhe anterior', 'platform_id': self.platform['_id']}
        self.db = FakeDB(transactions=ReviewCollection([self.row]), merchants=ReviewCollection([self.platform]))

    def update(self, **fields):
        value = main.TransactionClassificationUpdate(expected_token=source_token(self.row), **fields)
        return main._update_transaction_classification(str(self.row['_id']), value, self.db)

    def test_explicit_optional_clears_are_persisted_and_audited(self):
        result = self.update(subcategoria='', metodo_pagamento='', plataforma='', localidade='', detalhe='')
        self.assertTrue(result['changed'])
        for field in ('subcategoria', 'localidade', 'detalhe'):
            self.assertFalse(self.row.get(field))
        self.assertNotIn('metodo_pagamento', self.row)
        self.assertNotIn('platform_id', self.row)
        self.assertEqual(self.row['descricao_original'], 'Compra sintética')
        after = self.db.audit_log.docs[-1]['after']
        self.assertIsNone(after['plataforma'])
        self.assertIsNone(after['metodo_pagamento'])

    def test_category_change_accepts_explicit_dependent_subcategory_clear(self):
        self.update(categoria='Transporte', subcategoria='')
        self.assertEqual(self.row['categoria'], 'Transporte')
        self.assertFalse(self.row.get('subcategoria'))

    def test_omitted_optionals_preserve_their_previous_values(self):
        self.row.pop('subcategoria')
        self.update(detalhe='Novo detalhe')
        self.assertEqual(self.row['metodo_pagamento'], 'pix')
        self.assertEqual(self.row['platform_id'], self.platform['_id'])

    def test_whitespace_cannot_clear_required_classification(self):
        for field in ('categoria', 'tipo', 'competencia', 'localidade_contexto', 'status_conciliacao'):
            with self.subTest(field=field):
                with self.assertRaises(HTTPException) as failure:
                    self.update(**{field: ' '})
                self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(self.row['categoria'], 'Alimentação')

    def test_provisional_optional_clears_preserve_revision_and_raw_evidence(self):
        row = {**self.row, 'provisional_id': 'clear-synthetic', 'revision': 1, 'status': 'enriched',
               'merchant_raw': 'Evidência original', 'categoria_sugerida': 'Alimentação'}
        self.db.provisional_transactions = ReviewCollection([row])
        value = main.ProvisionalUpdate(expected_revision=1, categoria_sugerida='', subcategoria='',
                                       metodo_pagamento='', plataforma='', localidade='', detalhe='')
        main._atomic_update_provisional(row['provisional_id'], value, self.db)
        self.assertEqual(row['revision'], 2)
        self.assertEqual(row['merchant_raw'], 'Evidência original')
        self.assertNotIn('platform_id', row)
        self.assertNotIn('metodo_pagamento', row)
        self.assertFalse(row.get('categoria_sugerida'))
        after = self.db.audit_log.docs[-1]['after']
        self.assertIsNone(after['plataforma'])
        self.assertIsNone(after['metodo_pagamento'])
        self.assertNotIn('vendedor', after, 'Clearing another field must not claim that the vendor was cleared')

    def test_exact_batch_propagates_optional_clears(self):
        peer = {**self.row, '_id': ObjectId()}
        self.db.transactions.docs.append(peer)
        matches = [(peer, 'Compra sintética')]
        with patch.object(main, 'batch_classification_matches', return_value=matches):
            token = main.preview_signature([peer], str(self.row['_id']))
            self.update(subcategoria='', metodo_pagamento='', plataforma='', apply_similar_classification=True,
                        classification_preview_token=token)
        self.assertFalse(peer.get('subcategoria'))
        self.assertNotIn('metodo_pagamento', peer)
        self.assertNotIn('platform_id', peer)

    def test_legacy_platform_clear_removes_both_representations_and_audits_previous_value(self):
        for kind in ('provisional', 'transaction'):
            for has_platform_id in (False, True):
                with self.subTest(kind=kind, has_platform_id=has_platform_id):
                    row = {**self.row, '_id': ObjectId(), 'plataforma': 'Plataforma legada sintética'}
                    if not has_platform_id:
                        row.pop('platform_id')
                    previous = self.platform['nome_padrao'] if has_platform_id else row['plataforma']
                    peer = {**row, '_id': ObjectId()}
                    db = FakeDB(merchants=ReviewCollection([self.platform]))
                    if kind == 'provisional':
                        row.update(provisional_id='legacy-platform-synthetic', revision=1, status='enriched',
                                   merchant_raw='Evidência original')
                        db.provisional_transactions = ReviewCollection([row])
                        result = main._atomic_update_provisional(
                            row['provisional_id'], main.ProvisionalUpdate(expected_revision=1, plataforma=''), db)
                        self.assertEqual(row['revision'], 2)
                        self.assertEqual(row['merchant_raw'], 'Evidência original')
                    else:
                        # Unsetting an old field also repairs rows admitted by an older schema.
                        db.transactions = ReviewCollection([row, peer])
                        with patch.object(main, 'batch_classification_matches', return_value=[(peer, 'Compra sintética')]):
                            value = main.TransactionClassificationUpdate(
                                expected_token=source_token(row), plataforma='', apply_similar_classification=True,
                                classification_preview_token=main.preview_signature([peer], str(row['_id'])))
                            result = main._update_transaction_classification(str(row['_id']), value, db)
                        self.assertNotIn('platform_id', peer)
                        self.assertNotIn('plataforma', peer)
                        self.assertEqual(row['descricao_original'], 'Compra sintética')
                    self.assertTrue(result['changed'])
                    self.assertNotIn('platform_id', row)
                    self.assertNotIn('plataforma', row)
                    self.assertFalse(result['item'].get('plataforma'))
                    event = 'provisional_transaction_updated' if kind == 'provisional' else 'transaction_classification_updated'
                    audit = next(entry for entry in db.audit_log.docs if entry['event'] == event)
                    self.assertEqual(audit['before']['plataforma'], previous)
                    self.assertIsNone(audit['after']['plataforma'])
                    self.assertNotIn('vendedor', audit['after'])
                    self.assertEqual(len(db.merchants.docs), 1)

    def test_whitespace_platform_clear_never_creates_a_blank_merchant(self):
        for kind in ('provisional', 'transaction'):
            for has_platform_id in (False, True):
                with self.subTest(kind=kind, has_platform_id=has_platform_id):
                    row = {**self.row, '_id': ObjectId()}
                    if not has_platform_id:
                        row.pop('platform_id')
                    db = FakeDB(merchants=ReviewCollection([self.platform]))
                    if kind == 'provisional':
                        row.update(provisional_id='whitespace-platform-synthetic', revision=1, status='enriched',
                                   merchant_raw='Evidência original')
                        db.provisional_transactions = ReviewCollection([row])
                        result = main._atomic_update_provisional(
                            row['provisional_id'], main.ProvisionalUpdate(expected_revision=1, plataforma=' \t\n '), db)
                        self.assertEqual(row['merchant_raw'], 'Evidência original')
                        self.assertEqual(row['revision'], 2 if has_platform_id else 1)
                    else:
                        db.transactions = ReviewCollection([row])
                        value = main.TransactionClassificationUpdate(expected_token=source_token(row), plataforma=' \t\n ')
                        result = main._update_transaction_classification(str(row['_id']), value, db)
                        self.assertEqual(row['descricao_original'], 'Compra sintética')
                    self.assertNotIn('platform_id', row)
                    self.assertFalse(result['item'].get('plataforma'))
                    self.assertEqual(result['changed'], has_platform_id)
                    self.assertEqual(db.merchants.docs, [self.platform])
                    self.assertTrue(all(merchant.get('nome_padrao', '').strip() for merchant in db.merchants.docs))
                    if has_platform_id:
                        audit = db.audit_log.docs[-1]
                        self.assertEqual(audit['before']['plataforma'], self.platform['nome_padrao'])
                        self.assertIsNone(audit['after']['plataforma'])
                    else:
                        self.assertEqual(db.audit_log.docs, [])


if __name__ == '__main__':
    unittest.main()
