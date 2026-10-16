import sys,unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).parents[1]))
from pydantic import ValidationError
from app.planning import ScenarioInput,LiquidityInput,simulate,plan_liquidity,purchase_flows

class PlanningTests(unittest.TestCase):
    def scenario(self,**changes):
        return ScenarioInput.model_validate({'start':'2026-01-31','first_payment':'2026-01-31','opening_balance':'100.00','purchase':'10.00','installments':3,**changes})

    def test_installments_preserve_cents_and_anchor(self):
        flows=purchase_flows(Decimal('10.00'),3,date(2026,1,31))
        self.assertEqual([f['date'] for f in flows],[date(2026,1,31),date(2026,2,28),date(2026,3,31)])
        self.assertEqual([f['amount'] for f in flows],[Decimal('-3.34'),Decimal('-3.33'),Decimal('-3.33')])

    def test_scenario_does_not_lose_outside_horizon_debt(self):
        result=simulate(self.scenario(days=1));buy=result['scenarios'][1]
        self.assertEqual(buy['closing_balance'],'96.66');self.assertEqual(buy['outside_horizon_total'],'6.66')
        self.assertEqual(result['scenarios'][0]['closing_balance'],'100.00')

    def test_unknown_balance_blocks_projection(self):
        self.assertEqual(simulate(self.scenario(opening_balance=None))['status'],'insufficient_data')

    def test_duplicate_source_and_past_flow_rejected(self):
        flow={'id':'one','source_ref':'same','date':'2026-02-02','amount':'-1.00','label':'Conta'}
        with self.assertRaises(ValidationError):self.scenario(flows=[flow,{**flow,'id':'two'}])
        with self.assertRaises(ValidationError):self.scenario(flows=[{**flow,'date':'2026-01-01'}])

    def test_precision_and_reduced_price_constraints(self):
        for changes in ({'purchase':'1.001'},{'purchase':'NaN'},{'purchase':'-1'},{'reduced_purchase':'11.00'},{'days':91}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):self.scenario(**changes)

    def liquidity(self,**changes):
        return LiquidityInput.model_validate({'start':'2026-09-29','days':3,'accounts':[{'key':'itau','balance':'100','reserve':'20'},{'key':'santander','balance':'0','reserve':'30'}],**changes})

    def test_liquidity_preserves_future_donor_obligation(self):
        flow={'id':'one','date':'2026-09-30','amount':'-70','label':'Conta','account_key':'itau'}
        result=plan_liquidity(self.liquidity(flows=[flow]))
        self.assertEqual(result['proposals'][0]['amount'],'10.00')
        self.assertEqual(result['closing_balances']['itau'],'20.00')
        self.assertTrue(result['uncovered']);self.assertEqual(result['execution'],'none')

    def test_future_assumed_income_cannot_finance_donation(self):
        flows=[{'id':'income','date':'2026-09-30','amount':'500','label':'Talvez','account_key':'itau'}, {'id':'bill','date':'2026-10-01','amount':'-100','label':'Conta','account_key':'itau'}]
        result=plan_liquidity(self.liquidity(flows=flows))
        self.assertFalse(any(p['date']=='2026-09-29' for p in result['proposals']))

    def test_missing_balance_never_becomes_zero(self):
        result=plan_liquidity(self.liquidity(accounts=[{'key':'itau','balance':None}]))
        self.assertEqual(result['closing_balances'],{});self.assertTrue(result['gaps'])

    def test_transfer_daily_limit_and_total_conservation(self):
        result=plan_liquidity(self.liquidity(days=1,accounts=[{'key':'a','balance':'100','transfer_limit':'5'},{'key':'b','balance':'0','reserve':'30'}]))
        self.assertEqual(result['proposals'][0]['amount'],'5.00')
        self.assertEqual(sum(Decimal(v) for v in result['closing_balances'].values()),100)

    def test_account_assignment_is_required(self):
        with self.assertRaises(ValidationError):self.liquidity(flows=[{'id':'one','date':'2026-09-29','amount':'-1','label':'Conta'}])
