import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson.decimal128 import Decimal128
from app.analytics import build_cost_control_summary, calculate_financial_totals

class FinancialTotalsTests(unittest.TestCase):
    def test_excludes_card_invoice_payment_and_own_transfer(self):
        txs=[
            {"valor":Decimal128("-100"),"fluxo":"saida","categoria":"Mercado","classification":{"migration_reason":"despesa_real"}},
            {"valor":Decimal128("10"),"fluxo":"entrada","categoria":"Mercado","classification":{"migration_reason":"estorno_abate_gasto"}},
            {"valor":Decimal128("-100"),"fluxo":"saida","classification":{"migration_reason":"pagamento_fatura"}},
            {"valor":Decimal128("-500"),"fluxo":"saida","classification":{"migration_reason":"transferencia_propria"}},
            {"valor":Decimal128("500"),"fluxo":"entrada","classification":{"migration_reason":"receita_ou_movimentacao"}},
        ]
        result=calculate_financial_totals(txs)
        self.assertEqual(result['expenses'],90)
        self.assertEqual(result['income'],500)
        self.assertEqual(result['net_flow'],410)
        self.assertEqual(result['categories']['Mercado'],90)

    def test_confirmed_mobile_transaction_enters_totals_once(self):
        result=calculate_financial_totals([], [{"valor":Decimal128("-27.50"),"categoria_sugerida":"Alimentação"}])
        self.assertEqual(result['expenses'],27.50)
        self.assertEqual(result['categories']['Alimentação'],27.50)

    def test_confirmed_own_transfer_is_neutral_in_both_directions(self):
        captures=[
            {"valor":Decimal128("-200.00"),"natureza":"TRANSFERENCIA","categoria_sugerida":"Outros"},
            {"valor":Decimal128("200.00"),"natureza":"TRANSFERENCIA","categoria_sugerida":"Outros"},
        ]
        result=calculate_financial_totals([],captures)
        self.assertEqual(result['expenses'],0)
        self.assertEqual(result['income'],0)
        self.assertEqual(result['categories'],{})

    def test_legacy_transfer_classification_overrides_old_spending_flag(self):
        result=calculate_financial_totals([{"valor":Decimal128("-200.00"),"natureza":"TRANSFERENCIA","classification":{"migration_reason":"despesa_real"}}])
        self.assertEqual(result['expenses'],0)

    def test_cost_control_summary_mtd_vs_same_period_previous_month_and_insights(self):
        txs=[
            # September 2026: 400 in first 20 days + 200 on day 28 = 600 full month
            {"_id":"s1","data_evento":datetime(2026,9,5,15,tzinfo=timezone.utc),"valor":Decimal128("-250.00"),"fluxo":"saida","categoria":"Mercado","classification":{"migration_reason":"despesa_real"}},
            {"_id":"s2","data_evento":datetime(2026,9,14,15,tzinfo=timezone.utc),"valor":Decimal128("-150.00"),"fluxo":"saida","categoria":"Restaurantes","classification":{"migration_reason":"despesa_real"}},
            {"_id":"s3","data_evento":datetime(2026,9,28,15,tzinfo=timezone.utc),"valor":Decimal128("-200.00"),"fluxo":"saida","categoria":"Mercado","classification":{"migration_reason":"despesa_real"}},
            # October 2026 up to day 20: 180 Mercado + 140 Restaurantes = 320 (-20% vs Sept MTD 400)
            {"_id":"o1","data_evento":datetime(2026,10,4,15,tzinfo=timezone.utc),"valor":Decimal128("-180.00"),"fluxo":"saida","categoria":"Mercado","classification":{"migration_reason":"despesa_real"}},
            {"_id":"o2","data_evento":datetime(2026,10,12,15,tzinfo=timezone.utc),"valor":Decimal128("-140.00"),"fluxo":"saida","categoria":"Restaurantes","classification":{"migration_reason":"despesa_real"}},
            {"_id":"o3","data_evento":datetime(2026,10,5,15,tzinfo=timezone.utc),"valor":Decimal128("3000.00"),"fluxo":"entrada","classification":{"migration_reason":"receita_ou_movimentacao"}},
        ]
        summary=build_cost_control_summary(
            txs,
            anchor_date=date(2026,10,20),
            period="month",
            period_income=Decimal("3000.00"),
            period_expenses=Decimal("320.00"),
            previous_period_expenses=Decimal("600.00"),
            recurring_total=Decimal("100.00"),
        )
        self.assertEqual(summary["status"],"controlling")
        self.assertEqual(summary["mtd"]["current_expenses"],320.0)
        self.assertEqual(summary["mtd"]["previous_mtd_expenses"],400.0)
        self.assertEqual(summary["mtd"]["previous_full_expenses"],600.0)
        self.assertEqual(summary["mtd"]["delta_amount"],-80.0)
        self.assertEqual(summary["mtd"]["delta_pct"],-20.0)
        self.assertEqual(len(summary["mtd"]["buckets"]),5)
        self.assertEqual(summary["mtd"]["buckets"][0]["current_expenses"],180.0)
        self.assertEqual(summary["mtd"]["buckets"][0]["previous_mtd_expenses"],250.0)
        self.assertEqual(summary["mtd"]["buckets"][1]["current_cumulative"],320.0)
        self.assertEqual(summary["mtd"]["buckets"][1]["previous_mtd_cumulative"],400.0)
        self.assertIsNone(summary["mtd"]["buckets"][3]["current_cumulative"])
        self.assertEqual(summary["biggest_saving"]["category"],"Mercado")
        self.assertEqual(summary["biggest_saving"]["delta_amount"],-70.0)
        self.assertTrue(any(item["id"]=="burn_projection" for item in summary["insights"]))
        self.assertTrue(any(item["id"]=="top_saving_category" for item in summary["insights"]))

if __name__=='__main__':unittest.main()
