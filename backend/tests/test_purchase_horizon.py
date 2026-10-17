import sys
import unittest
from contextlib import nullcontext
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import require_session
from app.insights import database
from app.main import app
from app.planning import ScenarioInput, purchase_flows
from app.purchase_check import PurchaseCheckInput, assess_purchase
from app.purchase_horizon import purchase_horizon


class PurchaseHorizonTests(unittest.TestCase):
    def body(self, **changes):
        return PurchaseCheckInput.model_validate({
            'start': '2026-09-30', 'first_payment': '2026-09-30',
            'price': '1873.14', 'installments': 12, 'opening_balance': '10000', **changes,
        })

    def test_twelve_installments_cover_thirteen_calendar_months_and_later_mortgage(self):
        result = assess_purchase(self.body(flows=[{
            'id': 'mortgage:2027-09', 'date': '2027-09-11', 'amount': '-3635.73',
            'label': 'Financiamento imobiliário',
        }]))
        self.assertEqual(result['horizon']['minimum_months'], 13)
        self.assertEqual(result['horizon']['end'], '2027-10-30')
        scenarios = result['cash_scenarios']['scenarios']
        self.assertEqual(scenarios[1]['closing_balance'], '4491.13')
        self.assertEqual(scenarios[0]['closing_balance'], '6364.27')
        self.assertEqual(result['installment_amounts'], ['156.10'] * 6 + ['156.09'] * 6)
        self.assertTrue(all(s['outside_horizon_total'] == '0.00' for s in scenarios))
        self.assertTrue(all(s['curve'][-1]['date'] == result['horizon']['end'] for s in scenarios))
        self.assertEqual(result['execution'], 'none')

    def test_informed_income_lifts_the_purchase_minimum_once_per_civil_month(self):
        from decimal import Decimal
        from app.planning import money
        start = date(2026, 9, 30)
        horizon = purchase_horizon(start, 1, start)
        end = horizon['end']
        opening = Decimal('1000.00')
        outflow = Decimal('-5000.00')
        price = Decimal('100.00')
        income = Decimal('200.00')
        months = (date.fromisoformat(end).year - start.year) * 12 + date.fromisoformat(end).month - start.month + 1
        body = dict(start='2026-09-30', first_payment='2026-09-30', price=str(price), installments=1,
                    opening_balance=str(opening), flows=[{
                        'id': 'rent:end', 'date': end, 'amount': str(outflow), 'label': 'Aluguel',
                    }])
        with_income = assess_purchase(self.body(**body, monthly_net_income=str(income)))
        buy = next(row for row in with_income['cash_scenarios']['scenarios'] if row['name'] == 'buy_now')
        expected = opening + outflow - price + income * months
        self.assertEqual(buy['minimum_balance'], money(expected))
        self.assertTrue(all(row['outside_horizon_total'] == '0.00' for row in with_income['cash_scenarios']['scenarios']))
        without = assess_purchase(self.body(**body))
        plain = next(row for row in without['cash_scenarios']['scenarios'] if row['name'] == 'buy_now')
        self.assertEqual(plain['minimum_balance'], money(opening + outflow - price))
        self.assertEqual(plain['outside_horizon_total'], '0.00')
        # The twelve-installment case without income keeps its previous close.
        legacy = assess_purchase(self.body(flows=[{
            'id': 'mortgage:2027-09', 'date': '2027-09-11', 'amount': '-3635.73',
            'label': 'Financiamento imobiliário',
        }]))
        self.assertEqual(legacy['cash_scenarios']['scenarios'][1]['closing_balance'], '4491.13')

    def test_single_payment_keeps_existing_ninety_day_minimum(self):
        h = purchase_horizon(date(2026, 9, 30), 1, date(2026, 9, 30))
        self.assertEqual(h['days'], 90)
        self.assertEqual(h['end'], '2026-12-28')

    def test_leap_year_deferred_first_payment_and_wait_have_month_after_last_payment(self):
        start, first = date(2027, 12, 31), date(2028, 1, 31)
        h = purchase_horizon(start, 12, first)
        end = date.fromisoformat(h['end'])
        for payment_start in [first, first + timedelta(days=30)]:
            last = purchase_flows(self.body().price, 12, payment_start)[-1]['date']
            from app.commitments import advance
            self.assertGreaterEqual(end, advance(last, 'mensal', 1))
        self.assertEqual(h['end'], '2029-03-01')

    def test_sixty_payments_are_all_counted_cent_exactly(self):
        result = assess_purchase(self.body(installments=60))
        self.assertEqual(result['horizon']['minimum_months'], 61)
        self.assertEqual(result['horizon']['end'], '2031-10-30')
        for scenario in result['cash_scenarios']['scenarios']:
            self.assertEqual(scenario['outside_horizon_total'], '0.00')
        self.assertEqual(result['cash_scenarios']['scenarios'][1]['closing_balance'], '8126.86')

    def test_missing_balance_still_returns_the_same_horizon(self):
        result = assess_purchase(self.body(opening_balance=None))
        self.assertEqual(result['cash_scenarios']['status'], 'insufficient_data')
        self.assertEqual(result['horizon'], result['cash_scenarios']['horizon'])

    def test_unsafe_dates_and_installment_counts_rejected_without_extending_general_simulator(self):
        for changes in [{'installments': 61}, {'first_payment': '2026-09-29'},
                        {'first_payment': '2040-01-01'}, {'first_payment': '9999-12-31'}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.body(**changes)
        with self.assertRaises(ValidationError):
            ScenarioInput.model_validate({'start': '2026-09-30', 'first_payment': '2026-09-30', 'days': 91})

    def test_context_and_assessment_share_horizon_and_leave_imports_for_manual_selection(self):
        commitments = Mock()
        commitments.find.return_value = [{
            'commitment_id': 'weekly-old', 'name': 'Semanal', 'status': 'ativo',
            'next_due_date': '2010-01-04', 'amount': '10', 'frequency': 'semanal',
            'payment_method': 'pix',
        }, {
            'commitment_id': 'card-azul', 'name': 'Mensalidade Azul', 'status': 'ativo',
            'next_due_date': '2026-10-15', 'amount': '42.50', 'frequency': 'mensal',
            'payment_method': 'cartao',
        }]
        obligations = Mock()
        obligations.find.return_value = [
            {'_id': 'jan', 'tipo': 'financiamento', 'competencia': '2027-01',
             'data_vencimento': '2027-01-11', 'saldo_aberto': '3694.57'},
            {'_id': 'outside', 'tipo': 'financiamento', 'competencia': '2027-11',
             'data_vencimento': '2027-11-11', 'saldo_aberto': '3000'},
            {'_id': 'bill', 'tipo': 'fatura_cartao', 'competencia': '2026-11',
             'data_vencimento': '2026-11-10', 'saldo_aberto': '999.00'},
        ]
        overrides = Mock(); overrides.find.return_value = []
        db = SimpleNamespace(commitments=commitments, obligations=obligations, future_schedule_overrides=overrides)
        app.dependency_overrides[require_session] = lambda: True
        app.dependency_overrides[database] = lambda: db
        try:
            with patch('app.insights.mongo_snapshot', return_value=nullcontext((db, 'test'))), \
                 patch('app.insights.account_positions', return_value=[]):
                client = TestClient(app)
                response = client.get('/v1/insights/purchase-context?start=2026-09-30&installments=12&first_payment=2026-09-30')
                self.assertEqual(response.status_code, 200)
                context = response.json()
                self.assertEqual(context['horizon'], assess_purchase(self.body())['horizon'])
                self.assertEqual([o['id'] for o in context['suggested_flows']], ['obligation:jan'])
                self.assertGreater(len(context['flows']), 50)
                self.assertTrue(all(f['id'].startswith('commitment:') for f in context['flows']))
                card_rows = [row for row in context['flows'] if row['label'] == 'Mensalidade Azul']
                self.assertGreaterEqual(len(card_rows), 2)
                self.assertTrue(all(row['amount'] == '-42.50' for row in card_rows))
                self.assertTrue(all(row['id'].startswith('commitment:card-azul:') and row['date'] for row in card_rows))
                self.assertNotIn('obligation:bill', [row['id'] for row in context['flows'] + context['suggested_flows']])
                self.assertTrue(all('Fatura' not in row['label'] for row in context['flows'] + context['suggested_flows']))
                self.assertFalse(any(row['ref'] == 'commitment:card-azul' for row in context['excluded_sources']))
                # The shared planning context still hides card commitments and still offers the invoice.
                shared = client.get('/v1/insights/planning-context?start=2026-09-30&end=2026-12-28')
                self.assertEqual(shared.status_code, 200)
                shared_body = shared.json()
                self.assertFalse(any(row['label'] == 'Mensalidade Azul' for row in shared_body['flows']))
                self.assertTrue(any(row['ref'] == 'commitment:card-azul' for row in shared_body['excluded_sources']))
                self.assertIn('obligation:bill', [row['id'] for row in shared_body['suggested_flows']])
                # Other planning workflows retain their old maximum.
                self.assertEqual(client.get('/v1/insights/planning-context?start=2026-09-30&end=2027-10-30').status_code, 422)
                for suffix in ['installments=61&first_payment=2026-09-30',
                               'installments=12&first_payment=2040-01-01',
                               'installments=12&first_payment=2026-09-29']:
                    self.assertEqual(client.get('/v1/insights/purchase-context?start=2026-09-30&' + suffix).status_code, 422)
                self.assertFalse(commitments.method_calls.count(('insert_one', (), {})))
                self.assertEqual([call[0] for call in obligations.method_calls], ['find', 'find'])
                self.assertEqual([call[0] for call in commitments.method_calls], ['find', 'find'])
        finally:
            app.dependency_overrides.pop(require_session, None)
            app.dependency_overrides.pop(database, None)


if __name__ == '__main__':
    unittest.main()
