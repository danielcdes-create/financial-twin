"""NT-ARCH-MONGO-002 category conversion on synthetic rows only."""
import copy
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.economic_policy import effects
from app.main import categories
from app.taxonomy import (
    apply_taxonomy_conversion,
    classify_pair,
    format_conversion_report,
    rejects_direct_level1_posting,
    taxonomy_catalog,
)
from test_category_catalog_consistency import CountDB


WHEN = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def row(identity, amount, category, subcategory="", **extra):
    document = {
        "_id": identity,
        "valor": Decimal128(amount),
        "data_evento": WHEN,
        "natureza": extra.pop("natureza", "COMPRA"),
        "linked_transactions": [{"transaction_id": "link-1"}],
        "classification": {"migration_reason": "despesa_real"},
    }
    if category is not None:
        document["categoria"] = category
    if subcategory is not None:
        document["subcategoria"] = subcategory
    document.update(extra)
    return document


class CategoryConversionTests(unittest.TestCase):
    def test_served_catalog_is_the_section_07_tree(self):
        catalog = taxonomy_catalog()
        groups = [node for node in catalog if node["level"] == 1]
        leaves = [node for node in catalog if node["level"] == 2]
        self.assertEqual([node["name"] for node in groups], [
            "Habitação", "Alimentação", "Transporte", "Saúde e Cuidados", "Pets",
            "Lazer e Estilo", "Compras", "Educação", "Finanças/Impostos", "Receitas",
        ])
        self.assertTrue(all(node["allow_direct_posting"] is False and node["parent"] is None for node in groups))
        self.assertTrue(all(node["level"] <= 2 for node in catalog))
        self.assertEqual(max(node["level"] for node in catalog), 2)
        by_parent = {}
        for node in leaves:
            by_parent.setdefault(node["parent"], []).append(node)
            self.assertEqual(node["level"], 2)
            self.assertTrue(node["allow_direct_posting"])
            self.assertIn("path", node)
        for parent, nodes in by_parent.items():
            codes = [tuple(node["analytics"]["default_mcc_codes"]) for node in nodes]
            self.assertEqual(len(set(codes)), 1, parent)
        for name in groups:
            if name["name"] == "Receitas":
                self.assertEqual([node["name"] for node in by_parent["Receitas"]], [
                    "Renda Principal", "Renda Extra", "Reembolsos",
                ])
                self.assertTrue(all(node["analytics"]["default_mcc_codes"] == [] for node in by_parent["Receitas"]))
            else:
                self.assertEqual(by_parent[name["name"]][-1]["name"], "Outros")
        food = {node["name"]: node["analytics"] for node in by_parent["Alimentação"]}
        self.assertEqual(food["Supermercado/Feira/Açougue"]["budget_bucket"], "NEEDS")
        self.assertEqual(food["Padaria"]["budget_bucket"], "NEEDS")
        self.assertEqual(food["Restaurantes/Bares"]["budget_bucket"], "WANTS")
        self.assertEqual(food["Delivery/Apps"]["budget_bucket"], "WANTS")
        self.assertIsNone(food["Supermercado/Feira/Açougue"]["nature"])
        natures = {node["name"]: node["analytics"]["nature"] for node in leaves}
        self.assertEqual(natures["Transporte Público"], "F/V")
        self.assertEqual(natures["Ração e Petiscos"], "F/V")
        self.assertEqual(natures["Futebol/Sócio-Torcedor"], "F/V")
        self.assertEqual(natures["Aluguel/Prestação"], "FIXED")
        self.assertEqual(natures["Combustível/Recarga"], "VARIABLE")
        transport = next(node for node in leaves if node["name"] == "Combustível/Recarga")
        self.assertEqual(transport["path"], "EXPENSE.transporte.combustivel")
        self.assertEqual(transport["analytics"]["default_mcc_codes"], ["5541", "4121", "4111", "7523", "7538"])
        self.assertEqual(transport["analytics"]["budget_bucket"], "NEEDS")
        education = next(node for node in leaves if node["name"] == "Faculdade/Pós/Cursos")
        self.assertEqual(education["analytics"]["budget_bucket"], "SAVINGS / NEEDS")
        finance = next(node for node in leaves if node["name"] == "Juros/Multas/IOF")
        self.assertEqual(finance["analytics"]["budget_bucket"], "NEEDS / SAVINGS")
        self.assertTrue(rejects_direct_level1_posting("Habitação", ""))
        self.assertFalse(rejects_direct_level1_posting("Habitação", "Condomínio"))
        self.assertFalse(rejects_direct_level1_posting("Restaurantes", ""))
        served = categories(CountDB())["taxonomy"]
        self.assertEqual(served, taxonomy_catalog())

    def test_conversion_rewrites_only_deterministic_labels_and_is_idempotent(self):
        db = CountDB()
        rent = row("rent", "-100.00", "Habitação", "Aluguel")
        fuel = row("fuel", "-40.50", "Transporte", "Combustível")
        interest = row("interest", "-5.00", "Despesas Financeiras", "Juros", natureza="TARIFA_ENCARGO")
        fee = row("fee", "-2.25", "Despesas Financeiras", "Tarifas e Anuidades", natureza="TARIFA_ENCARGO")
        parent = row("parent", "-7.00", "Habitação", "")
        blank = row("blank", "-3.00", None, None)
        labeled = row("labeled", "-1.00", "Sem categoria", "algo")
        empty = row("empty", "-1.50", "", "")
        unknown = row("unknown", "-9.00", "Zoologico", "Ingresso")
        transfer = row("transfer", "-500.00", "Transferência entre contas próprias", "", natureza="TRANSFERENCIA")
        bill = row("bill", "-80.00", "Pagamento de Fatura (Cartão)", "", tipo="pagamento_fatura")
        bill["classification"] = {"migration_reason": "pagamento_fatura"}
        originals = {
            item["_id"]: copy.deepcopy(item)
            for item in (rent, fuel, interest, fee, parent, blank, labeled, empty, unknown, transfer, bill)
        }
        for item in originals.values():
            db.transactions.insert_one(copy.deepcopy(item))
        db.provisional_transactions.insert_one({
            "_id": "capture", "categoria_sugerida": "Despesas Financeiras", "subcategoria": "Juros",
            "valor": Decimal128("-5.00"), "data_evento": WHEN, "natureza": "TARIFA_ENCARGO", "status": "confirmed",
        })
        db.commitments.insert_one({"_id": "commitment", "category": "Habitação", "valor": Decimal128("-7.00"), "natureza": "COMPRA"})
        before_money = {
            item["_id"]: (item["valor"], item["data_evento"], item["natureza"], item["linked_transactions"])
            for item in db.transactions.docs
        }

        report = apply_taxonomy_conversion(db)
        print(format_conversion_report(report))
        stored = {item["_id"]: item for item in db.transactions.docs}

        self.assertEqual(stored["rent"]["categoria"], "Habitação")
        self.assertEqual(stored["rent"]["subcategoria"], "Aluguel/Prestação")
        self.assertEqual(stored["fuel"]["subcategoria"], "Combustível/Recarga")
        self.assertEqual((stored["interest"]["categoria"], stored["interest"]["subcategoria"]), ("Finanças/Impostos", "Juros/Multas/IOF"))
        self.assertEqual(stored["fee"]["subcategoria"], "Tarifas/Anuidade")
        self.assertEqual((stored["parent"]["categoria"], stored["parent"]["subcategoria"]), ("Habitação", "Outros"))
        self.assertEqual(stored["rent"]["taxonomy_previous"], {"categoria": "Habitação", "subcategoria": "Aluguel"})
        self.assertEqual(stored["parent"]["taxonomy_previous"], {"categoria": "Habitação", "subcategoria": None})
        for identity in ("blank", "labeled", "empty", "unknown", "transfer", "bill"):
            self.assertNotIn("taxonomy_previous", stored[identity])
            self.assertEqual(stored[identity].get("categoria"), originals[identity].get("categoria"))
            self.assertEqual(stored[identity].get("subcategoria"), originals[identity].get("subcategoria"))
        self.assertEqual(classify_pair("Zoologico", "Ingresso")["status"], "unresolved")
        self.assertEqual(classify_pair("Sem categoria", "algo")["status"], "uncategorized")
        for identity, (amount, stamp, nature, links) in before_money.items():
            current = stored[identity]
            self.assertIsInstance(current["valor"], Decimal128)
            self.assertNotIsInstance(current["valor"], (float, int))
            self.assertEqual(current["valor"].to_decimal(), amount.to_decimal())
            self.assertEqual(current["data_evento"], stamp)
            self.assertEqual(current["natureza"], nature)
            self.assertEqual(current["linked_transactions"], links)

        capture = db.provisional_transactions.docs[0]
        self.assertEqual((capture["categoria_sugerida"], capture["subcategoria"]), ("Finanças/Impostos", "Juros/Multas/IOF"))
        self.assertEqual(capture["taxonomy_previous"]["categoria"], "Despesas Financeiras")
        self.assertEqual(capture["valor"].to_decimal(), Decimal("-5.00"))
        commitment = db.commitments.docs[0]
        self.assertEqual((commitment["category"], commitment["subcategoria"]), ("Habitação", "Outros"))
        self.assertNotIn("categoria", commitment)
        self.assertEqual(commitment["taxonomy_previous"], {"categoria": "Habitação", "subcategoria": None})

        tree_groups = {node["name"] for node in taxonomy_catalog() if node["level"] == 1}
        groups = {}
        subs = {}
        for item in db.transactions.docs:
            effect = effects(item)
            if effect["role"] != "expense" or item.get("categoria") not in tree_groups:
                continue
            self.assertTrue(item.get("subcategoria"))
            groups[item["categoria"]] = groups.get(item["categoria"], Decimal("0")) + effect["expenses"]
            key = (item["categoria"], item["subcategoria"])
            subs[key] = subs.get(key, Decimal("0")) + effect["expenses"]
        for category, total in groups.items():
            self.assertEqual(sum(amount for (parent, _), amount in subs.items() if parent == category), total)
        self.assertEqual(groups["Habitação"], Decimal("107.00"))
        self.assertEqual(groups["Transporte"], Decimal("40.50"))
        self.assertEqual(groups["Finanças/Impostos"], Decimal("7.25"))
        self.assertEqual(effects(stored["transfer"])["expenses"], Decimal("0"))
        self.assertEqual(effects(stored["bill"])["expenses"], Decimal("0"))
        self.assertEqual({node["name"] for node in db.categories.docs}, {
            "Habitação", "Alimentação", "Transporte", "Saúde e Cuidados", "Pets",
            "Lazer e Estilo", "Compras", "Educação", "Finanças/Impostos", "Receitas",
        })
        self.assertTrue(all(node["allow_direct_posting"] is False and node["level"] == 1 for node in db.categories.docs))

        snapshot = {
            name: copy.deepcopy(db[name].docs)
            for name in ("transactions", "provisional_transactions", "commitments", "categories", "audit_log")
        }
        again = apply_taxonomy_conversion(db)
        self.assertEqual(again["converted"], 0)
        self.assertEqual(again["category_documents_written"], 0)
        for name, docs in snapshot.items():
            self.assertEqual(db[name].docs, docs)


if __name__ == "__main__":
    unittest.main()
