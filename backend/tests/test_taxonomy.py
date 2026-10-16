import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).parents[1]))
from app.taxonomy import infer_classification


class TaxonomyTests(unittest.TestCase):
    def test_iof_international_is_atomic_tax_and_financial_cost_center(self):
        self.assertEqual(infer_classification({"descricao_original":"IOF COMPRA INTERNACIONAL USD"}),{
            "categoria":"Despesas Financeiras",
            "subcategoria":"IOF (Internacional / Câmbio)",
            "natureza":"IMPOSTO_TRIBUTO",
        })

    def test_unknown_iof_does_not_invent_origin(self):
        self.assertEqual(infer_classification({"descricao_original":"COBRANCA IOF"})["subcategoria"],"IOF (Não especificado)")

    def test_housing_name_is_preserved_for_iptu(self):
        self.assertEqual(infer_classification({"descricao_original":"PARCELA IPTU 03/10"}),{
            "categoria":"Habitação","subcategoria":"IPTU","natureza":"IMPOSTO_TRIBUTO",
        })

    def test_pix_is_not_assumed_to_be_neutral_transfer(self):
        self.assertEqual(infer_classification({"tipo":"pix","descricao_original":"PIX PADARIA"}),{})

    def test_invoice_payment_is_not_forced_into_six_natures(self):
        self.assertEqual(infer_classification({"tipo":"pagamento","descricao_original":"PAGAMENTO FATURA"}),{})


if __name__=="__main__": unittest.main()
