import copy
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.cards import (BillEdit, CardEdit, VersionIn, catalog, bills, edit_card, edit_bill,
                       delete_card, restore_card, delete_bill, restore_bill)
from app.financial_time import day_start
from app.account_profiles import account_profiles
from test_subcategories import CatalogDB, CatalogCollection
from test_architecture_failures import rollback_fake, FailsOnce


class SortedCursor(list):
    def sort(self, key, direction):
        return SortedCursor(sorted(self, key=lambda row: row.get(key), reverse=direction < 0))


class CardCollection(CatalogCollection):
    def find(self, query, projection=None):
        return SortedCursor(super().find(query, projection))


class CardDB(CatalogDB):
    def __missing__(self, key):
        self[key] = CardCollection()
        return self[key]


class CardTests(unittest.TestCase):
    def setUp(self):
        self.db = CardDB()
        self.card = ObjectId()
        self.other = ObjectId()
        self.bill = ObjectId()
        self.obligation = ObjectId()
        for oid, name in [(self.card, 'Cartão A'), (self.other, 'Cartão B')]:
            self.db.accounts.insert_one({'_id': oid, 'tipo': 'cartao_credito', 'instituicao': name})
        self.db.credit_card_bills.insert_one({'_id': self.bill, 'card_account_id': self.card,
            'competencia': '2026-10', 'data_vencimento': day_start(date(2026, 10, 15)),
            'valor_total': Decimal128('100.00'), 'valor_pago': Decimal128('20.00'),
            'saldo_aberto': Decimal128('80.00'), 'novos_lancamentos': Decimal128('95.00'),
            'encargos': Decimal128('5.00'), 'status': 'parcialmente_paga',
            'items': [{'transaction_id': ObjectId(), 'valor': Decimal128('95.00'), 'tipo': 'compra'}]})
        self.db.obligations.insert_one({'_id': self.obligation, 'tipo': 'fatura_cartao', 'origem_id': self.bill,
            'competencia': '2026-10', 'data_vencimento': day_start(date(2026, 10, 15)),
            'valor_original': Decimal128('100.00'), 'valor_pago': Decimal128('20.00'),
            'saldo_aberto': Decimal128('80.00'), 'principal': Decimal128('95.00'), 'status': 'parcialmente_paga'})
        self.db.transactions.insert_one({'_id': ObjectId(), 'bill_id': self.bill, 'valor': Decimal128('-95.00')})
        self.db.reconciliations.insert_one({'_id': ObjectId(), 'obligation_id': self.obligation, 'valor': Decimal128('20.00')})

    def bill_item(self):
        return bills(str(self.card), True, self.db)['items'][0]

    def edit(self, **values):
        body = dict(expected_token=self.bill_item()['token'], competencia='2026-10',
                    data_vencimento='2026-10-20', valor_total='125.99', observacoes='Correção de teste')
        body.update(values)
        return edit_bill(str(self.bill), BillEdit(**body), self.db)['item']

    def test_selection_isolates_cards_and_counts(self):
        self.assertEqual(len(bills(str(self.other), False, self.db)['items']), 0)
        self.assertEqual(len(bills(str(self.card), False, self.db)['items']), 1)
        self.assertEqual(sorted(item['bill_count'] for item in catalog(False, self.db)['items']), [0, 1])

    def test_card_edit_keeps_imported_identity_and_source(self):
        before = copy.deepcopy(self.db.accounts.docs)
        item = catalog(False, self.db)['items'][0]
        result = edit_card(item['id'], CardEdit(expected_token=item['token'], instituicao='Banco A', apelido='Meu cartão', cartao_final='0123'), self.db)['item']
        self.assertEqual(result['cartao_final'], '0123')
        self.assertEqual(self.db.accounts.docs, before)
        with self.assertRaises(HTTPException) as failure:
            edit_card(item['id'], CardEdit(expected_token=item['token'], instituicao='Outra instituição'), self.db)
        self.assertEqual(failure.exception.status_code, 409)
        # Card/bill metadata must never leak into the bank account catalog.
        self.assertEqual(len(account_profiles(self.db)), 6)

    def test_card_settings_roundtrip_clear_and_preserve_older_clients(self):
        before = copy.deepcopy((self.db.accounts.docs, self.db.credit_card_bills.docs,
                                self.db.obligations.docs, self.db.transactions.docs))
        original = next(row for row in catalog(False, self.db)['items'] if row['id'] == str(self.card))
        result = edit_card(str(self.card), CardEdit(expected_token=original['token'],
            instituicao='Banco A', validade='2030-12', dia_vencimento=31,
            dia_fechamento=3, melhor_dia_compra=4, observacoes='Conferido no banco'), self.db)['item']
        self.assertEqual(result['validade'], '2030-12')
        self.assertEqual(result['dia_vencimento'], 31)
        legacy = edit_card(str(self.card), CardEdit(expected_token=result['token'],
            instituicao='Banco A', apelido='Novo nome'), self.db)['item']
        self.assertEqual(legacy['validade'], '2030-12')
        self.assertEqual(legacy['melhor_dia_compra'], 4)
        cleared = edit_card(str(self.card), CardEdit(expected_token=legacy['token'],
            instituicao='Banco A', validade=None, dia_vencimento=None,
            dia_fechamento=None, melhor_dia_compra=None, observacoes=None), self.db)['item']
        self.assertIsNone(cleared['validade'])
        self.assertIsNone(cleared['melhor_dia_compra'])
        self.assertEqual((self.db.accounts.docs, self.db.credit_card_bills.docs,
                          self.db.obligations.docs, self.db.transactions.docs), before)
        self.assertEqual(bills(str(self.card), False, self.db)['card']['validade'], None)

    def test_card_settings_reject_invalid_dates_and_days(self):
        for values in [{'validade':'12/2030'}, {'validade':'2030-13'}, {'validade':'0000-12'},
                       {'dia_vencimento':0}, {'dia_fechamento':32}, {'melhor_dia_compra':1.5},
                       {'dia_vencimento':True}]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                CardEdit(expected_token='a'*64, instituicao='Banco A', **values)

    def test_invoice_correction_syncs_obligation_preserves_payment_and_purchases(self):
        originals = copy.deepcopy((self.db.transactions.docs, self.db.reconciliations.docs))
        items = copy.deepcopy(self.db.credit_card_bills.docs[0]['items'])
        updated = self.edit()
        self.assertEqual(updated['valor_total'], '125.99')
        self.assertEqual(updated['saldo_aberto'], '105.99')
        self.assertEqual(updated['valor_pago'], '20.00')
        obligation = self.db.obligations.docs[0]
        self.assertEqual(str(obligation['valor_original']), '125.99')
        self.assertEqual(str(obligation['principal']), '120.99')
        self.assertEqual(obligation['data_vencimento'], day_start(date(2026, 10, 20)))
        self.assertEqual((self.db.transactions.docs, self.db.reconciliations.docs), originals)
        self.assertEqual(self.db.credit_card_bills.docs[0]['items'], items)

    def test_recoverable_deletion_keeps_debt_and_links(self):
        before = copy.deepcopy((self.db.credit_card_bills.docs, self.db.obligations.docs, self.db.transactions.docs, self.db.reconciliations.docs))
        delete_bill(str(self.bill), VersionIn(expected_token=self.bill_item()['token']), self.db)
        self.assertEqual(bills(str(self.card), False, self.db)['items'], [])
        restore_bill(str(self.bill), VersionIn(expected_token=self.bill_item()['token']), self.db)
        card = next(row for row in catalog(False, self.db)['items'] if row['id'] == str(self.card))
        delete_card(str(self.card), VersionIn(expected_token=card['token']), self.db)
        self.assertEqual(len(catalog(False, self.db)['items']), 1)
        deleted = next(row for row in catalog(True, self.db)['items'] if row['id'] == str(self.card))
        restore_card(str(self.card), VersionIn(expected_token=deleted['token']), self.db)
        self.assertEqual(len(catalog(False, self.db)['items']), 2)
        self.assertEqual((self.db.credit_card_bills.docs, self.db.obligations.docs, self.db.transactions.docs, self.db.reconciliations.docs), before)

    def test_total_below_registered_payments_rejected(self):
        with self.assertRaises(HTTPException) as failure:
            self.edit(valor_total='19.99')
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(str(self.db.credit_card_bills.docs[0]['valor_total']), '100.00')

    def test_paid_state_never_rewrites_payments(self):
        updated = self.edit(valor_total='20.00')
        self.assertEqual(updated['status'], 'paga')
        self.assertEqual(updated['saldo_aberto'], '0.00')
        self.assertEqual(str(self.db.obligations.docs[0]['valor_pago']), '20.00')

    def test_conflict_after_payment_requires_reopen(self):
        old = self.bill_item()['token']
        self.db.obligations.docs[0]['valor_pago'] = Decimal128('30.00')
        with self.assertRaises(HTTPException) as failure:
            self.edit(expected_token=old)
        self.assertEqual(failure.exception.status_code, 409)

    def test_divergent_or_missing_obligation_rejected(self):
        self.db.obligations.docs[0]['valor_original'] = Decimal128('99.00')
        with self.assertRaises(HTTPException): self.edit()
        self.db.obligations.docs.clear()
        with self.assertRaises(HTTPException): self.edit()

    def test_deleted_card_preserves_existing_commitment_but_rejects_new_links(self):
        from app.main import validate_commitment_refs
        from app.commitments import CommitmentIn
        card = next(row for row in catalog(False, self.db)['items'] if row['id'] == str(self.card))
        delete_card(str(self.card), VersionIn(expected_token=card['token']), self.db)
        body=CommitmentIn(kind='assinatura',name='Existing service',purchase_date='2026-09-01',
            next_due_date='2026-11-01',amount='10.00',payment_method='cartao',card_id=str(self.card))
        validate_commitment_refs(body,self.db,{'card_id':str(self.card)})
        with self.assertRaises(HTTPException):validate_commitment_refs(body,self.db)
        with self.assertRaises(HTTPException):validate_commitment_refs(body,self.db,{'card_id':str(self.other)})

    def test_deleted_parent_cannot_be_edited(self):
        card = next(row for row in catalog(False, self.db)['items'] if row['id'] == str(self.card))
        delete_card(str(self.card), VersionIn(expected_token=card['token']), self.db)
        with self.assertRaises(HTTPException): self.edit()

    def test_failure_rolls_back_invoice_and_debt(self):
        original_bill = copy.deepcopy(self.db.credit_card_bills.docs)
        original_obligation = copy.deepcopy(self.db.obligations.docs)
        self.db['audit_log'] = FailsOnce()
        with patch('app.cards.mongo_transaction', lambda db: rollback_fake(db)):
            with self.assertRaises(RuntimeError): self.edit()
        self.assertEqual(self.db.credit_card_bills.docs, original_bill)
        self.assertEqual(self.db.obligations.docs, original_obligation)

    def test_duplicate_month_and_total_below_charges_rejected(self):
        duplicate = copy.deepcopy(self.db.credit_card_bills.docs[0])
        duplicate.update(_id=ObjectId(), competencia='2026-11')
        self.db.credit_card_bills.insert_one(duplicate)
        with self.assertRaises(HTTPException): self.edit(competencia='2026-11')
        self.db.credit_card_bills.docs[0]['valor_pago'] = Decimal128('0')
        self.db.obligations.docs[0]['valor_pago'] = Decimal128('0')
        with self.assertRaises(HTTPException): self.edit(valor_total='4.99')

    def test_invalid_cents_dates_and_unknown_fields(self):
        for value in ['NaN', 'Infinity', '1.001', '-1']:
            with self.assertRaises(ValidationError):
                BillEdit(expected_token='a'*64, competencia='2026-10', data_vencimento='2026-10-15', valor_total=value)
        with self.assertRaises(ValidationError):
            BillEdit(expected_token='a'*64, competencia='2026-10', data_vencimento='2026-02-30', valor_total='1')


if __name__ == '__main__': unittest.main()
