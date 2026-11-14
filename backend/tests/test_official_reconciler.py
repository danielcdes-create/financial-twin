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
BILL_SCHEMA = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "_id", "external_key", "schema_version", "card_account_id", "competencia",
        "data_vencimento", "saldo_anterior", "novos_lancamentos", "encargos",
        "pagamentos", "valor_total", "status", "items", "created_at", "updated_at",
    ],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "external_key": {"bsonType": "string"},
        "schema_version": {"bsonType": "int"},
        "card_account_id": {"bsonType": "objectId"},
        "competencia": {"bsonType": "string"},
        "data_vencimento": {"bsonType": "date"},
        "saldo_anterior": {"bsonType": "decimal"},
        "novos_lancamentos": {"bsonType": "decimal"},
        "encargos": {"bsonType": "decimal"},
        "pagamentos": {"bsonType": "decimal"},
        "valor_pago": {"bsonType": "decimal"},
        "saldo_aberto": {"bsonType": "decimal"},
        "valor_total": {"bsonType": "decimal"},
        "status": {"enum": ["aberta", "paga", "parcialmente_paga", "vencida"]},
        "items": {"bsonType": "array"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
    },
}
OBL_SCHEMA = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "_id", "external_key", "schema_version", "tipo", "origem_id",
        "data_vencimento", "valor_original", "valor_pago", "saldo_aberto",
        "principal", "status", "created_at", "updated_at",
    ],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "external_key": {"bsonType": "string"},
        "schema_version": {"bsonType": "int"},
        "tipo": {"enum": ["fatura_cartao", "financiamento", "emprestimo", "imposto", "outro"]},
        "origem_id": {"bsonType": "objectId"},
        "competencia": {"bsonType": "string"},
        "data_vencimento": {"bsonType": "date"},
        "valor_original": {"bsonType": "decimal"},
        "valor_pago": {"bsonType": "decimal"},
        "saldo_aberto": {"bsonType": "decimal"},
        "principal": {"bsonType": "decimal"},
        "status": {"enum": ["futura", "paga", "parcialmente_paga", "vencida", "cancelada"]},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
    },
}


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
    _SCHEMAS = {
        "transactions": TX_SCHEMA,
        "accounts": ACC_SCHEMA,
        "credit_card_bills": BILL_SCHEMA,
        "obligations": OBL_SCHEMA,
    }

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

    def test_disjoint_brands_and_same_category_stores_never_merge(self):
        # P0-03: Burger King vs McDonald's on the exact same day/amount/card must never merge
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
        self.db.transactions.insert_one(
            _valid_ledger_tx(
                account_id=self.card_id,
                dt=date(2026, 11, 4),
                valor="-35.90",
                descricao="Burger King",
                categoria="Alimentação",
                merchant_id=bk_id,
            )
        )
        self.db.provisional_transactions.insert_one(
            {
                "_id": ObjectId(),
                "provisional_id": "prov-padaria-a",
                "status": "confirmed",
                "data_evento": day_start(date(2026, 11, 4)),
                "valor": Decimal128("-25.00"),
                "vendedor": "Padaria Pão Quente",
                "categoria_sugerida": "Alimentação",
                "account_id": self.card_id,
            }
        )
        doc = OfficialStatementDocument(
            doc_key="fatura_itau_1111_202611",
            filename="fatura.pdf",
            sha256="1" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-20",
            total_amount="60.90",
            status="aberta",
            items=[
                _make_item("fatura_itau_1111_202611", 1, "card:itau:1111", "2026-11-04", "-35.90", "ARCOS DOURADOS"),
                OfficialStatementItem(
                    doc_key="fatura_itau_1111_202611",
                    row_index=2,
                    target="card:itau:1111",
                    date="2026-11-04",
                    purchase_date_hint="2026-11-04",
                    statement_month="2026-11",
                    amount="-25.00",
                    kind="expense",
                    description="RESTAURANTE SABOR MINEIRO",
                    popular_vendor="RESTAURANTE SABOR MINEIRO",
                    platform=None,
                    categoria="Alimentação",
                    subcategoria="Restaurantes/Bares",
                    natureza="COMPRA",
                    tipo="compra",
                ),
            ],
        )
        plans, targets = build_reconciliation_plan(self.db, [doc])
        self.assertEqual([p.status for p in plans], ["missing", "missing"])
        apply_reconciliation_plan(self.db, [doc], plans, targets)
        bk_doc = self.db.merchants.find_one({"_id": bk_id})
        self.assertEqual(bk_doc["aliases"], [])

    def test_prior_cycle_installment_preserves_june_first_installment_and_sets_competencia(self):
        # P0-04: 05/10 on 2026-11 bill must not eat 01/10 from 2026-06 and must record competencia='2026-11'
        from app.economic_period import economic_month

        june_tx = _valid_ledger_tx(
            account_id=self.card_id,
            dt=date(2026, 6, 12),
            valor="-150.00",
            descricao="KALUNGA 01/10",
            categoria="Compras",
        )
        june_tx["installment"] = {"numero": 1, "total": 10}
        self.db.transactions.insert_one(june_tx)

        item_05 = _make_item("fatura_itau_1111_202611", 1, "card:itau:1111", "2026-06-12", "-150.00", "KALUNGA 05/10")
        item_05.installment_number = 5
        item_05.installment_total = 10
        item_05.prior_cycle_installment = True
        item_05.statement_month = "2026-11"

        doc = OfficialStatementDocument(
            doc_key="fatura_itau_1111_202611",
            filename="fatura_nov.pdf",
            sha256="2" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-20",
            total_amount="150.00",
            status="aberta",
            items=[item_05],
        )
        plans, targets = build_reconciliation_plan(self.db, [doc])
        self.assertEqual(plans[0].status, "missing")
        summary = apply_reconciliation_plan(self.db, [doc], plans, targets)
        self.assertEqual(summary["inserted_new"], 1)
        inserted = self.db.provisional_transactions.docs[0]
        self.assertEqual(inserted.get("competencia"), "2026-11")
        self.assertEqual(economic_month(inserted), "2026-11")
        self.assertEqual(inserted.get("parcela_numero"), 5)

    def test_deleted_and_cancelled_movements_are_not_resurrected(self):
        # P0-05: deleted_movements and cancelled captures must be skipped
        deleted_tx = _valid_ledger_tx(
            account_id=self.card_id,
            dt=date(2026, 11, 3),
            valor="-89.90",
            descricao="PETZ",
            categoria="Pets",
        )
        self.db.deleted_movements.insert_one(
            {
                "_id": f"ledger:{deleted_tx['_id']}",
                "source": "ledger",
                "identity": str(deleted_tx["_id"]),
                "document": deleted_tx,
                "preview_token": "tok",
                "reason": "user_deleted_from_statement",
            }
        )
        self.db.provisional_transactions.insert_one(
            {
                "_id": ObjectId(),
                "provisional_id": "prov-cancelled-uber",
                "status": "cancelled",
                "data_evento": day_start(date(2026, 11, 4)),
                "valor": Decimal128("-22.50"),
                "vendedor": "Uber",
                "account_id": self.card_id,
            }
        )
        doc = OfficialStatementDocument(
            doc_key="fatura_itau_1111_202611",
            filename="fatura.pdf",
            sha256="3" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-20",
            total_amount="112.40",
            status="aberta",
            items=[
                _make_item("fatura_itau_1111_202611", 1, "card:itau:1111", "2026-11-03", "-89.90", "PETZ"),
                _make_item("fatura_itau_1111_202611", 2, "card:itau:1111", "2026-11-04", "-22.50", "UBER *TRIP"),
            ],
        )
        plans, targets = build_reconciliation_plan(self.db, [doc])
        self.assertEqual([p.status for p in plans], ["skipped_deleted_by_user", "skipped_deleted_by_user"])
        summary = apply_reconciliation_plan(self.db, [doc], plans, targets)
        self.assertEqual(summary["inserted_new"], 0)
        self.assertEqual(summary["skipped_deleted_by_user"], 2)

    def test_overlapping_bank_statements_and_reordered_rows_do_not_duplicate(self):
        # P0-07: partial + full Itaú statement in same batch deduplicate items and keep latest closing balance
        partial = OfficialStatementDocument(
            doc_key="extrato_itau_20261101_20261110",
            filename="parcial.pdf",
            sha256="4" * 64,
            kind="bank_statement",
            institution="Itaú",
            account_profile_key="itau",
            period_start="2026-11-01",
            period_end="2026-11-10",
            opening_balance="500.00",
            closing_balance="450.00",
            closing_as_of="2026-11-10",
            items=[
                _make_item("extrato_itau_20261101_20261110", 1, "profile:itau", "2026-11-05", "-50.00", "DA CEMIG"),
            ],
        )
        full = OfficialStatementDocument(
            doc_key="extrato_itau_20261101_20261130",
            filename="mensal.pdf",
            sha256="5" * 64,
            kind="bank_statement",
            institution="Itaú",
            account_profile_key="itau",
            period_start="2026-11-01",
            period_end="2026-11-30",
            opening_balance="500.00",
            closing_balance="380.00",
            closing_as_of="2026-11-30",
            items=[
                _make_item("extrato_itau_20261101_20261130", 1, "profile:itau", "2026-11-05", "-50.00", "DA CEMIG"),
                _make_item("extrato_itau_20261101_20261130", 2, "profile:itau", "2026-11-20", "-70.00", "DA TIM CELU"),
            ],
        )
        plans, targets = build_reconciliation_plan(self.db, [partial, full])
        summary = apply_reconciliation_plan(self.db, [partial, full], plans, targets)
        self.assertEqual(summary["inserted_new"], 2)
        self.assertEqual(summary["skipped_overlap_duplicate"], 1)
        snap = self.db.account_balance_snapshots.find_one({"account_key": "itau"})
        self.assertEqual(snap["as_of_date"], "2026-11-30")
        self.assertEqual(str(snap["amount"]), "380.00")

        # Re-export with row_index shifted must still recognize already_reconciled by canonical_key
        reordered = OfficialStatementDocument(
            doc_key="extrato_itau_20261101_20261130_v2",
            filename="mensal_v2.pdf",
            sha256="6" * 64,
            kind="bank_statement",
            institution="Itaú",
            account_profile_key="itau",
            period_start="2026-11-01",
            period_end="2026-11-30",
            opening_balance="500.00",
            closing_balance="360.00",
            closing_as_of="2026-11-30",
            items=[
                _make_item("extrato_itau_20261101_20261130_v2", 1, "profile:itau", "2026-11-02", "-20.00", "UBER"),
                _make_item("extrato_itau_20261101_20261130_v2", 2, "profile:itau", "2026-11-05", "-50.00", "DA CEMIG"),
                _make_item("extrato_itau_20261101_20261130_v2", 3, "profile:itau", "2026-11-20", "-70.00", "DA TIM CELU"),
            ],
        )
        plans2, _ = build_reconciliation_plan(self.db, [reordered])
        self.assertEqual([p.status for p in plans2], ["missing", "already_reconciled", "already_reconciled"])

    def test_atlas_liquidada_obligation_sync_and_raw_event_healing(self):
        # P0-06 & P0-08: Atlas obligations enum uses 'liquidada' & 'atrasada'; updating open -> paid must keep 1:1 parity
        from app.official_reconciler import _conform_document_to_schema
        from app.official_statement_parsers import _infer_statement_item_date

        atlas_obl_schema = {
            **OBL_SCHEMA,
            "properties": {
                **OBL_SCHEMA["properties"],
                "status": {"enum": ["futura", "liquidada", "parcialmente_paga", "atrasada", "cancelada"]},
            },
        }
        self.db._SCHEMAS = {**self.db._SCHEMAS, "obligations": atlas_obl_schema}
        self.db["obligations"] = ReconcilerCollection(name="obligations", validator_schema=atlas_obl_schema)

        doc_open = OfficialStatementDocument(
            doc_key="fatura_itau_1111_202612",
            filename="fatura_dez.pdf",
            sha256="7" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-12",
            due_date="2099-12-10",
            total_amount="100.00",
            status="aberta",
            items=[
                _make_item("fatura_itau_1111_202612", 1, "card:itau:1111", "2026-11-12", "-100.00", "UBER"),
            ],
        )
        plans, targets = build_reconciliation_plan(self.db, [doc_open])
        apply_reconciliation_plan(self.db, [doc_open], plans, targets)
        obl = self.db.obligations.docs[0]
        self.assertEqual(obl["status"], "futura")

        # Simulate orphaned provisional without raw_events, then re-apply as paid
        self.db.raw_events.docs.clear()
        self.db.official_statement_reconciliations.docs.clear()
        doc_paid = OfficialStatementDocument(
            doc_key="fatura_itau_1111_202612",
            filename="fatura_dez.pdf",
            sha256="7" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-12",
            due_date="2099-12-10",
            paid_date="2026-12-09",
            total_amount="100.00",
            status="paga",
            items=[
                _make_item("fatura_itau_1111_202612", 1, "card:itau:1111", "2026-11-12", "-100.00", "UBER"),
            ],
        )
        plans_p, targets_p = build_reconciliation_plan(self.db, [doc_paid])
        summary_p = apply_reconciliation_plan(self.db, [doc_paid], plans_p, targets_p)
        self.assertEqual(summary_p["inserted_new"], 0)
        self.assertEqual(self.db.credit_card_bills.docs[0]["status"], "paga")
        self.assertEqual(self.db.obligations.docs[0]["status"], "liquidada")

        # Missing required fields must raise instead of fabricating synthetic values
        with self.assertRaises(ValueError):
            _conform_document_to_schema(self.db, "credit_card_bills", {"_id": ObjectId()})

        # Leap-day installment on non-leap statement year resolves safely
        leap_dt, prior = _infer_statement_item_date("29/02", 2026, 3)
        self.assertEqual(leap_dt, date(2024, 2, 29))
        self.assertTrue(prior)

        # Outgoing PIX TRANSF must never classify as Receitas, and surname/word collisions must not match brands
        out_pix = classify_official_descriptor("PIX TRANSF JOAO ARAUJO 05/11", signed_amount=Decimal("-80.00"))
        self.assertIsNone(out_pix.categoria)
        self.assertFalse(popular_alias_match("PIX TRANSF JOAO ARAUJO", "Drogaria Araujo"))
        self.assertFalse(popular_alias_match("ZONA AZUL BH", "Azul Linhas Aéreas"))

        # 1-to-1 correlation: one bank payment cannot mark two card bills paid
        b1 = OfficialStatementDocument(
            doc_key="fatura_itau_1111_202611",
            filename="f1.pdf",
            sha256="8" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="1111",
            competencia="2026-11",
            due_date="2026-11-10",
            total_amount="500.00",
            status="aberta",
            items=[],
        )
        b2 = OfficialStatementDocument(
            doc_key="fatura_itau_2222_202611",
            filename="f2.pdf",
            sha256="9" * 64,
            kind="card_statement",
            institution="Itaú",
            card_final="2222",
            competencia="2026-11",
            due_date="2026-11-10",
            total_amount="500.00",
            status="aberta",
            items=[],
        )
        st = OfficialStatementDocument(
            doc_key="extrato_itau",
            filename="ex.pdf",
            sha256="0" * 64,
            kind="bank_statement",
            institution="Itaú",
            account_profile_key="itau",
            items=[
                _make_item("extrato_itau", 1, "profile:itau", "2026-11-09", "-500.00", "FATURA PAGA", kind="bill_payment")
            ],
        )
        correlate_parsed_statements([b1, b2, st])
        self.assertEqual(sorted([b1.status, b2.status]), ["aberta", "paga"])


if __name__ == "__main__":
    unittest.main()
