import json
import re
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.financial_time import LOCAL_ZONE, day_start
from app.official_reconciler import (
    apply_reconciliation_plan,
    build_reconciliation_plan,
    format_markdown_report,
)
from app.official_statement_parsers import (
    OfficialStatementDocument,
    OfficialStatementItem,
    correlate_parsed_statements,
    parse_brl_decimal,
)
from app.popular_merchants import (
    brand_keys_for_text,
    classify_official_descriptor,
    extract_embedded_purchase_date,
    popular_alias_match,
)
from test_subcategories import CatalogCollection, CatalogDB

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TX_SCHEMA = json.loads((FIXTURES_DIR / "transactions-validator.json").read_text(encoding="utf-8"))["validator"]["$jsonSchema"]
ACC_SCHEMA = json.loads((FIXTURES_DIR / "accounts-validator.json").read_text(encoding="utf-8"))["validator"]["$jsonSchema"]


def _assert_matches_bson_schema(doc: dict, schema: dict, label: str) -> None:
    required = set(schema.get("required") or [])
    missing = required - set(doc.keys())
    if missing:
        raise AssertionError(f"{label}: missing required keys {sorted(missing)}")
    if schema.get("additionalProperties") is False:
        allowed = set((schema.get("properties") or {}).keys())
        extra = set(doc.keys()) - allowed
        if extra:
            raise AssertionError(f"{label}: forbidden additionalProperties {sorted(extra)}")
    props = schema.get("properties") or {}
    for key, val in doc.items():
        spec = props.get(key) or {}
        if "enum" in spec and val not in spec["enum"]:
            raise AssertionError(f"{label}.{key}: {val!r} not in enum {spec['enum']}")
        if "pattern" in spec and isinstance(val, str) and not re.match(spec["pattern"], val):
            raise AssertionError(f"{label}.{key}: {val!r} does not match {spec['pattern']}")


class ReconcilerCollection(CatalogCollection):
    def __init__(self, name: str = "", validator_schema: dict | None = None):
        super().__init__()
        self._name = name
        self._validator_schema = validator_schema

    def _matches(self, doc, query):
        if not query:
            return True
        for field, expected in query.items():
            if field == "$or":
                if not any(self._matches(doc, branch) for branch in expected):
                    return False
                continue
            value = doc.get(field)
            if isinstance(expected, dict):
                if "$in" in expected and value not in expected["$in"]:
                    return False
                if "$nin" in expected and value in expected["$nin"]:
                    return False
                if "$ne" in expected and value == expected["$ne"]:
                    return False
                if "$exists" in expected and (field in doc) != expected["$exists"]:
                    return False
                if "$gte" in expected and (value is None or value < expected["$gte"]):
                    return False
                if "$lt" in expected and (value is None or value >= expected["$lt"]):
                    return False
                if "$regex" in expected:
                    if not isinstance(value, str) or not re.search(expected["$regex"], value):
                        return False
            elif value != expected:
                return False
        return True

    def insert_one(self, doc):
        if self._validator_schema is not None:
            _assert_matches_bson_schema(doc, self._validator_schema, self._name)
        return super().insert_one(doc)

    def update_one(self, query, update, upsert=False):
        res = super().update_one(query, update, upsert=upsert)
        if self._validator_schema is not None:
            updated = self.find_one(query)
            if updated is not None:
                _assert_matches_bson_schema(updated, self._validator_schema, self._name)
        return res


class ReconcilerDB(CatalogDB):
    _SCHEMAS = {"transactions": TX_SCHEMA, "accounts": ACC_SCHEMA}

    def __missing__(self, key):
        self[key] = ReconcilerCollection(name=key, validator_schema=self._SCHEMAS.get(key))
        return self[key]

    def list_collections(self, filter=None):
        target = (filter or {}).get("name")
        names = [target] if target else list(self.keys())
        out = []
        for name in names:
            schema = self._SCHEMAS.get(name)
            opts = {"validator": {"$jsonSchema": schema}} if schema else {}
            out.append({"name": name, "options": opts})
        return iter(out)


def _valid_ledger_tx(
    *,
    account_id: ObjectId,
    dt: date,
    valor: str,
    descricao: str,
    categoria: str = "Alimentação",
    subcategoria: str | None = "Delivery",
    merchant_id: ObjectId | None = None,
) -> dict:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    oid = ObjectId()
    doc = {
        "_id": oid,
        "external_key": f"tx-{oid}",
        "schema_version": 2,
        "account_id": account_id,
        "data_evento": datetime.combine(dt, time(12, 0), LOCAL_ZONE).astimezone(timezone.utc),
        "competencia": dt.strftime("%Y-%m"),
        "valor": Decimal128(valor),
        "moeda": "BRL",
        "fluxo": "saida" if Decimal(valor) < 0 else "entrada",
        "tipo": "compra",
        "descricao_original": descricao,
        "categoria": categoria,
        "localidade_contexto": "nao_determinado",
        "localidade_real_validada": False,
        "origem_localidade": "nao_informada",
        "source": {
            "source_record_id": ObjectId(),
            "identificador_original": f"orig-{oid}",
            "fonte": "manual_test",
        },
        "status_conciliacao": "pendente",
        "created_at": now,
        "updated_at": now,
    }
    if subcategoria:
        doc["subcategoria"] = subcategoria
    if merchant_id:
        doc["merchant_id"] = merchant_id
    return doc


def _make_item(
    doc_key: str,
    row_index: int,
    target: str,
    dt: str,
    amount: str,
    description: str,
    *,
    purchase_date_hint: str | None = None,
    principal_amount: str | None = None,
    fee_amount: str | None = None,
    kind: str = "expense",
) -> OfficialStatementItem:
    info = classify_official_descriptor(description)
    return OfficialStatementItem(
        doc_key=doc_key,
        row_index=row_index,
        target=target,
        date=dt,
        purchase_date_hint=purchase_date_hint or dt,
        statement_month=dt[:7],
        amount=amount,
        kind=kind,
        description=description,
        popular_vendor=info.popular_vendor,
        platform=info.platform,
        categoria=info.categoria,
        subcategoria=info.subcategoria,
        natureza="COMPRA" if kind == "expense" else "TRANSFERENCIA",
        tipo="compra" if kind == "expense" else "pagamento_fatura",
        principal_amount=principal_amount,
        fee_amount=fee_amount,
        brand_aliases=list(info.brand_aliases),
        context_hints=list(info.context_hints),
    )


class OfficialReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.db = ReconcilerDB()
        now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        self.card_id = ObjectId()
        self.checking_id = ObjectId()
        self.db.accounts.insert_one(
            {
                "_id": self.card_id,
                "external_key": "card-itau-1111",
                "schema_version": 2,
                "instituicao": "Itaú",
                "tipo": "cartao_credito",
                "cartao_final": "1111",
                "titular": "Titular Teste",
                "moeda": "BRL",
                "status": "ativo",
                "created_at": now,
                "updated_at": now,
            }
        )
        self.db.accounts.insert_one(
            {
                "_id": self.checking_id,
                "external_key": "cc-itau-main",
                "schema_version": 2,
                "instituicao": "Itaú",
                "tipo": "conta_corrente",
                "titular": "Titular Teste",
                "moeda": "BRL",
                "status": "ativo",
                "created_at": now,
                "updated_at": now,
            }
        )

    def test_popular_merchant_aliases_and_embedded_dates(self):
        self.assertTrue(popular_alias_match("IFD *ZAMP SABEL", "Burger King"))
        self.assertTrue(popular_alias_match("CAPPTA *LANCHONETE LANB", "Lanche Bem"))
        self.assertTrue(popular_alias_match("SLO SILVA LOBO", "McDonald's Silva Lobo"))
        self.assertTrue(popular_alias_match("PAYPAL *GOOGLE CHATGP", "ChatGPT"))
        self.assertFalse(popular_alias_match("PAYPAL *GOOGLE CHATGP", "Yahoo"))
        self.assertIn("MERCADO DA BOCA", brand_keys_for_text("MEP*Da Boca"))

        lag_pix = extract_embedded_purchase_date("PIX QRS MARKET4U GE07/11", date(2026, 11, 9))
        self.assertEqual(lag_pix, date(2026, 11, 7))
        lag_deb = extract_embedded_purchase_date("COMPRA CARTAO DEB MC 24/10 Uber", date(2026, 10, 26))
        self.assertEqual(lag_deb, date(2026, 10, 24))
        self.assertEqual(parse_brl_decimal("1.234,56-"), Decimal("-1234.56"))

    def test_popular_name_ledger_reconciliation_obeys_strict_schema_and_preserves_popular_name(self):
        bk_id = ObjectId()
        now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        self.db.merchants.insert_one(
            {
                "_id": bk_id,
                "external_key": "merchant-bk",
                "schema_version": 2,
                "created_at": now,
                "updated_at": now,
                "tipo_entidade": "marca",
                "nome_padrao": "Burger King",
                "aliases": [],
            }
        )
        tx = _valid_ledger_tx(
            account_id=self.card_id,
            dt=date(2026, 11, 4),
            valor="-45.90",
            descricao="Burger King",
            merchant_id=bk_id,
        )
        self.db.transactions.insert_one(tx)

        doc = OfficialStatementDocument(
            doc_key="fatura_itau_1111_2026-11",
            filename="fatura_teste.pdf",
            sha256="a" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-10",
            total_amount="45.90",
            status="aberta",
            items=[
                _make_item(
                    "fatura_itau_1111_2026-11",
                    1,
                    "card:itau:1111",
                    "2026-11-04",
                    "-45.90",
                    "IFD *ZAMP SABEL",
                )
            ],
        )

        plans, targets = build_reconciliation_plan(self.db, [doc])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].status, "matched_exact")
        self.assertEqual(plans[0].candidate.doc_id, tx["_id"])

        summary = apply_reconciliation_plan(self.db, [doc], plans, targets)
        self.assertEqual(summary["updated_existing"], 1)
        self.assertEqual(summary["inserted_new"], 0)

        updated_tx = self.db.transactions.find_one({"_id": tx["_id"]})
        self.assertEqual(updated_tx["status_conciliacao"], "confirmado")
        self.assertEqual(updated_tx["descricao_original"], "Burger King")
        self.assertIsNotNone(updated_tx.get("platform_id"))

        updated_merchant = self.db.merchants.find_one({"_id": bk_id})
        self.assertEqual(updated_merchant["nome_padrao"], "Burger King")
        alias_values = [a["valor"] for a in updated_merchant["aliases"]]
        self.assertIn("IFD *ZAMP SABEL", alias_values)

    def test_one_to_one_non_collision_on_duplicate_same_day_amounts(self):
        self.db.provisional_transactions.insert_one(
            {
                "_id": ObjectId(),
                "provisional_id": "prov-m4u-1",
                "status": "confirmed",
                "data_evento": day_start(date(2026, 11, 1)),
                "valor": Decimal128("-6.99"),
                "vendedor": "market4u",
                "account_id": self.card_id,
            }
        )
        doc = OfficialStatementDocument(
            doc_key="fatura_itau_1111_2026-11",
            filename="fatura_m4u.pdf",
            sha256="b" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-10",
            total_amount="13.98",
            status="aberta",
            items=[
                _make_item("fatura_itau_1111_2026-11", 1, "card:itau:1111", "2026-11-01", "-6.99", "MP *MARKET4U"),
                _make_item("fatura_itau_1111_2026-11", 2, "card:itau:1111", "2026-11-01", "-6.99", "MP *MARKET4U"),
            ],
        )

        plans, targets = build_reconciliation_plan(self.db, [doc])
        statuses = sorted(p.status for p in plans)
        self.assertEqual(statuses, ["matched_exact", "missing"])

        summary = apply_reconciliation_plan(self.db, [doc], plans, targets)
        self.assertEqual(summary["updated_existing"], 1)
        self.assertEqual(summary["inserted_new"], 1)
        self.assertEqual(len(self.db.provisional_transactions.docs), 2)

        # Idempotent second run
        plans2, targets2 = build_reconciliation_plan(self.db, [doc])
        self.assertEqual([p.status for p in plans2], ["already_reconciled", "already_reconciled"])
        summary2 = apply_reconciliation_plan(self.db, [doc], plans2, targets2)
        self.assertEqual(summary2["updated_existing"], 0)
        self.assertEqual(summary2["inserted_new"], 0)
        self.assertEqual(len(self.db.provisional_transactions.docs), 2)

    def test_pgconta_principal_match_inserts_only_financing_fee_and_syncs_new_card_and_balances(self):
        self.db.provisional_transactions.insert_one(
            {
                "_id": ObjectId(),
                "provisional_id": "prov-condo",
                "status": "provisional",
                "data_evento": day_start(date(2026, 10, 10)),
                "valor": Decimal128("-923.39"),
                "vendedor": "Condomínio",
                "categoria_sugerida": "Habitação",
                "subcategoria": "Condomínio",
            }
        )
        self.db.provisional_transactions.insert_one(
            {
                "_id": ObjectId(),
                "provisional_id": "prov-weekend-m4u",
                "status": "provisional",
                "data_evento": day_start(date(2026, 11, 7)),
                "valor": Decimal128("-18.50"),
                "vendedor": "market4u",
                "account_profile_id": "itau",
            }
        )

        card_doc = OfficialStatementDocument(
            doc_key="fatura_santander_9999_2026-11",
            filename="fatura_9999.pdf",
            sha256="c" * 64,
            kind="card_statement",
            institution="Santander",
            card_final="9999",
            competencia="2026-11",
            due_date="2026-11-15",
            total_amount="966.13",
            status="aberta",
            items=[
                _make_item(
                    "fatura_santander_9999_2026-11",
                    1,
                    "card:santander:9999",
                    "2026-10-10",
                    "-966.13",
                    "PgConta DSC CONDOMINIAL",
                    principal_amount="-923.39",
                    fee_amount="-42.74",
                )
            ],
        )
        bank_doc = OfficialStatementDocument(
            doc_key="extrato_itau_2026-10-14_2026-11-13",
            filename="extrato_itau.pdf",
            sha256="d" * 64,
            kind="bank_statement",
            institution="Itaú",
            account_profile_key="itau",
            period_start="2026-10-14",
            period_end="2026-11-13",
            opening_balance="100.00",
            closing_balance="81.50",
            closing_as_of="2026-11-13",
            items=[
                _make_item(
                    "extrato_itau_2026-10-14_2026-11-13",
                    1,
                    "profile:itau",
                    "2026-11-09",
                    "-18.50",
                    "PIX QRS MARKET4U GE07/11",
                    purchase_date_hint="2026-11-07",
                )
            ],
        )

        plans, targets = build_reconciliation_plan(self.db, [card_doc, bank_doc])
        self.assertEqual(plans[0].status, "matched_principal")
        self.assertTrue(plans[0].insert_missing_fee)
        self.assertEqual(plans[1].status, "matched_exact")

        report_md = format_markdown_report([card_doc, bank_doc], plans, targets)
        self.assertIn("PgConta DSC CONDOMINIAL", report_md)

        summary = apply_reconciliation_plan(self.db, [card_doc, bank_doc], plans, targets)
        self.assertEqual(len(summary["created_cards"]), 1)
        self.assertEqual(summary["updated_existing"], 2)
        self.assertEqual(summary["inserted_new"], 0)
        self.assertEqual(summary["inserted_fees"], 1)

        fee_rows = [
            r for r in self.db.provisional_transactions.docs if str(r.get("valor")) == "-42.74"
        ]
        self.assertEqual(len(fee_rows), 1)
        self.assertEqual(fee_rows[0]["natureza"], "TARIFA_ENCARGO")
        self.assertEqual(fee_rows[0]["categoria_sugerida"], "Finanças/Impostos")
        self.assertEqual(fee_rows[0]["subcategoria"], "Juros/Multas/IOF")

        snap = self.db.account_balance_snapshots.find_one({"account_key": "itau"})
        expected_cutoff = datetime.combine(date(2026, 11, 14), time.min, LOCAL_ZONE).astimezone(timezone.utc)
        self.assertEqual(snap["as_of"], expected_cutoff)
        self.assertEqual(snap["reference_kind"], "civil_day")
        self.assertEqual(snap["as_of_date"], "2026-11-13")

    def test_correlate_parsed_statements_marks_card_bill_paid(self):
        bill = OfficialStatementDocument(
            doc_key="fatura_itau_1111_2026-11",
            filename="fatura.pdf",
            sha256="e" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-10",
            total_amount="600.00",
            status="aberta",
            items=[],
        )
        stmt = OfficialStatementDocument(
            doc_key="extrato_itau",
            filename="extrato.pdf",
            sha256="f" * 64,
            kind="bank_statement",
            institution="Itaú",
            account_profile_key="itau",
            items=[
                _make_item(
                    "extrato_itau",
                    1,
                    "profile:itau",
                    "2026-11-09",
                    "-600.00",
                    "FATURA PAGA CARTAO ITAU",
                    kind="bill_payment",
                )
            ],
        )
        correlate_parsed_statements([bill, stmt])
        self.assertEqual(bill.status, "paga")
        self.assertEqual(bill.paid_date, "2026-11-09")


if __name__ == "__main__":
    unittest.main()
