"""NF07/NF08: explicit classification intent and committed peer audit values."""
import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
from app import main
from app.economic_policy import effects
from app.source_tokens import source_token
from test_architecture_failures import rollback_fake
from test_ingestion import FakeDB
from test_transaction_review import ReviewCollection


class BulkAuditFailure(ReviewCollection):
    fail = True

    def insert_one(self, entry):
        if entry.get('event') == 'transaction_classification_bulk_propagated' and self.fail:
            self.fail = False
            raise RuntimeError('injected peer audit failure')
        return super().insert_one(entry)


class ClassificationDomainTests(unittest.TestCase):
    def setUp(self):
        self.row = {'_id': ObjectId(), 'valor': Decimal128('100.00'), 'fluxo': 'entrada',
                    'tipo': 'receita', 'categoria': 'Viagem',
                    'descricao_original': 'REEMBOLSO AMIGO VIAGEM',
                    'data_evento': datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
                    'classification': {'migration_reason': 'receita_ou_movimentacao'}}
        self.db = FakeDB(transactions=ReviewCollection([self.row]),
                         audit_log=ReviewCollection(), merchants=ReviewCollection())

    def update(self, **fields):
        return main._update_transaction_classification(str(self.row['_id']),
            main.TransactionClassificationUpdate(expected_token=source_token(self.row), **fields), self.db)

    def test_note_only_does_not_accept_inferred_nature_ledger(self):
        previous = effects(self.row)
        result = self.update(detalhe='Nota explícita', validate_review=True)
        self.assertEqual(self.row['detalhe'], 'Nota explícita')
        self.assertNotIn('natureza', self.row)
        self.assertEqual(effects(self.row), previous)
        self.assertEqual(result['item']['status_revisao'], 'validado')
        audit = next(entry for entry in self.db.audit_log.docs
                     if entry['event'] == 'transaction_classification_updated')
        self.assertEqual(audit['after'], {'detalhe': 'Nota explícita'})

    def test_note_only_does_not_accept_inferred_nature_confirmed_capture(self):
        capture = {**self.row, 'provisional_id': 'note-only', 'revision': 1,
                   'status': 'confirmed', 'categoria_sugerida': 'Viagem',
                   'merchant_raw': 'REEMBOLSO AMIGO VIAGEM'}
        self.db.provisional_transactions = ReviewCollection([capture])
        previous = effects(capture, captured=True)
        main._atomic_update_provisional(capture['provisional_id'],
            main.ProvisionalUpdate(expected_revision=1, detalhe='Nota explícita', validate_review=True), self.db)
        self.assertEqual(capture['revision'], 2)
        self.assertNotIn('natureza', capture)
        self.assertEqual(effects(capture, captured=True), previous)
        audit = next(entry for entry in self.db.audit_log.docs
                     if entry['event'] == 'provisional_transaction_updated')
        self.assertNotIn('natureza', audit['after'])

    def test_explicit_nature_acceptance_changes_and_audits_economic_effect(self):
        for captured in (False, True):
            with self.subTest(captured=captured):
                self.setUp()
                row = self.row
                if captured:
                    row.update(provisional_id='explicit-refund', revision=1, status='confirmed',
                               categoria_sugerida='Viagem', merchant_raw='REEMBOLSO AMIGO VIAGEM')
                    self.db.provisional_transactions = ReviewCollection([row])
                    main._atomic_update_provisional(row['provisional_id'],
                        main.ProvisionalUpdate(expected_revision=1, natureza='ESTORNO'), self.db)
                else:
                    self.update(natureza='ESTORNO')
                final = effects(row, captured=captured)
                self.assertEqual(final['income'], 0)
                self.assertEqual(final['expenses'], -100)
                audit = self.db.audit_log.docs[0]
                self.assertIsNone(audit['before']['natureza'])
                self.assertEqual(audit['after']['natureza'], 'ESTORNO')

    def peers(self):
        platform = {'_id': ObjectId(), 'nome_padrao': 'Plataforma normalizada'}
        self.db.merchants.docs.append(platform)
        peers = [
            {**self.row, '_id': ObjectId(), 'subcategoria': 'Cinema', 'metodo_pagamento': 'debito',
             'platform_id': platform['_id'], 'plataforma': 'Plataforma legada'},
            {**self.row, '_id': ObjectId(), 'subcategoria': 'Passeios',
             'plataforma': 'Somente legada'},
        ]
        self.db.transactions.docs.extend(peers)
        # Production matching returns a projection, not the prior optional values.
        matches = [({'_id': peer['_id'], 'valor': peer['valor'],
                     'descricao_original': peer['descricao_original']}, peer['descricao_original'])
                   for peer in peers]
        return peers, matches

    def bulk_update(self, matches, **fields):
        value = main.TransactionClassificationUpdate(expected_token=source_token(self.row),
            apply_similar_classification=True,
            classification_preview_token=main.preview_signature([peer for peer, _ in matches], str(self.row['_id'])),
            **fields)
        with patch.object(main, 'batch_classification_matches', return_value=matches):
            return main._update_transaction_classification(str(self.row['_id']), value, self.db)

    def test_bulk_classification_audits_real_before_after_for_every_peer(self):
        peers, matches = self.peers()
        previous = copy.deepcopy(peers)
        result = self.bulk_update(matches, subcategoria='', metodo_pagamento='', plataforma='')
        audit = next(entry for entry in self.db.audit_log.docs
                     if entry['event'] == 'transaction_classification_bulk_propagated')
        self.assertEqual(audit['transaction_count'], 2)
        self.assertEqual(result['classification_propagated_count'], 2)
        records = {entry['id']: entry for entry in audit['matched_records']}
        self.assertEqual(set(records), {peer['_id'] for peer in peers})
        for original, final in zip(previous, peers):
            record = records[final['_id']]
            self.assertEqual(record['before']['subcategoria'], original['subcategoria'])
            self.assertEqual(record['after']['subcategoria'], '')
            for field in ('platform_id', 'plataforma'):
                self.assertEqual(record['before'][field], original.get(field))
                self.assertIsNone(record['after'][field])
                self.assertIn(field, record['after_missing'])
                self.assertEqual(field in record['before_missing'], field not in original)
                self.assertNotIn(field, final)
            if 'metodo_pagamento' in original:
                self.assertEqual(record['before']['metodo_pagamento'], 'debito')
                self.assertIsNone(record['after']['metodo_pagamento'])
                self.assertIn('metodo_pagamento', record['after_missing'])
            self.assertNotIn('valor', record['before'], 'Audit stores changed classification fields only')

    def test_validation_only_batch_audits_actual_previous_peer_status(self):
        peers, matches = self.peers()
        peers[0]['status_revisao'] = 'pendente'
        self.bulk_update(matches, validate_review=True)
        audit = next(entry for entry in self.db.audit_log.docs
                     if entry['event'] == 'transaction_classification_bulk_propagated')
        records = {entry['id']: entry for entry in audit['matched_records']}
        self.assertEqual(records[peers[0]['_id']]['before']['status_revisao'], 'pendente')
        self.assertEqual(records[peers[0]['_id']]['after']['status_revisao'], 'validado')
        self.assertIn('status_revisao', records[peers[1]['_id']]['before_missing'])
        self.assertEqual(records[peers[1]['_id']]['after']['status_revisao'], 'validado')

    def test_bulk_audit_failure_rolls_back_source_peers_audit_and_command(self):
        peers, matches = self.peers()
        self.db['audit_log'] = BulkAuditFailure()
        body = main.TransactionClassificationUpdate(expected_token=source_token(self.row),
            subcategoria='', metodo_pagamento='', plataforma='', apply_similar_classification=True,
            classification_preview_token=main.preview_signature([peer for peer, _ in matches], str(self.row['_id'])))
        self.db['batch_commands'] = ReviewCollection([{'_id': 'rollback-classification', 'status': 'pending',
            'transaction_id': str(self.row['_id']), 'payload': body.model_dump(mode='json')}])
        before = copy.deepcopy(self.db.transactions.docs)
        with patch.object(main, 'mongo_transaction', rollback_fake), \
             patch.object(main, 'batch_classification_matches', return_value=matches):
            with self.assertRaisesRegex(RuntimeError, 'injected peer audit failure'):
                main.run_classification_command(self.db, 'rollback-classification')
            self.assertEqual(self.db.transactions.docs, before)
            self.assertEqual(self.db.audit_log.docs, [])
            self.assertEqual(self.db.batch_commands.docs[0]['status'], 'pending')
            result = main.run_classification_command(self.db, 'rollback-classification')
            self.assertEqual(result['classification_propagated_count'], len(peers))
            repeated = main.run_classification_command(self.db, 'rollback-classification')
            self.assertEqual(repeated['classification_propagated_count'], len(peers))
        bulk = [entry for entry in self.db.audit_log.docs
                if entry['event'] == 'transaction_classification_bulk_propagated']
        self.assertEqual(len(bulk), 1, 'Completed retry does not duplicate the audit')
        self.assertEqual(len(bulk[0]['matched_records']), len(peers))


if __name__ == '__main__':
    unittest.main()
