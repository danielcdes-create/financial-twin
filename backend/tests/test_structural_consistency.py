"""Synthetic reproductions for identities, ACKs and recoverable projections."""
import copy
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).parents[1]))
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from app.transaction_service import TransactionService, EventConflict
from app.operation_identity import bind_upload, complete_upload
from app.mutation_identity import durable_mutation
from app.main import (ProvisionalUpdate, ProvisionalAction, update_provisional, get_provisional,
                      extract_document_sync, import_statement_sync, finish_receipt)
from app.schemas_document import DocumentExtraction
from app.upload_worker import persisted_event
from test_ingestion import FakeDB, Collection, event
from test_capture_batches import BatchDB
from test_architecture_failures import rollback_fake, FailsOnce
from bson.decimal128 import Decimal128


class StructuralConsistencyTests(unittest.TestCase):
    def setUp(self):self.stamp=datetime(2026,10,1,12,tzinfo=timezone.utc)

    def test_original_notification_replay_survives_mutable_reinterpretation(self):
        db=FakeDB();service=TransactionService(db);payload=event('original','manual',self.stamp)
        service.process(payload)
        db.raw_events.docs[0]['parsed_payload']={'amount':99,'merchant_raw':'Human correction'}
        db.raw_events.docs[0]['parser_version']='reinterpreted-v2'
        self.assertTrue(service.process(payload)['duplicate'])
        self.assertEqual(len(db.provisional_transactions.docs),1)
        with self.assertRaises(EventConflict):service.process({**payload,'raw_payload':{'text':'Other original'}})

    def test_unparsed_event_has_a_visible_review_without_fabricated_money(self):
        db=FakeDB();service=TransactionService(db)
        payload=event(str(uuid4()),'android_notification',self.stamp);payload['parsed_payload']={}
        self.assertEqual(service.process(payload)['status'],'needs_parse')
        self.assertEqual(db.review_queue.docs[0]['reason'],'event_needs_parse')
        self.assertEqual(db.review_queue.docs[0]['event_id'],payload['event_id'])
        self.assertEqual(db.provisional_transactions.docs,[])
        self.assertTrue(service.process(payload)['duplicate'])
        self.assertEqual(len(db.review_queue.docs),1)

    def test_legacy_notification_replay_uses_original_evidence_without_migration(self):
        db=FakeDB();service=TransactionService(db);payload=event('legacy-original','android_notification',self.stamp)
        service.process(payload)
        stored=db.raw_events.docs[0]
        stored.pop('ingest_token');stored.pop('ingest_payload')
        stored['parsed_payload']={'amount':99,'merchant_raw':'Reparsed notification'}
        stored['parser_version']='new-parser'
        self.assertTrue(service.process(payload)['duplicate'])
        self.assertEqual(stored['parsed_payload']['amount'],99)
        with self.assertRaises(EventConflict):service.process({**payload,'raw_payload':{'text':'Other notification'}})

    def test_notification_replay_with_mongodb_naive_utc_keeps_review(self):
        db=FakeDB();service=TransactionService(db)
        payload=event('mongo-notification','android_notification',self.stamp)
        service.process(payload)
        stored=db.raw_events.docs[0]
        # PyMongo decodes BSON dates as naive UTC by default, at ms precision.
        stored['captured_at']=self.stamp.replace(tzinfo=None)
        stored['parsed_payload']={'amount':99,'merchant_raw':'Human correction'}
        stored['processing_status']='confirmed'
        before=copy.deepcopy(stored)
        self.assertTrue(service.process(payload)['duplicate'])
        self.assertEqual(stored,before)
        with self.assertRaises(EventConflict):
            service.process({**payload,'captured_at':self.stamp.replace(second=1)})
        with self.assertRaises(EventConflict):
            service.process({**payload,'source_provider':'other.bank'})
        with self.assertRaises(EventConflict):
            service.process({**payload,'raw_payload':{'text':'Changed evidence'}})

    def mismatch(self):
        payload=event(str(uuid4()),'receipt',self.stamp)
        payload['raw_payload']['validation']={'review_required':True,'total':26.,'items_total':25.}
        return payload

    def test_receipt_discrepancy_is_projected_with_review_in_same_transaction(self):
        db=FakeDB();service=TransactionService(db);payload=self.mismatch()
        db.review_queue=FailsOnce();service.review=db.review_queue
        with patch('app.transaction_service.mongo_transaction',rollback_fake):
            with self.assertRaises(RuntimeError):service.process(payload)
            self.assertEqual(db.raw_events.docs[0]['processing_status'],'pending')
            self.assertEqual(db.provisional_transactions.docs,[])
            service.process(payload)
        self.assertEqual(db.raw_events.docs[0]['processing_status'],'review_required')
        self.assertEqual(db.review_queue.docs[0]['reason'],'item_total_mismatch')
        self.assertEqual(db.review_queue.docs[0]['provisional_id'],db.provisional_transactions.docs[0]['provisional_id'])

    def test_receipt_mismatch_never_attaches_automatically_to_a_financial_record(self):
        db=FakeDB();payload=self.mismatch()
        db.transactions.docs=[{'_id':'posted','valor':Decimal128('-26.00'),'data_evento':self.stamp,'estabelecimento_original':'Padaria ABC','tipo':'compra'}]
        result=TransactionService(db).process(payload)
        self.assertEqual(result['status'],'review_required')
        self.assertEqual(db.transaction_evidence.docs,[])
        self.assertEqual(db.review_queue.docs[0]['candidate_transaction_id'],'posted')

    def test_resolved_receipt_is_not_reopened_on_reupload(self):
        db=FakeDB();payload=self.mismatch();service=TransactionService(db);result=service.process(payload)
        db.provisional_transactions.docs[0]['status']='confirmed'
        db.raw_events.docs[0].pop('processing_error',None)
        db.raw_events.docs[0]['processing_status']='confirmed'
        db.review_queue.docs[0].update({'status':'resolved','decision':'keep_separate'})
        finish_receipt(db,payload['event_id'],result,{},payload['raw_payload']['validation'])
        self.assertEqual(db.review_queue.docs[0]['status'],'resolved')
        self.assertEqual(db.raw_events.docs[0]['processing_status'],'confirmed')

    def test_atomic_edit_and_confirm_replays_before_stale_revision_check(self):
        db=BatchDB();service=TransactionService(db);payload=event(str(uuid4()),'manual',self.stamp)
        service.process(payload);identity=db.provisional_transactions.docs[0]['provisional_id']
        body=ProvisionalUpdate(request_id=uuid4(),expected_revision=1,valor='-27.00',action='confirm')
        result=update_provisional(identity,body,db)
        self.assertTrue(result['persisted']);self.assertEqual(result['item']['status'],'confirmed')
        audits=len(db.audit_log.docs)
        self.assertEqual(update_provisional(identity,body,db),result)
        self.assertEqual(len(db.audit_log.docs),audits)
        with self.assertRaises(HTTPException) as error:update_provisional(identity,body.model_copy(update={'valor':Decimal('-28')}),db)
        self.assertEqual(error.exception.status_code,409)

    def test_direct_read_returns_terminal_capture_and_original_revision(self):
        db=BatchDB();db.provisional_transactions.docs=[{'provisional_id':'terminal','status':'cancelled','revision':9}]
        current=get_provisional('terminal',db)['item']
        self.assertEqual((current['status'],current['revision']),('cancelled',9))
        with self.assertRaises(HTTPException):get_provisional('missing',db)

    def test_upload_ack_belongs_to_event_bytes_and_purpose(self):
        db=BatchDB();identity=uuid4();digest='a'*64
        self.assertIsNone(bind_upload(db,identity,'document',digest))
        ack=complete_upload(db,identity,'document',digest,{'persisted':True,'processing':{'event_id':'durable-server','persisted':True}})
        self.assertEqual(ack['event_id'],str(identity));self.assertEqual(ack['processing']['event_id'],str(identity))
        self.assertEqual(ack['processing']['persisted_event_id'],'durable-server')
        self.assertEqual(bind_upload(db,identity,'document',digest),ack)
        for kind,sha in [('document','b'*64),('statement-import',digest)]:
            with self.assertRaises(HTTPException) as error:bind_upload(db,identity,kind,sha)
            self.assertEqual(error.exception.status_code,409)

    def test_lost_document_reply_replays_without_ai_or_new_capture(self):
        db=BatchDB();service=TransactionService(db);identity=uuid4()
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db,service=service)))
        def upload():return UploadFile(filename='voice.txt',file=io.BytesIO(b'synthetic spoken text'),headers=Headers({'content-type':'text/plain'}))
        extraction=DocumentExtraction(document_type='receipt',total=26,currency='BRL',merchant='Padaria ABC')
        with patch('app.main.store_media',return_value='retained-original.txt'),patch('app.main.XAITextProvider.extract_text',return_value=extraction) as ai:
            first=extract_document_sync(upload(),'mobile_voice',db,request,event_id=identity)
            second=extract_document_sync(upload(),'mobile_voice',db,request,event_id=identity)
        self.assertEqual(first,second);ai.assert_called_once()
        self.assertEqual(len(db.raw_events.docs),1);self.assertEqual(len(db.provisional_transactions.docs),1)

    def test_invalid_csv_does_not_post_a_valid_prefix(self):
        db=BatchDB();service=TransactionService(db)
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db,service=service)))
        data=b'data;descricao;valor;fluxo\n01/10/2026;Valid synthetic;-10,00;saida\n02/10/2026;Invalid synthetic;?;saida\n'
        upload=UploadFile(filename='synthetic.csv',file=io.BytesIO(data))
        with patch('app.main.store_media',return_value='retained-synthetic.csv'),self.assertRaises(HTTPException) as error:
            import_statement_sync(upload,'synthetic',request,event_id=uuid4())
        self.assertEqual(error.exception.status_code,422)
        self.assertEqual(db.raw_events.docs,[]);self.assertEqual(db.provisional_transactions.docs,[])

    def test_rejected_event_never_authorizes_retiring_a_file(self):
        db=BatchDB();db.raw_events.docs=[{'event_id':'rejected','processing_status':'rejected'}]
        with self.assertRaises(HTTPException) as error:persisted_event(db,'rejected')
        self.assertEqual(error.exception.status_code,422)

    def test_legacy_receipt_mismatch_adds_reason_without_erasing_match_context(self):
        db=BatchDB();payload=event('legacy-receipt','receipt',self.stamp);service=TransactionService(db);result=service.process(payload)
        candidate={'review_id':'observed-candidate','_id':'review-candidate','event_id':payload['event_id'],'status':'pending',
                   'candidate_transaction_id':'original-candidate','reason':'possible_match'}
        db.review_queue.docs.append(candidate)
        finish_receipt(db,payload['event_id'],result,{}, {'review_required':True,'total':26.,'items_total':25.})
        self.assertEqual(candidate['reason'],'possible_match')
        self.assertEqual(candidate['candidate_transaction_id'],'original-candidate')
        self.assertEqual(candidate['additional_reasons'],['item_total_mismatch'])
        self.assertEqual(db.raw_events.docs[0]['processing_status'],'review_required')

    def test_cancel_cannot_hide_capture_that_still_supports_evidence(self):
        from app.main import decide_provisional
        db=BatchDB();service=TransactionService(db);payload=event(str(uuid4()),'manual',self.stamp)
        service.process(payload);identity=db.provisional_transactions.docs[0]['provisional_id']
        db.transaction_evidence.docs.append({'event_id':'supporting-evidence','provisional_id':identity})
        with self.assertRaises(HTTPException) as error:decide_provisional(identity,ProvisionalAction(request_id=uuid4(),expected_revision=1,action='cancel'),db)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(db.provisional_transactions.docs[0]['status'],'provisional')

    def test_concurrent_operation_is_retryable_and_header_is_readable_on_web(self):
        from pymongo.errors import OperationFailure
        from fastapi.testclient import TestClient
        from app.main import app
        @durable_mutation('synthetic-conflict',())
        def conflict(body,db):raise OperationFailure('synthetic concurrent write',code=112)
        with self.assertRaises(HTTPException) as error:conflict(ProvisionalAction(request_id=uuid4(),action='confirm'),BatchDB())
        self.assertEqual(error.exception.headers['X-Operation-Pending'],'1')
        response=TestClient(app).get('/v1/reviews',headers={'Origin':'http://127.0.0.1:19006'})
        self.assertIn('X-Operation-Pending',response.headers['access-control-expose-headers'])


if __name__=='__main__':unittest.main()
