import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson.decimal128 import Decimal128
from app.analytics import calculate_financial_totals

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

if __name__=='__main__':unittest.main()
