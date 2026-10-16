import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import commitment_statement_page, statement_lookup_page
from test_capture_batches import BatchDB


class StatementSearchTests(unittest.TestCase):
    def setUp(self):
        self.db = BatchDB()
        self.commitment = {
            'commitment_id': 'servico-sintetico', 'kind': 'assinatura', 'name': 'Servico sintetico',
            'amount': '80.00', 'frequency': 'mensal', 'status': 'ativo', 'renewal_status': 'automatica',
            'purchase_date': '2026-01-01', 'next_due_date': '2026-12-01', 'revision': 4,
        }
        self.db.commitments.docs.append(self.commitment)

    def expense(self, day, value, description, **extra):
        row = {
            '_id': ObjectId(), 'data_evento': datetime.fromisoformat(day).replace(hour=15, tzinfo=timezone.utc),
            'valor': Decimal128(str(Decimal(value).copy_abs() * -1)), 'fluxo': 'saida', 'moeda': 'BRL',
            'classification': {'migration_reason': 'despesa_real'}, 'descricao_original': description,
            'estabelecimento_original': description,
        }
        row.update(extra)
        self.db.transactions.docs.append(row)
        return row

    def search(self, **kwargs):
        values = {'q': None, 'date_from': None, 'date_to': None, 'amount': None, 'amount_min': None, 'amount_max': None, 'limit': 40, 'offset': 0}
        values.update(kwargs)
        return commitment_statement_page(self.commitment['commitment_id'], db=self.db, **values)

    def test_name_and_amount_reach_rows_the_candidate_list_hides(self):
        near = self.expense('2026-11-28', '-80.00', 'SERVICO SINTETICO MENSAL')
        far = self.expense('2026-01-15', '-12.00', 'Padaria distante')
        capture = {'_id': ObjectId(), 'provisional_id': 'cap-sintetica', 'status': 'confirmed',
            'data_evento': datetime(2026, 3, 2, 15, tzinfo=timezone.utc), 'valor': Decimal128('-33.00'),
            'fluxo': 'saida', 'moeda': 'BRL', 'classification': {'migration_reason': 'despesa_real'},
            'merchant_raw': 'Captura distante'}
        promoted = {**capture, '_id': ObjectId(), 'provisional_id': 'cap-promovida', 'linked_transaction_id': str(far['_id']), 'merchant_raw': 'Captura promovida'}
        self.db.provisional_transactions.docs.extend([capture, promoted])
        transfer = self.expense('2026-01-16', '-12.00', 'Padaria transferencia', natureza='TRANSFERENCIA')
        self.commitment['linked_transactions'] = [{'transaction_id': str(near['_id']), 'due_date': '2026-11-01'}]
        captured = self.search(q='captura')
        self.assertEqual([item['provisional_id'] for item in captured['items']], ['cap-sintetica'])
        self.assertEqual(captured['items'][0]['source_kind'], 'confirmed_capture')
        self.assertTrue(captured['items'][0]['linkable'])
        by_name = self.search(q='padaria')
        self.assertEqual([item['id'] for item in by_name['items']], [str(transfer['_id']), str(far['_id'])])
        bakery = next(item for item in by_name['items'] if item['id'] == str(far['_id']))
        self.assertTrue(bakery['linkable'])
        self.assertEqual(bakery['source_kind'], 'transaction')
        self.assertTrue(bakery['source_token'])
        blocked = next(item for item in by_name['items'] if item['id'] == str(transfer['_id']))
        self.assertFalse(blocked['linkable'])
        self.assertIn('não é uma despesa', blocked['block_reason'])
        by_amount = self.search(amount=Decimal('12.00'))
        self.assertEqual({item['id'] for item in by_amount['items']}, {str(far['_id']), str(transfer['_id'])})
        typed = self.search(q='12,00')
        self.assertEqual({item['id'] for item in typed['items']}, {str(far['_id']), str(transfer['_id'])})
        window = self.search(q='padaria', date_from=date(2026, 2, 1), date_to=date(2026, 2, 28))
        self.assertEqual(window['items'], [])
        self.assertEqual(window['total'], 0)
        linked = self.search(q='servico sintetico')
        self.assertEqual(linked['items'][0]['id'], str(near['_id']))
        self.assertFalse(linked['items'][0]['linkable'])
        self.assertIn('Já vinculado', linked['items'][0]['block_reason'])
        self.assertEqual(linked['commitment_revision'], 4)

    def test_period_and_amount_band_do_not_require_the_commitment_name(self):
        self.expense('2026-11-20', '-70.00', 'Outro lancamento')
        self.expense('2026-11-21', '-400.00', 'Outro lancamento grande')
        result = self.search(date_from=date(2026, 11, 1), date_to=date(2026, 11, 30), amount_min=Decimal('60'), amount_max=Decimal('100'))
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['descricao_original'], 'Outro lancamento')
        self.assertTrue(result['items'][0]['linkable'])

    def test_empty_query_is_refused(self):
        with self.assertRaises(HTTPException) as failure:
            self.search()
        self.assertEqual(failure.exception.status_code, 422)

    def test_imported_charge_search_ranks_the_closer_amount_without_writing(self):
        close = self.expense('2026-10-12', '-3716.63', 'FINANCIAMENTO IMOBILIARIO')
        far = self.expense('2026-10-12', '-33.28', 'MERCADO')
        before = len(self.db.transactions.docs)
        result = statement_lookup_page('Financiamento', date(2026, 10, 11), Decimal('3716.63'), None, date(2026, 10, 1), date(2026, 10, 31), None, None, None, 40, 0, self.db, rank='relevance')
        self.assertEqual(result['items'][0]['id'], str(close['_id']))
        self.assertEqual(result['items'][1]['id'], str(far['_id']))
        self.assertTrue(result['items'][0]['linkable'])
        self.assertNotIn('requires_occurrence_selection', result['items'][0])
        self.assertEqual(len(self.db.transactions.docs), before)

    def test_missing_commitment_is_not_found(self):
        with self.assertRaises(HTTPException) as failure:
            commitment_statement_page('ausente', None, None, None, None, None, None, 40, 0, self.db)
        self.assertEqual(failure.exception.status_code, 404)


if __name__ == '__main__':
    unittest.main()
