"""Adversarial probes of the shipped domain. Synthetic fixtures only."""
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from app.analytics import calculate_financial_totals
from app.commitments import COMMITMENT_CUTOFF, automatic_suggestion, presentation_state, schedule, stored_document, CommitmentIn
from app.economic_policy import amount, effects, eligible_payment
from app.financial_time import civil_date, date_filter, local_date
from app.dashboard_window import PROJECTION
from app.main import _dashboard
from test_filter_consistency import included
from app.matching import automatic_eligible
from app.obligation_policy import OPEN_OBLIGATION_STATUSES, bill_status
from test_filter_consistency import FilterDB, listing, put


class AdversarialAuditTests(unittest.TestCase):
    def test_money_sign_refund_transfer_and_card_bill_stay_apart(self):
        purchase = {'valor': Decimal128('-100.00'), 'natureza': 'COMPRA', 'fluxo': 'saida', 'classification': {'migration_reason': 'despesa_real'}}
        refund = {'valor': Decimal128('15.50'), 'natureza': 'ESTORNO', 'fluxo': 'entrada', 'classification': {'migration_reason': 'estorno'}}
        transfer = {'valor': Decimal128('-80.00'), 'natureza': 'TRANSFERENCIA', 'fluxo': 'saida', 'classification': {'migration_reason': 'transferencia_propria'}}
        bill = {'valor': Decimal128('-100.00'), 'natureza': 'PAGAMENTO', 'fluxo': 'saida', 'classification': {'migration_reason': 'pagamento_fatura'}}
        totals = calculate_financial_totals([purchase, refund, transfer, bill])
        self.assertEqual(totals['expenses'], Decimal('84.50'))
        self.assertEqual(totals['income'], Decimal('0'))
        self.assertEqual(effects(transfer)['role'], 'neutral')
        self.assertEqual(effects(bill)['role'], 'neutral')
        self.assertFalse(eligible_payment({**purchase, 'parcelamento_purchase': True}))
        with self.assertRaises(ValueError):
            amount(Decimal('Infinity'))

    def test_civil_calendar_keeps_brasilia_and_an_exclusive_end(self):
        instant = datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(local_date(instant), date(2026, 9, 30))
        self.assertEqual(civil_date('2026-10-10'), date(2026, 10, 10))
        window = date_filter(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(local_date(window['$lt'] - timedelta(microseconds=1)), date(2026, 10, 31))
        self.assertLess(window['$gte'], window['$lt'])

    def test_fixed_cutoff_schedule_and_suggestion_window(self):
        self.assertEqual(COMMITMENT_CUTOFF, date(2026, 10, 1))
        as_of = date(2026, 12, 15)
        self.assertEqual(presentation_state(date(2026, 9, 30), False, as_of), 'Pago (assumido)')
        self.assertEqual(presentation_state(date(2026, 10, 10), False, as_of), 'Vencido · sem vínculo')
        self.assertEqual(presentation_state(date(2026, 11, 10), False, date(2026, 10, 15)), 'Previsto')
        self.assertEqual(presentation_state(date(2026, 9, 30), True, as_of), 'Pago vinculado')
        item = stored_document(CommitmentIn(kind='obrigacao', name='Tributo', purchase_date=date(2026, 10, 1),
            next_due_date=date(2026, 10, 10), cash_due_date=date(2026, 10, 10), obligation_plan='parcelado',
            amount=Decimal('100'), frequency='mensal', installment_count=3, next_installment_number=1,
            payment_method='pix', renewal_status='nao_aplicavel'))
        self.assertEqual([row['amount'] for row in schedule(item)], [33.33, 33.33, 33.34])
        due = date(2026, 10, 10)
        september = date(2026, 9, 10)
        self.assertFalse(automatic_suggestion(september, due, related=True, amount=Decimal('-50'), expected=Decimal('50')))
        self.assertTrue(automatic_suggestion(date(2026, 10, 8), due, related=False, amount=Decimal('-50'), expected=Decimal('50')))

    def test_open_card_bill_stays_in_planning_until_the_balance_is_zero(self):
        self.assertEqual(OPEN_OBLIGATION_STATUSES, ('futura', 'aberta', 'vencida', 'parcialmente_paga'))
        self.assertEqual(bill_status(Decimal('120'), Decimal('0'), date(2026, 10, 1), date(2026, 10, 15)), 'vencida')
        self.assertEqual(bill_status(Decimal('120'), Decimal('20'), date(2026, 10, 20), date(2026, 10, 15)), 'parcialmente_paga')
        self.assertEqual(bill_status(Decimal('0'), Decimal('120'), date(2026, 10, 1), date(2026, 10, 15)), 'paga')

    def test_similar_purchases_do_not_merge_without_a_bank_reference(self):
        evidence = {'source_provider': 'mobile_manual', 'source_type': 'manual', 'parsed_payload': {'amount': '-20.00', 'direction': 'expense', 'merchant_raw': 'Padaria'}}
        candidate = {'valor': Decimal128('-20.00'), 'estabelecimento_original': 'Padaria', 'fluxo': 'saida', 'tipo': 'compra'}
        self.assertFalse(automatic_eligible(evidence, candidate))

    def test_dashboard_does_not_count_a_promoted_capture_twice(self):
        db = FilterDB()
        ledger = put(db, day=date(2026, 10, 5), amount='-10', category='Alimentação', natureza='COMPRA')
        put(db, True, day=date(2026, 10, 5), amount='-10', category='Alimentação', natureza='COMPRA', linked_transaction_id=str(ledger['_id']))
        lone = put(db, True, day=date(2026, 10, 6), amount='-4', category='Transporte', natureza='COMPRA')
        dashboard = _dashboard('month', date(2026, 10, 1), date(2026, 10, 31), db, snapshot=(
            [included(row, PROJECTION) for row in db.transactions.docs],
            [included(row, PROJECTION) for row in db.provisional_transactions.docs]))
        self.assertEqual(dashboard['expenses'], 14)
        self.assertEqual(sum(row['expenses'] for row in dashboard['monthly_series']), 14)
        drill = listing(db, category='Alimentação', period_basis='economic', spending_only=True)
        self.assertEqual(drill['spending_total'], 10)
        self.assertNotIn(str(lone['_id']), [row.get('id') for row in drill['items']])
        self.assertEqual(len(db.transactions.docs), 1)
        self.assertEqual(len(db.provisional_transactions.docs), 2)


if __name__ == '__main__':
    unittest.main()
