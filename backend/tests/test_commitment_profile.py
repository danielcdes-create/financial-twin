import copy
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.commitment_profile import ProfileWrite, StatementProfileWrite, apply_commitment_profile, close_imported_open, sync_statement_metadata
from app.future_commitments import effective_document, future_obligations_view
from test_capture_batches import BatchDB


def money(value):
    return Decimal128(str(value))


class CommitmentProfileTests(unittest.TestCase):
    def test_app_route_matches_the_editor(self):
        from app.commitment_profile import router
        paths = {getattr(route, 'path', '') for route in router.routes}
        self.assertIn('/v1/commitment-profile', paths)
        self.assertIn('/v1/commitment-profile/from-statement', paths)


    def setUp(self):
        clock = patch('app.future_commitments.today', return_value=date(2026, 10, 14))
        clock.start()
        self.addCleanup(clock.stop)
        self.db = BatchDB()
        for name in ('transactions', 'future_schedule_overrides', 'future_commitment_commands', 'audit_log', 'commitment_profile_commands', 'reconciliations', 'commitments', 'installment_plans', 'obligations'):
            self.db[name]

    def tx(self, identity, valor='-10.00', day='2026-10-12'):
        document = {'_id': identity, 'valor': money(valor), 'fluxo': 'saida', 'natureza': 'COMPRA', 'vendedor': 'Codigo bruto',
            'descricao_original': 'LOJA1000', 'data_evento': datetime.fromisoformat(day + 'T15:00:00+00:00'), 'categoria': 'Outros', 'subcategoria': ''}
        self.db.transactions.docs.append(document)
        return document

    def test_metadata_reaches_every_linked_row_and_amount_stays_inside_scope(self):
        paid = self.tx('tx-paid', '-10.00', '2026-09-12')
        opened = self.tx('tx-open')
        later = self.tx('tx-later', '-10.00', '2026-12-12')
        plan_id = ObjectId()
        plan = {'_id': plan_id, 'descricao': 'LOJA1000', 'status': 'ativo', 'numero_parcelas': 3,
            'installments': [
                {'numero': 1, 'competencia': '2026-09', 'valor': money('10.00'), 'status': 'paga', 'transaction_id': 'tx-paid'},
                {'numero': 2, 'competencia': '2026-11', 'data_vencimento': datetime(2026, 11, 2, tzinfo=timezone.utc), 'valor': money('10.00'), 'status': 'projetada', 'open_statement_id': 'tx-open'},
                {'numero': 3, 'competencia': '2026-12', 'data_vencimento': datetime(2026, 12, 2, tzinfo=timezone.utc), 'valor': money('10.00'), 'status': 'projetada', 'open_statement_id': 'tx-later'},
            ]}
        self.db.installment_plans.docs.append(copy.deepcopy(plan))
        saved = apply_commitment_profile(self.db, ProfileWrite(
            request_id=uuid4(), source='installment', identity=str(plan_id), display_name='Loja Sintetica',
            natureza='COMPRA', categoria='Saude', subcategoria='farmacia', metodo_pagamento='cartao_credito',
            amount=Decimal('12.50'), due_date=date(2026, 11, 5), scope='next'))
        self.assertTrue(saved['persisted'])
        self.assertEqual(self.db.installment_plans.docs[0]['descricao'], 'LOJA1000')
        self.assertEqual(self.db.installment_plans.docs[0]['installments'], plan['installments'])
        for identity in ('tx-paid', 'tx-open', 'tx-later'):
            row = next(item for item in self.db.transactions.docs if item['_id'] == identity)
            self.assertEqual(row['vendedor'], 'Loja Sintetica')
            self.assertEqual(row['categoria'], 'Saude')
            self.assertEqual(row['subcategoria'], 'farmacia')
            self.assertEqual(row['metodo_pagamento'], 'cartao_credito')
            self.assertEqual(row['descricao_original'], 'LOJA1000')
        self.assertEqual(paid['valor'].to_decimal(), Decimal('-10.00'))
        self.assertEqual(opened['valor'].to_decimal(), Decimal('-12.50'))
        self.assertEqual(later['valor'].to_decimal(), Decimal('-10.00'))
        self.assertEqual(opened['data_evento'].date().isoformat(), '2026-11-05')
        shown = effective_document(self.db, 'installment', self.db.installment_plans.docs[0])
        self.assertEqual(shown['descricao_original'], 'LOJA1000')
        self.assertEqual(shown['descricao'], 'Loja Sintetica')
        self.assertEqual(shown['installments'][0]['valor'].to_decimal(), Decimal('10.00'))
        self.assertEqual(shown['installments'][1]['valor'].to_decimal(), Decimal('12.50'))
        self.assertEqual(shown['installments'][2]['valor'].to_decimal(), Decimal('10.00'))
        apply_commitment_profile(self.db, ProfileWrite(
            request_id=uuid4(), source='installment', identity=str(plan_id), amount=Decimal('8.00'), scope='future'))
        self.assertEqual(paid['valor'].to_decimal(), Decimal('-10.00'))
        self.assertEqual(opened['valor'].to_decimal(), Decimal('-8.00'))
        self.assertEqual(later['valor'].to_decimal(), Decimal('-8.00'))
        shown = effective_document(self.db, 'installment', self.db.installment_plans.docs[0])
        self.assertEqual(shown['installments'][0]['status'], 'paga')
        self.assertEqual([part['valor'].to_decimal() for part in shown['installments'][1:]], [Decimal('8.00'), Decimal('8.00')])
        before = {row['_id']: row['valor'].to_decimal() for row in self.db.transactions.docs}
        sync_statement_metadata(self.db, StatementProfileWrite(request_id=uuid4(), transaction_id='tx-open', vendedor='Loja Sintetica', categoria='Casa', natureza='COMPRA'))
        self.assertEqual(before, {row['_id']: row['valor'].to_decimal() for row in self.db.transactions.docs})
        self.assertTrue(all(row['categoria'] == 'Casa' for row in self.db.transactions.docs))
        close_imported_open(self.db, 'installment', str(plan_id))
        shown = effective_document(self.db, 'installment', self.db.installment_plans.docs[0])
        self.assertEqual([part['status'] for part in shown['installments']], ['paga', 'cancelada', 'cancelada'])
        self.assertEqual(self.db.installment_plans.docs[0], self.db.installment_plans.docs[0])
        again = effective_document(self.db, 'installment', copy.deepcopy(plan))
        self.assertEqual([part['status'] for part in again['installments']], ['paga', 'cancelada', 'cancelada'])
        self.assertEqual(paid['valor'].to_decimal(), Decimal('-10.00'))

    def test_financing_series_edits_the_whole_open_set_and_close_does_not_recreate_it(self):
        self.tx('tx-paid-debt', '-40.00', '2026-09-08')
        self.tx('tx-next', '-20.00', '2026-11-08')
        self.tx('tx-after', '-20.00', '2026-12-08')
        paid_id, next_id, after_id = ObjectId(), ObjectId(), ObjectId()
        rows = [
            {'_id': paid_id, 'status': 'futura', 'tipo': 'financiamento', 'descricao': 'DIVIDA1000', 'external_key': 'sintetico|divida|parcela-1',
             'data_vencimento': datetime(2026, 9, 8, tzinfo=timezone.utc), 'saldo_aberto': money('20.00'), 'valor_pago': money('20.00'), 'transaction_id': 'tx-paid-debt'},
            {'_id': next_id, 'status': 'futura', 'tipo': 'financiamento', 'descricao': 'DIVIDA1000', 'external_key': 'sintetico|divida|parcela-2',
             'data_vencimento': datetime(2026, 11, 8, tzinfo=timezone.utc), 'saldo_aberto': money('20.00'), 'valor_pago': money('0.00'), 'open_statement_id': 'tx-next'},
            {'_id': after_id, 'status': 'futura', 'tipo': 'financiamento', 'descricao': 'DIVIDA1000', 'external_key': 'sintetico|divida|parcela-3',
             'data_vencimento': datetime(2026, 12, 8, tzinfo=timezone.utc), 'saldo_aberto': money('20.00'), 'valor_pago': money('0.00'), 'open_statement_id': 'tx-after'},
        ]
        originals = copy.deepcopy(rows)
        self.db.obligations.docs.extend(rows)
        apply_commitment_profile(self.db, ProfileWrite(
            request_id=uuid4(), source='obligation', identity='sintetico|divida', display_name='Divida Sintetica',
            natureza='COMPRA', categoria='Moradia', subcategoria='financiamento', amount=Decimal('15.00'), scope='next'))
        self.assertEqual(self.db.obligations.docs, originals)
        self.assertEqual(next(row['vendedor'] for row in self.db.transactions.docs if row['_id'] == 'tx-paid-debt'), 'Divida Sintetica')
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-next'), Decimal('-15.00'))
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-after'), Decimal('-20.00'))
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-paid-debt'), Decimal('-40.00'))
        apply_commitment_profile(self.db, ProfileWrite(request_id=uuid4(), source='obligation', identity='sintetico|divida', amount=Decimal('9.00'), scope='future'))
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-paid-debt'), Decimal('-40.00'))
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-next'), Decimal('-9.00'))
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-after'), Decimal('-9.00'))
        first = close_imported_open(self.db, 'obligation', 'sintetico|divida')
        self.assertEqual(first['closed'], 2)
        listed = [item['id'] for item in future_obligations_view(self.db)['items']]
        self.assertNotIn(str(next_id), listed)
        self.assertNotIn(str(after_id), listed)
        second = close_imported_open(self.db, 'obligation', 'sintetico|divida')
        self.assertEqual(second['closed'], 0)
        self.assertEqual([item['id'] for item in future_obligations_view(self.db)['items']], listed)
        self.assertEqual(self.db.obligations.docs, originals)
        self.assertEqual(next(row['valor'].to_decimal() for row in self.db.transactions.docs if row['_id'] == 'tx-paid-debt'), Decimal('-40.00'))

    def test_live_statement_row_supplies_the_seller_category_and_card(self):
        from app.installment_cards import attach_cards
        from app.main import with_installment_references, with_merchant_names
        plan_id, card_id, bank_id, tx_id = ObjectId(), ObjectId(), ObjectId(), ObjectId()
        self.db.accounts.docs.append({'_id': card_id, 'tipo': 'cartao_credito', 'instituicao': 'Banco', 'cartao_final': '1111'})
        self.db.accounts.docs.append({'_id': bank_id, 'tipo': 'conta_corrente', 'instituicao': 'Banco'})
        self.db.account_profiles.docs.append({'_id': f'card-profile:{card_id}', 'values': {'apelido': 'Cartao Sintetico 1111'}})
        self.db.installment_card_links.docs.append({'_id': str(plan_id), 'card_id': str(card_id)})
        document = {'_id': tx_id, 'vendedor': 'Loja Sintetica', 'categoria': 'Saude', 'subcategoria': 'farmacia',
            'natureza': 'COMPRA', 'metodo_pagamento': 'cartao_credito', 'descricao_original': 'LOJA1000',
            'account_id': bank_id, 'valor': money('-10.00')}
        self.db.transactions.docs.append(document)
        plan = {'_id': plan_id, 'descricao': 'LOJA1000', 'status': 'ativo', 'numero_parcelas': 2,
            'installments': [{'numero': 1, 'status': 'paga', 'transaction_id': tx_id},
                             {'numero': 2, 'status': 'projetada', 'valor': money('10.00')}]}
        self.db.installment_plans.docs.append(copy.deepcopy(plan))
        listed = attach_cards(self.db, [plan])[0]
        self.assertEqual(listed['descricao'], 'LOJA1000')
        self.assertEqual(listed['display_name'], 'Loja Sintetica')
        self.assertEqual(listed['linked_purchase']['vendedor'], 'Loja Sintetica')
        self.assertEqual(listed['categoria'], 'Saude')
        self.assertEqual(listed['subcategoria'], 'farmacia')
        self.assertEqual(listed['card_label'], 'Cartao Sintetico 1111')
        self.assertFalse(self.db.audit_log.docs)
        enriched = with_merchant_names([document], self.db)
        with_installment_references([document], enriched, self.db)
        self.assertEqual(enriched[0]['card_label'], 'Cartao Sintetico 1111')
        self.assertEqual(enriched[0]['installment_plan']['card_label'], 'Cartao Sintetico 1111')
        self.assertEqual(enriched[0]['installment_plan']['name'], 'Loja Sintetica')
        self.assertNotIn('cartao_label', enriched[0])
        painted = apply_commitment_profile(self.db, ProfileWrite(
            request_id=uuid4(), source='installment', identity=str(plan_id), card_id='Cartao Sintetico 2222'))
        self.assertTrue(painted['persisted'])
        self.assertEqual(document['card_label'], 'Cartao Sintetico 2222')
        self.assertNotEqual(document.get('card_id'), 'Cartao Sintetico 2222')
        from app.future_commitments import effective_documents
        visible = attach_cards(self.db, effective_documents(self.db, 'installment', self.db.installment_plans.docs))[0]
        self.assertEqual(visible['card_label'], 'Cartao Sintetico 2222')
        self.assertEqual(visible['profile']['card_label'], 'Cartao Sintetico 2222')
        again = with_merchant_names([document], self.db)
        with_installment_references([document], again, self.db)
        self.assertEqual(again[0]['card_label'], 'Cartao Sintetico 2222')
        self.assertEqual(again[0]['installment_plan']['card_label'], 'Cartao Sintetico 2222')
