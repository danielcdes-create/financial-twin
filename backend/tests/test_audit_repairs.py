import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import ProvisionalAction, ProvisionalUpdate, ReviewDecision, TransactionClassificationUpdate, dashboard, decide_provisional, decide_review, update_provisional, update_transaction_classification
from app.transaction_service import TransactionService
from app.main import classification_match_preview
from test_ingestion import FakeDB, event
from test_transaction_review import ReviewCollection


from observed_contract import observed_review, observed_update

class AuditRepairTests(unittest.TestCase):
    def test_dashboard_includes_confirmed_capture_after_latest_legacy_event(self):
        legacy_date = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        capture_date = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
        db = MagicMock()
        db.transactions.find.return_value.limit.return_value = [{
            "data_evento": legacy_date, "valor": Decimal128("-100.00"),
            "fluxo": "saida", "categoria": "Mercado",
            "classification": {"migration_reason": "despesa_real"},
        }]
        db.provisional_transactions.find.return_value = [{
            "data_evento": capture_date, "valor": Decimal128("-20.00"),
            "fluxo": "saida", "tipo": "compra", "categoria_sugerida": "Transporte",
        }]
        db.review_queue.count_documents.return_value = 0
        db.provisional_transactions.count_documents.return_value = 0
        db.obligations.find.return_value = []
        db.installment_plans.find.return_value = []
        db.commitments.find.return_value = []
        db.investments.count_documents.return_value = 0
        db.source_records.find_one.return_value = None

        result = dashboard(period="month", date_from=None, date_to=None, db=db)

        self.assertEqual(result["period_end"], "2026-09-26")
        self.assertEqual(result["expenses"], 120.0)
        self.assertEqual(result["transaction_count"], 2)
        self.assertEqual(result["legacy_transaction_count"], 1)
        self.assertEqual(result["confirmed_capture_count"], 1)
        self.assertEqual(result["legacy_data_updated_until"], legacy_date.isoformat())
        self.assertEqual(result["confirmed_capture_updated_until"], capture_date.isoformat())

    def test_capture_ingestion_sets_flow_and_evidence_backed_purchase_type(self):
        stamp = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
        db = FakeDB()
        TransactionService(db).process(event("purchase-1", "android_notification", stamp))
        draft = db.provisional_transactions.docs[0]
        self.assertEqual(draft["fluxo"], "saida")
        self.assertEqual(draft["tipo"], "compra")

    def test_edit_rejects_flow_conflicting_with_signed_amount(self):
        draft = {"_id": ObjectId(), "provisional_id": "bad-flow", "status": "confirmed",
                 "valor": Decimal128("-20.93"), "fluxo": "saida", "tipo": "compra"}
        db = FakeDB(provisional_transactions=FakeDB().provisional_transactions)
        db.provisional_transactions.docs.append(draft)
        with self.assertRaises(HTTPException) as failure:
            update_provisional("bad-flow", ProvisionalUpdate(fluxo="entrada"), db)
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(draft["fluxo"], "saida")

    def test_legacy_flow_edit_rejects_opposite_sign(self):
        source = {"_id": ObjectId(), "descricao_original": "Mesmo descritor",
                  "valor": Decimal128("-10.00"), "fluxo": "saida"}
        db = FakeDB(transactions=ReviewCollection([source]))
        with self.assertRaises(HTTPException) as failure:
            update_transaction_classification(str(source["_id"]), observed_update(TransactionClassificationUpdate(fluxo="entrada"), db, str(source["_id"])), db)
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(source["fluxo"], "saida")

    def test_bulk_flow_edit_rejects_peer_with_opposite_sign_before_writing(self):
        source = {"_id": ObjectId(), "descricao_original": "Mesmo descritor",
                  "valor": Decimal128("-10.00"), "fluxo": "saida"}
        peer = {"_id": ObjectId(), "descricao_original": "Mesmo descritor",
                "valor": Decimal128("10.00"), "fluxo": "entrada"}
        db = FakeDB(transactions=ReviewCollection([source, peer]))
        with self.assertRaises(HTTPException) as failure:
            update_transaction_classification(str(source["_id"]), observed_update(TransactionClassificationUpdate(fluxo="saida", apply_similar_classification=True, classification_preview_token=classification_match_preview(str(source['_id']),db)['preview_token']), db, str(source["_id"])), db)
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(source["fluxo"], "saida")
        self.assertEqual(peer["fluxo"], "entrada")
        self.assertEqual(db.audit_log.docs, [])

    def test_confirmation_requires_type_and_coherent_flow(self):
        draft = {"_id": ObjectId(), "provisional_id": "needs-type", "event_id": "event-1",
                 "status": "provisional", "valor": Decimal128("-10.00"), "fluxo": "saida"}
        db = FakeDB()
        db.provisional_transactions.docs.append(draft)
        db.raw_events.docs.append({"_id": ObjectId(), "event_id": "event-1"})
        with self.assertRaises(HTTPException) as failure:
            decide_provisional("needs-type", ProvisionalAction(action="confirm"), db)
        self.assertEqual(failure.exception.status_code, 422)
        draft["tipo"] = "compra"
        result = decide_provisional("needs-type", ProvisionalAction(action="confirm"), db)
        self.assertEqual(result["status"], "confirmed")

    def test_keep_separate_records_confirmation_and_raw_link(self):
        draft = {"_id": ObjectId(), "provisional_id": "separate-1", "event_id": "event-2",
                 "status": "provisional", "valor": Decimal128("-10.00"), "fluxo": "saida", "tipo": "compra"}
        db = FakeDB()
        db.provisional_transactions.docs.append(draft)
        db.raw_events.docs.append({"_id": ObjectId(), "event_id": "event-2"})
        db.review_queue.docs.append({"_id": ObjectId(), "review_id": "review-2", "event_id": "event-2", "status": "pending"})
        result = decide_review("review-2", observed_review(ReviewDecision(action="keep_separate"), db, "review-2"), db)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(draft["status"], "confirmed")
        self.assertIn("confirmed_at", draft)
        self.assertEqual(db.raw_events.docs[0]["linked_transaction_id"], draft["_id"])


if __name__ == "__main__":
    unittest.main()
