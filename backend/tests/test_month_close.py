import copy
import re
import sys
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from pydantic import ValidationError
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.month_close import (Version, ItemUpdate, BalancesUpdate, CustomItem, bounds,
    get_checklist, update_item, update_balances, add_custom, close_month, reopen_month)
from app.financial_time import day_start
from app.account_profiles import account_profiles
from test_cards import CardDB, CardCollection
from test_architecture_failures import rollback_fake, FailsOnce


class MonthCollection(CardCollection):
    def _matches(self, row, query):
        for key, wanted in query.items():
            actual = row.get(key)
            if isinstance(wanted, dict):
                for operator, expected in wanted.items():
                    if operator == '$in' and actual not in expected: return False
                    if operator == '$nin' and actual in expected: return False
                    if operator == '$ne' and actual == expected: return False
                    if operator == '$gte' and (actual is None or actual < expected): return False
                    if operator == '$lte' and (actual is None or actual > expected): return False
                    if operator == '$lt' and (actual is None or actual >= expected): return False
                    if operator == '$regex' and (not isinstance(actual, str) or not re.search(expected, actual)): return False
            elif actual != wanted:
                return False
        return True


class MonthDB(CardDB):
    def __missing__(self, key):
        self[key] = MonthCollection()
        return self[key]


class MonthCloseTests(unittest.TestCase):
    def setUp(self):
        self.db = MonthDB()
        self.bank, self.card, self.bill, self.obligation = [ObjectId() for _ in range(4)]
        self.db.accounts.insert_one({'_id': self.bank, 'instituicao': 'Itaú', 'tipo': 'conta_corrente'})
        self.db.accounts.insert_one({'_id': self.card, 'instituicao': 'Cartão de teste', 'tipo': 'cartao_credito'})
        for value, account, kind, reason in [('100.00', self.bank, 'credito', 'receita_ou_movimentacao'),
                    ('-20.00', self.bank, 'pagamento_fatura', 'pagamento_fatura'),
                    ('-50.00', self.card, 'compra', 'despesa_real')]:
            self.db.transactions.insert_one({'_id': ObjectId(), 'account_id': account, 'valor': Decimal128(value),
                'data_evento': day_start(date(2026, 9, 15)), 'tipo': kind, 'categoria': 'Teste', 'classification': {'migration_reason': reason}})
        self.purchase = self.db.transactions.docs[-1]['_id']
        self.db.credit_card_bills.insert_one({'_id': self.bill, 'card_account_id': self.card, 'competencia': '2026-09',
            'data_vencimento': day_start(date(2026, 9, 20)), 'valor_total': Decimal128('50.00'),
            'valor_pago': Decimal128('20.00'), 'saldo_aberto': Decimal128('30.00'),
            'items': [{'transaction_id': self.purchase, 'valor': Decimal128('50.00')}]})
        self.db.obligations.insert_one({'_id': self.obligation, 'tipo': 'fatura_cartao', 'origem_id': self.bill,
            'competencia': '2026-09', 'data_vencimento': day_start(date(2026, 9, 20)), 'valor_original': Decimal128('50.00'),
            'valor_pago': Decimal128('20.00'), 'saldo_aberto': Decimal128('30.00')})

    def get(self, month='2026-09'):
        return get_checklist(month, self.db)

    def item(self, item_id, month='2026-09'):
        return next(item for group in self.get(month)['groups'] for item in group['items'] if item['id'] == item_id)

    def mark(self, item_id, status='done', note='', month='2026-09', **override):
        current = self.get(month)
        fields = dict(expected_revision=current['revision'], expected_source_token=self.item(item_id, month)['source_token'], status=status, note=note)
        fields.update(override)
        return update_item(month, item_id, ItemUpdate(**fields), self.db)

    def balances(self, opening='100.00', closing='180.00', group='account:itau'):
        return update_balances('2026-09', group, BalancesUpdate(expected_revision=self.get()['revision'], opening=opening, closing=closing), self.db)

    def complete(self):
        self.balances()
        for group in self.get()['groups']:
            for item in group['items']:
                if group['kind'] == 'account' and group['id'] != 'account:itau':
                    self.mark(item['id'], 'not_applicable', 'Conta sem uso neste mês')
                else:
                    self.mark(item['id'])

    def test_read_is_pure_and_checkmarks_are_month_specific(self):
        self.assertEqual(self.get()['revision'], 0)
        self.assertEqual(self.db.account_profiles.docs, [])
        updated = self.mark('general:scope', note='Contas e cartões conferidos')
        self.assertEqual(updated['completed'], 1)
        self.assertEqual(self.get()['completed'], 1)
        self.assertEqual(self.get('2026-08')['completed'], 0)
        self.assertEqual(len(account_profiles(self.db)), 6)

    def test_bank_balance_equation_and_separate_references(self):
        before = copy.deepcopy(self.db.account_balance_snapshots.docs)
        with self.assertRaises(HTTPException): self.mark('account:itau:balances')
        self.balances(closing='179.99')
        with self.assertRaises(HTTPException): self.mark('account:itau:balances')
        self.balances()
        updated = self.mark('account:itau:balances')
        bank = next(group for group in updated['groups'] if group['id'] == 'account:itau')
        self.assertEqual(bank['balance']['flow'], '80.00')
        self.assertEqual(bank['balance']['difference'], '0.00')
        self.assertEqual(self.db.account_balance_snapshots.docs, before)

    def test_payment_not_double_counted_as_expense(self):
        current = self.get()
        self.assertEqual(current['totals'], {'income': '100.00', 'expenses': '50.00'})

    def test_justified_not_applicable_and_activity_cannot_be_skipped(self):
        with self.assertRaises(HTTPException): self.mark('account:inter:statement', 'not_applicable', 'x')
        self.mark('account:inter:statement', 'not_applicable', 'Sem uso neste mês')
        with self.assertRaises(HTTPException): self.mark('account:itau:statement', 'not_applicable', 'Sem extrato disponível')

    def test_bank_pending_captures_and_transfers_block_completion(self):
        self.db.provisional_transactions.insert_one({'_id': ObjectId(), 'provisional_id': 'pending', 'status': 'provisional',
            'data_evento': day_start(date(2026, 9, 16)), 'valor': Decimal128('-1')})
        with self.assertRaises(HTTPException): self.mark('general:capture')
        self.db.provisional_transactions.docs.clear()
        self.db.provisional_transactions.insert_one({'_id': ObjectId(), 'provisional_id': 'transfer', 'status': 'confirmed',
            'data_evento': day_start(date(2026, 9, 16)), 'valor': Decimal128('-10'),
            'transfer': {'from_account_key': 'itau', 'to_account_key': 'inter', 'reconciliation_status': 'aguardando_extratos'}})
        with self.assertRaises(HTTPException): self.mark('general:transfers')

    def test_reconciled_transfer_leg_and_capture_evidence_not_counted_twice(self):
        transaction = self.db.transactions.docs[0]
        transaction['valor'] = Decimal128('-10')
        self.db.provisional_transactions.insert_one({'_id': ObjectId(), 'status': 'confirmed',
            'data_evento': transaction['data_evento'], 'valor': Decimal128('-10'),
            'transfer': {'from_account_key': 'itau', 'to_account_key': 'inter', 'source_transaction_id': str(transaction['_id']), 'reconciliation_status': 'conciliada'}})
        current = self.get()
        bank = next(group for group in current['groups'] if group['id'] == 'account:itau')
        peer = next(group for group in current['groups'] if group['id'] == 'account:inter')
        self.assertEqual(bank['balance']['flow'], '-30.00')
        self.assertEqual(peer['balance']['flow'], '10.00')
        self.db.provisional_transactions.docs.clear()
        transaction['valor'] = Decimal128('100')
        self.db.provisional_transactions.insert_one({'_id': ObjectId(), 'status': 'confirmed',
            'data_evento': transaction['data_evento'], 'valor': transaction['valor'], 'account_profile_id': 'itau', 'categoria_sugerida': 'Teste', 'linked_transaction_id': str(transaction['_id'])})
        current = self.get()
        self.assertEqual(current['totals']['income'], '100.00')
        self.assertEqual(next(g for g in current['groups'] if g['id'] == 'account:itau')['balance']['flow'], '80.00')

    def test_different_accounts_same_bank_are_not_collapsed(self):
        second = ObjectId()
        self.db.accounts.insert_one({'_id': second, 'tipo': 'conta_corrente', 'instituicao': 'Itaú', 'conta': '000222'})
        groups = self.get()['groups']
        self.assertIn('legacy:' + str(self.bank), [group['id'] for group in groups])
        self.assertIn('legacy:' + str(second), [group['id'] for group in groups])

    def test_deleted_card_with_month_history_stays_in_scope(self):
        self.db.account_profiles.insert_one({'_id': 'card-profile:' + str(self.card), 'key': 'card-profile:' + str(self.card),
            'entity_type': 'card', 'deleted': True})
        self.assertIn('card:' + str(self.card), [group['id'] for group in self.get()['groups']])

    def test_invoice_obligation_mismatch_blocks_total_confirmation(self):
        self.db.obligations.docs[0]['saldo_aberto'] = Decimal128('29.99')
        with self.assertRaises(HTTPException): self.mark('card:' + str(self.card) + ':total')

    def test_source_change_and_concurrent_edit_require_fresh_review(self):
        self.mark('general:summary')
        previous = self.get()
        self.db.transactions.docs[0]['valor'] = Decimal128('101.00')
        self.assertTrue(self.item('general:summary')['needs_review'])
        with self.assertRaises(HTTPException) as failure:
            self.mark('general:scope', expected_source_token=next(i for g in previous['groups'] for i in g['items'] if i['id']=='general:scope')['source_token'])
        self.assertEqual(failure.exception.status_code, 409)
        with self.assertRaises(HTTPException): self.mark('general:scope', expected_revision=0)

    def test_close_reopen_and_new_source_invalidate_completed_month(self):
        self.complete()
        current = self.get()
        self.assertTrue(current['can_close'])
        closed = close_month('2026-09', Version(expected_revision=current['revision']), self.db)
        self.assertEqual(closed['status'], 'closed')
        with self.assertRaises(HTTPException): self.mark('general:scope', 'pending')
        self.db.transactions.docs[0]['valor'] = Decimal128('101')
        self.assertTrue(self.get()['source_changed'])
        self.assertEqual(self.get()['last_close']['totals']['income'], '100.00')
        reopened = reopen_month('2026-09', Version(expected_revision=self.get()['revision']), self.db)
        self.assertEqual(reopened['status'], 'open')
        self.assertTrue(self.item('general:scope')['needs_review'])

    def test_cannot_close_incomplete_or_current_month(self):
        with self.assertRaises(HTTPException): close_month('2026-09', Version(expected_revision=0), self.db)
        with patch('app.month_close.today', lambda: date(2026, 9, 15)):
            self.assertFalse(self.get()['month_ended'])

    def test_custom_requirements_are_persisted(self):
        result = add_custom('2026-09', CustomItem(expected_revision=0, request_id=uuid4(), group_id='general', title='Conferir reembolso do seguro'), self.db)
        self.assertTrue(any(i['title'] == 'Conferir reembolso do seguro' for g in result['groups'] for i in g['items']))

    def test_failed_audit_rolls_back_all_checklist_state(self):
        self.get()
        self.db['audit_log'] = FailsOnce()
        with patch('app.month_close.mongo_transaction', rollback_fake):
            with self.assertRaises(RuntimeError): self.mark('general:scope')
        self.assertEqual(self.get()['revision'], 0)
        self.assertEqual(self.get()['completed'], 0)

    def test_financial_source_documents_are_unchanged(self):
        names = ['transactions', 'provisional_transactions', 'accounts', 'credit_card_bills', 'obligations', 'reconciliations']
        originals = {name: copy.deepcopy(self.db[name].docs) for name in names}
        self.balances()
        self.mark('general:scope')
        self.assertEqual(originals, {name: self.db[name].docs for name in names})

    def test_calendar_cent_and_unknown_field_validation(self):
        self.assertEqual(bounds('2024-02')[1], date(2024, 2, 29))
        for value in ['2026-13', '../2026-09', '0000-01']:
            with self.assertRaises(HTTPException): bounds(value)
        for value in ['NaN', 'Infinity', '1.001']:
            with self.assertRaises(ValidationError): BalancesUpdate(expected_revision=0, opening=value, closing='0')


if __name__ == '__main__': unittest.main()
