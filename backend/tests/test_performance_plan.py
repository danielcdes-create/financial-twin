import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from bson.decimal128 import Decimal128
from pymongo.database import Database

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.atomic import _SessionDatabase, mongo_snapshot, with_batch_size
from app.auth import OWNER_ID, owner, owner_cache
from app.main import CLASSIFICATION_FIELDS, _build_classification_options, cached_taxonomy_overrides, dashboard, dashboard_overview, movements, taxonomy_overrides_cache
from app.reference_cache import classification_cache, dashboard_snapshot_cache, historical_context_cache, subcategory_catalog_cache, unwrap_database


def dashboard_db(transactions, captures=(), commitments=(), accounts=()):
    db = MagicMock()
    db.transactions.find.return_value.limit.return_value = list(transactions)
    db.provisional_transactions.find.return_value = list(captures)
    db.review_queue.count_documents.return_value = 0
    db.provisional_transactions.count_documents.return_value = 0
    db.obligations.find.return_value = []
    db.installment_plans.find.return_value = []
    db.commitments.find.return_value = list(commitments)
    db.accounts.find.return_value = list(accounts)
    db.account_profiles.find.return_value = []
    db.future_schedule_overrides.find.return_value = []
    db.investments.count_documents.return_value = 0
    db.source_records.find_one.return_value = None
    return db


class PerformancePlanTests(unittest.TestCase):
    def setUp(self):
        classification_cache.invalidate()
        historical_context_cache.invalidate()
        subcategory_catalog_cache.invalidate()
        dashboard_snapshot_cache.invalidate()
        taxonomy_overrides_cache.invalidate()
        owner_cache.invalidate()

    def test_dashboard_has_no_ten_thousand_record_cutoff(self):
        stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
        rows = [{"_id": index, "data_evento": stamp, "valor": Decimal128("-1.00"),
                 "fluxo": "saida", "categoria": "Teste",
                 "classification": {"migration_reason": "despesa_real"}}
                for index in range(10_001)]
        result = dashboard(period="all", date_from=None, date_to=None, db=dashboard_db(rows))
        self.assertEqual(result["legacy_transaction_count"], 10_001)
        self.assertEqual(result["expenses"], 10_001.0)

    def test_dashboard_overview_reads_one_transaction_snapshot(self):
        stamp = datetime(2026, 9, 10, tzinfo=timezone.utc)
        commitments = [
            {
                "commitment_id": f"c-{idx}",
                "name": f"Conta Variável {idx}",
                "kind": "assinatura",
                "amount": 100.0,
                "amount_mode": "rolling_average",
                "frequency": "mensal",
                "next_due_date": "2026-09-15",
                "purchase_date": "2026-01-15",
                "account_id": "acc-1",
                "status": "ativo",
            }
            for idx in range(5)
        ]
        db = dashboard_db(
            [{"_id": 1, "data_evento": stamp, "valor": Decimal128("-20.00"),
              "fluxo": "saida", "categoria": "Mercado",
              "classification": {"migration_reason": "despesa_real"}}],
            commitments=commitments,
            accounts=[{"_id": "acc-1", "instituicao": "Itaú", "tipo": "conta_corrente"}],
        )
        result = dashboard_overview(period="month", year_start=stamp.date().replace(month=1, day=1),
                                    month_start=stamp.date().replace(day=1), month_end=stamp.date(), db=db)
        self.assertEqual(db.transactions.find.call_count, 1)
        self.assertEqual(db.provisional_transactions.find.call_count, 1)
        self.assertLessEqual(db.accounts.find.call_count, 1)
        self.assertEqual(db.accounts.find_one.call_count, 0)
        self.assertEqual(db.transactions.find_one.call_count, 0)
        self.assertEqual(db.provisional_transactions.find_one.call_count, 0)
        self.assertEqual(result["selected"]["expenses"], 20.0)
        self.assertEqual(result["month"]["expenses"], 20.0)
        self.assertEqual(result["year"]["expenses"], 20.0)
        self.assertEqual(db.commitments.find.call_count, 1)
        self.assertEqual(db.installment_plans.find.call_count, 1)
        self.assertEqual(db.review_queue.count_documents.call_count, 1)
        self.assertEqual(db.investments.count_documents.call_count, 1)
        self.assertEqual(db.obligations.find.call_count, 2)

    def test_mongo_snapshot_does_not_open_transaction_session(self):
        db = MagicMock()
        with mongo_snapshot(db) as (snapshot_db, consistency):
            self.assertIs(snapshot_db, db)
            self.assertEqual(consistency, "best_effort")
        self.assertEqual(db.client.start_session.call_count, 0)

    def test_with_batch_size_applies_2000_to_cursors(self):
        class FakeCursor:
            def __init__(self):
                self.applied_size = None

            def batch_size(self, size):
                self.applied_size = size
                return self

        cursor = FakeCursor()
        returned = with_batch_size(cursor)
        self.assertIs(returned, cursor)
        self.assertEqual(cursor.applied_size, 2000)

    def test_unwrap_database_preserves_pymongo_database_identity(self):
        class FakeMongoDatabase(Database):
            def __init__(self):
                self.categories = MagicMock()
                self.categories.find.return_value = [
                    {"name": "Alimentação", "active": True, "subcategories": [{"name": "Padaria", "active": True}]}
                ]
                self.account = MagicMock()
                self.account.find_one.return_value = {"_id": OWNER_ID, "kind": "app_identity", "auth_version": 1, "sessions": []}

            def __getattr__(self, name):
                # Emulate PyMongo Database.__getattr__ returning a new Collection object on .db access
                return SimpleNamespace(collection_name=name)

        raw_db = FakeMongoDatabase()
        session_db = _SessionDatabase(raw_db, session=object())
        self.assertIs(unwrap_database(raw_db), raw_db)
        self.assertIs(unwrap_database(session_db), raw_db)

        first = cached_taxonomy_overrides(raw_db)
        second = cached_taxonomy_overrides(session_db)
        self.assertEqual(first, second)
        self.assertEqual(raw_db.categories.find.call_count, 1)

        acc1 = owner(raw_db)
        acc2 = owner(session_db)
        self.assertEqual(acc1["_id"], OWNER_ID)
        self.assertEqual(acc2["_id"], OWNER_ID)
        self.assertEqual(raw_db.account.find_one.call_count, 1)

    def test_classification_options_uses_single_aggregation_per_collection(self):
        db = MagicMock()
        tx_agg = {"_id": None, "platform_id": [], **{f: [] for f in CLASSIFICATION_FIELDS}}
        tx_agg.update({
            "categoria": ["Alimentação", "Transporte"],
            "subcategoria": ["Mercado"],
            "natureza": ["COMPRA"],
            "fluxo": ["saida"],
            "metodo_pagamento": ["pix"],
            "fornecedor": ["Padaria Central"],
            "vendedor": ["Padaria Central"],
            "plataforma": ["iFood"],
        })
        prov_agg = {"_id": None, "categoria_sugerida": ["Saúde"], "platform_id": [], **{f: [] for f in CLASSIFICATION_FIELDS if f != "categoria"}}
        db.transactions.aggregate.return_value = [tx_agg]
        db.provisional_transactions.aggregate.return_value = [prov_agg]
        db.merchants.find.return_value = [{"_id": "m1", "nome_padrao": "Padaria Central", "categoria_padrao": "Alimentação"}]
        db.categories.find.return_value = [{"name": "Alimentação"}]
        db.subcategories.find.return_value = []
        db.platforms.find.return_value = []
        db.taxonomy_overrides.find.return_value = []
        db.commitments.find.return_value = []
        db.transactions.find.return_value.sort.return_value.limit.return_value = []

        options = _build_classification_options(db)
        self.assertEqual(db.transactions.aggregate.call_count, 1)
        self.assertEqual(db.provisional_transactions.aggregate.call_count, 1)
        self.assertEqual(db.transactions.distinct.call_count, 0)
        self.assertEqual(db.provisional_transactions.distinct.call_count, 0)
        self.assertEqual(db.merchants.distinct.call_count, 0)
        self.assertIn("Alimentação", options["categoria"])
        self.assertIn("Saúde", options["categoria"])
        self.assertIn("Padaria Central", options["vendedor"])

    def test_movement_pages_cover_both_sources_without_duplicates(self):
        from bson import ObjectId
        from test_uncategorized_movements import CategoryCollection
        from test_record_review import RecordDB
        db=RecordDB();db["transactions"]=CategoryCollection();db["provisional_transactions"]=CategoryCollection()
        identities={}
        for label,day,captured in [("l3",30,False),("l2",28,False),("l1",26,False),("p2",29,True),("p1",27,True)]:
            identity=ObjectId();identities[str(identity)]=label
            row={"_id":identity,"data_evento":datetime(2026,9,day,10,tzinfo=timezone.utc),"status":"confirmed"}
            if captured:row["provisional_id"]=str(identity)
            db["provisional_transactions" if captured else "transactions"].docs.append(row)
        args={"limit":3,"search":None,"category":None,"account_id":None,"date_from":None,"date_to":None,"db":db}
        first = movements(offset=0,**args)
        second = movements(offset=3,**args)
        ids = [identities[item.get("id") or item.get("provisional_id")] for item in first["items"] + second["items"]]
        self.assertEqual(ids, ["l3", "p2", "l2", "p1", "l1"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(second["has_more"])

    def test_provisional_review_syncs_new_alias_on_existing_merchant_without_validation_error(self):
        from uuid import uuid4
        from bson import ObjectId
        from pymongo.errors import WriteError
        from app.main import ProvisionalUpdate, _sync_merchant_alias, update_provisional
        from test_ingestion import Collection, Cursor, FakeDB

        def apply_projection(doc, projection):
            if not projection or doc is None:
                return doc
            out = {"_id": doc["_id"]} if "_id" in doc and projection.get("_id", 1) != 0 else {}
            for key, val in projection.items():
                if not val or key == "_id":
                    continue
                if "." in key:
                    top, sub = key.split(".", 1)
                    if isinstance(doc.get(top), list):
                        out[top] = [{sub: item[sub]} if isinstance(item, dict) and sub in item else {} for item in doc[top]]
                elif key in doc:
                    out[key] = [dict(x) if isinstance(x, dict) else x for x in doc[key]] if isinstance(doc[key], list) else doc[key]
            return out

        class ValidatedMerchantCollection(Collection):
            def find_one(self, query, projection=None):
                doc = super().find_one(query)
                return apply_projection(doc, projection) if projection and doc is not None else doc

            def find(self, query, projection=None):
                if "nome_padrao" in query and "$nin" in query["nome_padrao"]:
                    matched = [doc for doc in self.docs if doc.get("nome_padrao") not in query["nome_padrao"]["$nin"]]
                else:
                    matched = [doc for doc in self.docs if self._matches(doc, query)]
                return Cursor(apply_projection(doc, projection) if projection else doc for doc in matched)

            def update_one(self, query, update):
                set_fields = (update or {}).get("$set", {})
                if "aliases" in set_fields:
                    for alias in set_fields["aliases"]:
                        if not isinstance(alias, dict) or not all(k in alias for k in ("valor", "normalizado", "status", "source_record_ids")):
                            raise WriteError("Document failed validation: alias missing required schema fields", 121)
                return super().update_one(query, update)

        stamp = datetime(2026, 10, 20, 21, 21, 40, tzinfo=timezone.utc)
        vendor_id = ObjectId()
        platform_id = ObjectId()
        db = FakeDB(
            merchants=ValidatedMerchantCollection([
                {
                    "_id": vendor_id,
                    "external_key": "merchant-lanche-bem",
                    "schema_version": 2,
                    "tipo_entidade": "marca",
                    "nome_padrao": "Lanche Bem",
                    "aliases": [{"valor": "LANCHE BEM", "normalizado": "LANCHE BEM", "status": "confirmado", "source_record_ids": []}],
                },
                {
                    "_id": platform_id,
                    "external_key": "merchant-cappta",
                    "schema_version": 2,
                    "tipo_entidade": "marca",
                    "nome_padrao": "Cappta",
                    "aliases": [],
                },
            ]),
            provisional_transactions=Collection([
                {
                    "_id": ObjectId(),
                    "provisional_id": "prov-review-1",
                    "event_id": "ev-review-1",
                    "status": "provisional",
                    "revision": 1,
                    "merchant_raw": "CAPPTA LANCHE BEM FILIAL 2",
                    "valor": Decimal128("-35.90"),
                    "fluxo": "saida",
                    "tipo": "compra",
                    "natureza": "COMPRA",
                    "categoria_sugerida": "Alimentação",
                    "subcategoria": "Padaria",
                    "metodo_pagamento": "cartao_credito",
                    "data_evento": stamp,
                },
            ]),
            raw_events=Collection([{"_id": ObjectId(), "event_id": "ev-review-1", "processing_status": "provisional"}]),
        )
        req_id = uuid4()
        res = update_provisional(
            "prov-review-1",
            ProvisionalUpdate(
                request_id=req_id,
                expected_revision=1,
                vendedor="Lanche Bem",
                plataforma="Cappta",
                metodo_pagamento="cartao_credito",
                fluxo="saida",
                action="confirm",
            ),
            db,
        )
        self.assertTrue(res["persisted"])
        self.assertEqual(res["request_id"], str(req_id))
        self.assertEqual(res["item"]["status"], "confirmed")
        self.assertEqual(res["item"]["vendedor"], "Lanche Bem")
        merchant_doc = next(d for d in db.merchants.docs if d["_id"] == vendor_id)
        self.assertEqual(len(merchant_doc["aliases"]), 2)
        for alias in merchant_doc["aliases"]:
            self.assertTrue(all(k in alias for k in ("valor", "normalizado", "status", "source_record_ids")))
        # Also verify defensive reload when _sync_merchant_alias receives a stripped projection
        stripped = {"_id": vendor_id, "nome_padrao": "Lanche Bem", "aliases": [{"normalizado": "LANCHE BEM"}]}
        _sync_merchant_alias(db, stripped, "CAPPTA LANCHE BEM 3", "Lanche Bem", True, "Cappta", None, stamp)
        self.assertEqual(len(merchant_doc["aliases"]), 3)



    def test_category_subcategories_populates_cache_when_preloaded(self):
        from app.taxonomy import category_subcategories

        class FakeMongoDatabase(Database):
            def __init__(self):
                self.categories = MagicMock()
                self.categories.find.return_value = [
                    {"name": "Alimentação", "active": True, "subcategories": [{"name": "Padaria", "active": True}]}
                ]
                self.transactions = MagicMock()
                self.transactions.find.return_value = []
                self.provisional_transactions = MagicMock()
                self.provisional_transactions.find.return_value = []

            def __getattr__(self, name):
                return SimpleNamespace(collection_name=name)

        raw_db = FakeMongoDatabase()
        preloaded_txs = [{"categoria": "Alimentação", "subcategoria": "Supermercado/Feira/Açougue"}]
        first = category_subcategories(raw_db, preloaded_transactions=preloaded_txs, preloaded_captures=[])
        second = category_subcategories(raw_db)
        self.assertEqual(first, second)
        self.assertEqual(raw_db.transactions.find.call_count, 0)
        self.assertEqual(raw_db.provisional_transactions.find.call_count, 0)

    def test_dashboard_snapshot_cache_and_month_view_deduplication(self):
        from datetime import date

        class FakeMongoDatabase(Database):
            def __init__(self):
                stamp = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
                self.transactions = MagicMock()
                self.transactions.find.return_value = [
                    {
                        "_id": 1,
                        "data_evento": stamp,
                        "valor": Decimal128("-50.00"),
                        "fluxo": "saida",
                        "categoria": "Alimentação",
                        "classification": {"migration_reason": "despesa_real"},
                    }
                ]
                self.provisional_transactions = MagicMock()
                self.provisional_transactions.find.return_value = []
                self.provisional_transactions.count_documents.return_value = 0
                self.review_queue = MagicMock()
                self.review_queue.count_documents.return_value = 0
                self.obligations = MagicMock()
                self.obligations.find.return_value = []
                self.installment_plans = MagicMock()
                self.installment_plans.find.return_value = []
                self.commitments = MagicMock()
                self.commitments.find.return_value = []
                self.accounts = MagicMock()
                self.accounts.find.return_value = []
                self.account_profiles = MagicMock()
                self.account_profiles.find.return_value = []
                self.future_schedule_overrides = MagicMock()
                self.future_schedule_overrides.find.return_value = []
                self.investments = MagicMock()
                self.investments.count_documents.return_value = 0
                self.source_records = MagicMock()
                self.source_records.find_one.return_value = None

            def __getattr__(self, name):
                return SimpleNamespace(collection_name=name)

        raw_db = FakeMongoDatabase()
        res1 = dashboard_overview(
            period="month",
            year_start=date(2026, 1, 1),
            month_start=date(2026, 10, 1),
            month_end=date(2026, 10, 21),
            db=raw_db,
        )
        res2 = dashboard_overview(
            period="month",
            year_start=date(2026, 1, 1),
            month_start=date(2026, 10, 1),
            month_end=date(2026, 10, 21),
            db=raw_db,
        )
        self.assertEqual(raw_db.transactions.find.call_count, 1)
        self.assertEqual(raw_db.provisional_transactions.find.call_count, 1)
        self.assertEqual(res1["month"]["period_end"], "2026-10-21")
        self.assertEqual(res1["month"]["expenses"], 50.0)
        self.assertEqual(res2["month"]["expenses"], 50.0)


if __name__ == "__main__":
    unittest.main()
