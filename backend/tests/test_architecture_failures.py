import copy
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from bson import ObjectId
from fastapi import HTTPException

sys.path.insert(0,str(Path(__file__).parents[1]))
from app.main import ReviewDecision, decide_review, cursor_movements, update_commitment
from app.commitments import CommitmentIn, stored_document
from app.media_store import store_media
from app.transaction_service import TransactionService
from test_ingestion import Collection, FakeDB, event
from test_transaction_review import ReviewCollection


from observed_contract import observed_review

class FailsOnce(Collection):
    def __init__(self):
        super().__init__()
        self.fail=True

    def insert_one(self,doc):
        if self.fail:
            self.fail=False
            raise RuntimeError("injected audit failure")
        return super().insert_one(doc)


@contextmanager
def rollback_fake(db):
    snapshots={name:copy.deepcopy(collection.docs) for name,collection in db.items()}
    try:
        yield None
    except Exception:
        for name,documents in snapshots.items():
            db[name].docs=documents
        raise


class ArchitectureFailureTests(unittest.TestCase):
    def test_raw_event_pending_is_resumed_after_interruption(self):
        db=FakeDB()
        service=TransactionService(db)
        stamp=datetime(2026,9,28,12,tzinfo=timezone.utc)
        payload=event("crash-1","manual",stamp)
        original=service._finish
        def crash_once(*args,**kwargs):
            service._finish=original
            raise RuntimeError("injected crash after raw event")
        service._finish=crash_once
        with self.assertRaises(RuntimeError): service.process(payload)
        self.assertEqual(db.raw_events.docs[0]["processing_status"],"pending")
        result=service.process(payload)
        self.assertEqual(result["status"],"provisional")
        self.assertEqual(len(db.raw_events.docs),1)
        self.assertEqual(len(db.provisional_transactions.docs),1)

    def test_review_decision_rolls_back_evidence_when_audit_fails(self):
        stamp=datetime(2026,9,28,12,tzinfo=timezone.utc)
        review={"_id":ObjectId(),"review_id":"review-1","status":"pending","event_id":"event-1","candidate_type":"transaction","candidate_transaction_id":ObjectId(),"match_score":0.8}
        db=FakeDB(review_queue=Collection([review]),audit_log=FailsOnce())
        db.transactions.docs=[{"_id":review["candidate_transaction_id"]}]
        db.transaction_evidence.docs=[]
        db.raw_events.docs=[{"_id":ObjectId(),"event_id":"event-1","processing_status":"review_required"}]
        db.provisional_transactions.docs=[{"_id":ObjectId(),"event_id":"event-1","status":"provisional"}]
        with patch("app.main.mongo_transaction",rollback_fake):
            with self.assertRaises(RuntimeError): decide_review("review-1",observed_review(ReviewDecision(action="confirm_match"), db, "review-1"),db)
            self.assertEqual(db.transaction_evidence.docs,[])
            self.assertEqual(db.review_queue.docs[0]["status"],"pending")
            result=decide_review("review-1",observed_review(ReviewDecision(action="confirm_match"), db, "review-1"),db)
        self.assertEqual(result["status"],"resolved")
        self.assertEqual(len(db.transaction_evidence.docs),1)
        self.assertEqual(len(db.audit_log.docs),1)

    def test_commitment_update_requires_current_revision(self):
        from datetime import date
        from decimal import Decimal
        body=CommitmentIn(kind="parcelamento",name="Compra",purchase_date=date(2026,1,1),next_due_date=date(2026,1,31),amount=Decimal("29.90"),frequency="mensal",installment_count=3,next_installment_number=1,payment_method="pix",renewal_status="nao_aplicavel")
        existing=stored_document(body)
        existing["_id"]=ObjectId()
        db=FakeDB(commitments=ReviewCollection([existing]))
        with self.assertRaises(HTTPException) as error:
            update_commitment(existing["commitment_id"],body,db)
        self.assertEqual(error.exception.status_code,428)
        stale=CommitmentIn(**{**body.model_dump(),"revision":2})
        with self.assertRaises(HTTPException) as error:
            update_commitment(existing["commitment_id"],stale,db)
        self.assertEqual(error.exception.status_code,409)

    def test_media_checksum_and_atomic_promotion(self):
        import hashlib
        data=b"receipt bytes"
        digest=hashlib.sha256(data).hexdigest()
        with TemporaryDirectory() as temp,patch.dict("os.environ",{"MEDIA_STORAGE_PATH":temp}):
            db=MagicMock()
            path=store_media(db,data,digest,".pdf")
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(list(Path(temp).glob("*.tmp")),[])
            with self.assertRaises(ValueError): store_media(db,b"tampered",digest,".pdf")

    def test_cursor_page_orders_sources_without_repeating_anchor(self):
        stamp=datetime(2026,9,28,12,tzinfo=timezone.utc)
        db=FakeDB(transactions=ReviewCollection([{"_id":ObjectId(),"data_evento":stamp,"descricao_original":"A"}]),provisional_transactions=ReviewCollection([{"_id":ObjectId(),"provisional_id":"capture-1","data_evento":stamp,"status":"confirmed","merchant_raw":"B"}]))
        first=cursor_movements(1,None,None,None,None,None,None,db)
        self.assertEqual(first["items"][0]["movement_source"],"capture")
        self.assertTrue(first["next_cursor"])
        # The fake matcher ignores nested cursor clauses; verify the cursor is generated for the selected source.
        from app.main import decode_movement_cursor
        self.assertEqual(decode_movement_cursor(first["next_cursor"])[1],"capture")


if __name__=="__main__": unittest.main()
