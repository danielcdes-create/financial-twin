import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import require_session
from app.main import app
from app.purchase_check import PurchaseCheckInput, PurchaseOfferExtraction, assess_purchase


class PurchaseCheckTests(unittest.TestCase):
    def body(self, **changes):
        return PurchaseCheckInput.model_validate({
            'start': '2026-01-31', 'first_payment': '2026-01-31',
            'price': '300.00', 'installments': 3, 'opening_balance': '1000.00',
            **changes,
        })

    def test_goal_delay_and_work_hours_use_explicit_assumptions(self):
        result = assess_purchase(self.body(
            monthly_net_income='3000.00', monthly_work_hours='160',
            goal_target='1000.00', goal_saved='0', goal_monthly_contribution='500.00',
        ))
        self.assertEqual(result['work_hours'], '16.0')
        self.assertEqual(result['goal']['baseline_date'], '2026-03-31')
        self.assertEqual(result['goal']['purchase_date'], '2026-04-30')
        self.assertEqual(result['goal']['delay_days'], 30)
        self.assertEqual(result['installment_amounts'], ['100.00'] * 3)
        self.assertEqual(result['cash_scenarios']['scenarios'][1]['closing_balance'], '700.00')
        self.assertEqual(result['execution'], 'none')

    def test_missing_balance_preserves_gap_but_keeps_other_analysis(self):
        result = assess_purchase(self.body(opening_balance=None, monthly_net_income='3000', monthly_work_hours='160'))
        self.assertEqual(result['work_hours'], '16.0')
        self.assertEqual(result['cash_scenarios']['status'], 'insufficient_data')
        self.assertTrue(result['gaps'])

    def test_purchase_today_can_delay_an_already_funded_goal(self):
        result=assess_purchase(self.body(goal_target='1000',goal_saved='1000',goal_monthly_contribution='500'))
        self.assertEqual(result['goal']['baseline_date'],'2026-01-31')
        self.assertEqual(result['goal']['purchase_date'],'2026-02-28')
        self.assertEqual(result['goal']['delay_days'],28)

    def test_later_installment_spends_an_already_funded_goal(self):
        result=assess_purchase(self.body(
            first_payment='2026-02-28', price='300.00', installments=1,
            goal_target='1000', goal_saved='1000', goal_monthly_contribution='100',
        ))
        self.assertEqual(result['goal']['baseline_date'],'2026-01-31')
        self.assertEqual(result['goal']['purchase_date'],'2026-04-30')
        self.assertEqual(result['goal']['delay_days'],89)

    def test_bad_money_and_duplicate_flows_are_rejected(self):
        with self.assertRaises(ValidationError): self.body(price='10.001')
        flow = {'id':'a','source_ref':'same','date':'2026-02-01','amount':'-5','label':'Compromisso'}
        with self.assertRaises(ValidationError): self.body(flows=[flow, {**flow, 'id':'b'}])

    def test_scan_is_read_only_and_requires_real_image_signature(self):
        app.dependency_overrides[require_session] = lambda: True
        try:
            client = TestClient(app)
            self.assertEqual(client.post('/v1/insights/purchase-scan', files={'file':('x.png',b'not an image','image/png')}).status_code, 415)
            offer = PurchaseOfferExtraction(product_label='Fone', total_price='299.90', installment_count=3)
            with patch('app.insights.XAIVisionProvider') as provider:
                provider.return_value.extract_purchase_offer.return_value = offer
                response = client.post('/v1/insights/purchase-scan', files={'file':('x.png',b'\x89PNG\r\n\x1a\nabc','image/png')})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['total_price'], '299.90')
            self.assertFalse(response.json()['persisted'])
            self.assertTrue(response.json()['needs_review'])
        finally:
            app.dependency_overrides.pop(require_session, None)


if __name__ == '__main__': unittest.main()
