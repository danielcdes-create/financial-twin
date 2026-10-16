import io
import logging
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo.errors import WriteError
from app.account_positions import account_positions
from app.capture_batches import BatchReview, BatchCommit, commit, fingerprint
from app.financial_time import local_date
from app.main import (ProvisionalUpdate, update_provisional, cursor_movements,
                      recover_classification_commands)
from app.month_close import view
from app.schemas_document import DocumentExtraction
from app.financial_time import LOCAL_ZONE
from app.services.ai.xai.provider import _document_clock, _post
from app.main import extract_document_data
from types import SimpleNamespace
import json
from app.transaction_service import TransactionService
from test_capture_batches import BatchDB


def fixture(bank='santander', captured=False):
    db=BatchDB();stamp=datetime(2026,10,1,15,tzinfo=timezone.utc)
    accounts={key:ObjectId() for key in ('itau','santander')}
    for key,label in (('itau','Itaú'),('santander','Santander')):
        db.accounts.docs.append({'_id':accounts[key],'instituicao':label,'tipo':'conta_corrente'})
        db.account_balance_snapshots.docs.append({'_id':'balance-'+key,'account_key':key,
            'amount':Decimal128('100.00'),'as_of':stamp-timedelta(days=1)})
    row={'_id':ObjectId(),'account_id':accounts[bank],'data_evento':stamp,'valor':Decimal128('-26.00'),
         'fluxo':'saida','tipo':'compra','categoria':'Alimentação','descricao_original':'PADARIA SINTÉTICA',
         'classification':{'migration_reason':'despesa_real'}}
    if captured:
        row.update({'provisional_id':'existing','event_id':'old','status':'confirmed','revision':1,
            'natureza':'COMPRA','merchant_raw':'PADARIA SINTÉTICA','account_profile_id':bank})
        db.provisional_transactions.docs.append(row)
    else:db.transactions.docs.append(row)
    event={'event_id':str(uuid4()),'source_type':'android_notification','source_provider':'com.itau',
        'captured_at':stamp,'raw_payload':{'package':'com.itau','origin_verification':'exact_package_allowlist'},
        'parsed_payload':{'amount':'26.00','currency':'BRL','merchant_raw':'PADARIA SINTÉTICA',
            'event_timestamp':stamp.isoformat(),'event_type':'card_purchase','direction':'expense'}}
    return db,event,accounts


class AccountIsolationTests(unittest.TestCase):
    def test_reassigned_capture_agrees_in_filters_month_and_positions_and_audit(self):
        db,event,accounts=fixture('itau');db.transactions.docs.clear()
        batch_id=str(uuid4())
        review=BatchReview(account_ref='ledger:'+str(accounts['itau']),rows=[{
            'row_id':1,'date':'2026-10-01','amount':'-26.00','description':'PADARIA SINTÉTICA','kind':'expense','currency':'BRL'}])
        batch={'_id':batch_id,'batch_id':batch_id,'sha256':'a'*64,'status':'draft','complete':True,
               'storage_key':'synthetic:statement','rows':[r.model_dump(mode='json') for r in review.rows]}
        db.capture_batches.docs.append(batch)
        ack=commit(batch_id,BatchCommit(**review.model_dump(),preview_token=fingerprint(batch,review)),db)
        identity=ack['inserted'][0]['provisional_id']
        result=update_provisional(identity,ProvisionalUpdate(expected_revision=1,account_profile_id='santander'),db)
        self.assertTrue(result['persisted'])
        capture=db.provisional_transactions.docs[0]
        self.assertEqual(capture['account_id'],accounts['santander'])
        self.assertEqual(capture['source_account_identity']['account_id'],accounts['itau'])
        self.assertEqual(cursor_movements(50,None,None,None,str(accounts['itau']),None,None,db)['total'],0)
        self.assertEqual(cursor_movements(50,None,None,None,'profile:santander',None,None,db)['total'],1)
        flows={g['id']:g['balance']['flow'] for g in view(db,'2026-10')['groups'] if g.get('balance')}
        self.assertEqual(flows['account:itau'],'0.00');self.assertEqual(flows['account:santander'],'-26.00')
        positions={r['key']:r['balance'] for r in account_positions(db)}
        self.assertEqual((positions['itau'],positions['santander']),(100,74))
        audit=next(r for r in db.audit_log.docs if r['event']=='provisional_transaction_updated')
        self.assertEqual(audit['before']['account_id'],str(accounts['itau']))
        self.assertEqual(audit['after']['account_id'],str(accounts['santander']))

    def test_old_contradictory_identity_uses_explicit_profile_without_rewriting(self):
        db,_,accounts=fixture('santander',True)
        row=db.provisional_transactions.docs[0];row['account_id']=accounts['itau']
        self.assertEqual(cursor_movements(50,None,None,None,str(accounts['itau']),None,None,db)['total'],0)
        flows={g['id']:g['balance']['flow'] for g in view(db,'2026-10')['groups'] if g.get('balance')}
        self.assertEqual(flows['account:itau'],'0.00')
        self.assertEqual(row['account_id'],accounts['itau'])

    def test_notification_cannot_absorb_other_bank_ledger_or_capture(self):
        for captured in (False,True):
            with self.subTest(captured=captured):
                db,event,_=fixture(captured=captured)
                result=TransactionService(db).process(event)
                self.assertNotEqual(result['status'],'matched')
                self.assertEqual(db.transaction_evidence.docs,[])
                self.assertFalse(db.provisional_transactions.docs[-1].get('account_profile_id')=='itau')

    def test_same_bank_similarity_stays_reviewable_and_scoped_reference_matches(self):
        db,event,_=fixture('itau')
        self.assertEqual(TransactionService(db).process(event)['status'],'review_required')
        db,event,_=fixture('itau')
        event['parsed_payload'].update(bank_transaction_ref='purchase-1',account_identity='account-1')
        db.transactions.docs[0].update(bank_transaction_ref='purchase-1',account_identity='account-1',source_provider='com.itau')
        self.assertEqual(TransactionService(db).process(event)['status'],'matched')
        db,event,_=fixture('itau');db.transactions.docs[0].pop('account_id')
        self.assertNotEqual(TransactionService(db).process(event)['status'],'matched')

    def test_known_instrument_requires_consistent_instrument(self):
        for hint in (None,'4321','1234'):
            db,event,_=fixture('itau');event['parsed_payload']['payment_instrument_hint']='1234'
            event['parsed_payload'].update(bank_transaction_ref='purchase-1',account_identity='account-1')
            db.transactions.docs[0].update(bank_transaction_ref='purchase-1',account_identity='account-1',source_provider='com.itau')
            if hint:db.transactions.docs[0]['instrument_hint']=hint
            result=TransactionService(db).process(event)
            self.assertEqual(result['status']=='matched',hint=='1234')


class CaptureClockTests(unittest.TestCase):
    def test_ai_z_without_printed_timezone_is_brasilia_and_printed_offset_stays(self):
        for evidence,expected in ((None,15),('',15),('UTC',12),('+00:00',12),('UTC+02:00',15)):
            source={'document_type':'receipt','datetime':'2026-10-03T15:28:00Z','timezone_evidence':evidence}
            item=DocumentExtraction.model_validate(_document_clock(source))
            self.assertEqual(item.datetime.astimezone(LOCAL_ZONE).hour,expected)

    def test_actual_ai_adapter_and_receipt_ingestion_do_not_shift_printed_clock(self):
        response={'output':[{'content':[{'type':'output_text','text':json.dumps({
            'document_type':'receipt','merchant':'Synthetic merchant','datetime':'2026-10-01T23:40:00Z',
            'timezone_evidence':None,'total':26,'currency':'BRL','items':[]})}]}]}
        remote=io.BytesIO(json.dumps(response).encode())
        with patch('app.services.ai.xai.provider.urllib.request.urlopen',return_value=remote):
            extracted=_post('https://synthetic.invalid','synthetic-test-key',{})
        db=BatchDB();service=TransactionService(db)
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(service=service)))
        with patch('app.main.XAIVisionProvider.extract_image',return_value=extracted),patch('app.main.store_media',return_value='synthetic:image'):
            extract_document_data(b'synthetic-image','image/png','a'*64,'synthetic.png','mobile_capture',db,request)
        row=db.provisional_transactions.docs[0]
        self.assertEqual(row['data_evento'].isoformat(),'2026-10-02T02:40:00+00:00')
        self.assertEqual(local_date(row['data_evento']).isoformat(),'2026-10-01')
    def test_document_without_offset_is_brasilia_and_explicit_offset_is_preserved(self):
        naive=DocumentExtraction(document_type='receipt',datetime='2026-10-01T23:40:00')
        self.assertEqual(naive.datetime.utcoffset(),timedelta(hours=-3))
        explicit=DocumentExtraction(document_type='receipt',datetime='2026-10-02T02:40:00Z')
        self.assertEqual(naive.datetime,explicit.datetime)

    def test_capture_without_offset_keeps_printed_civil_day_near_midnight(self):
        for stamp in ('2026-10-01T00:10:00','2026-10-01T23:40:00','2026-10-02T02:40:00Z'):
            db,event,_=fixture();db.transactions.docs.clear()
            event['source_type']='receipt';event['source_provider']='synthetic-receipt';event['raw_payload']={}
            event['parsed_payload']['event_timestamp']=stamp
            TransactionService(db).process(event)
            row=db.provisional_transactions.docs[0]
            self.assertEqual(local_date(row['data_evento']).isoformat(),'2026-10-01')
            self.assertEqual(row['data_evento'].tzinfo,timezone.utc)
            self.assertEqual(db.raw_events.docs[0]['ingest_payload']['parsed_payload']['event_timestamp'],stamp)

    def test_matching_uses_same_instant_as_capture_for_zone_less_evidence(self):
        db,event,_=fixture('itau')
        event['parsed_payload'].update(bank_transaction_ref='purchase-1',account_identity='account-1')
        db.transactions.docs[0].update(bank_transaction_ref='purchase-1',account_identity='account-1',source_provider='com.itau')
        event['parsed_payload']['event_timestamp']='2026-10-01T12:00:00'
        result=TransactionService(db).process(event)
        self.assertEqual(result['status'],'matched')
        self.assertEqual(db.transaction_evidence.docs[0]['score_components']['time'],1.0)

    def test_manual_review_naive_wall_clock_uses_brasilia(self):
        body=ProvisionalUpdate(data_evento='2026-10-01T23:40:00')
        self.assertEqual(body.data_evento.isoformat(),'2026-10-02T02:40:00+00:00')


class RecoveryPrivacyTests(unittest.TestCase):
    def test_driver_and_validation_errors_never_copy_financial_inputs(self):
        marker='SYNTHETIC-FINANCIAL-SECRET'
        errors=(WriteError(marker,121,{'errInfo':{'consideredValue':marker}}),ValueError(marker),RuntimeError(marker))
        for failure in errors:
            with self.subTest(error=type(failure).__name__):
                db,event,_=fixture();db.transactions.docs.clear()
                db.raw_events.docs.append({**event,'processing_status':'pending',
                    'received_at':datetime.now(timezone.utc)-timedelta(minutes=2)})
                service=TransactionService(db);stream=io.StringIO();handler=logging.StreamHandler(stream)
                logging.getLogger().addHandler(handler)
                try:
                    with patch.object(service,'_finish',side_effect=failure):service.recover_pending()
                finally:logging.getLogger().removeHandler(handler)
                row=db.raw_events.docs[0]
                self.assertNotIn(marker,stream.getvalue());self.assertNotIn(marker,row['processing_error'])
                self.assertEqual(row['event_id'],event['event_id']);self.assertEqual(row['processing_attempts'],1)
                if not isinstance(failure,ValueError):
                    self.assertEqual(row['processing_status'],'pending');self.assertIn('next_attempt_at',row)
                if isinstance(failure,WriteError):self.assertIn('code=121',row['processing_error'])

    def test_classification_recovery_is_sanitized_and_retains_retry(self):
        db=BatchDB();db.batch_commands.docs.append({'_id':'synthetic','status':'pending','attempts':0,
            'created_at':datetime.now(timezone.utc)-timedelta(minutes=2)})
        marker='PRIVATE-PEER-AMOUNT';stream=io.StringIO();handler=logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        try:
            with patch('app.main.run_classification_command',side_effect=WriteError(marker,121,{'consideredValue':marker})):
                recover_classification_commands(db)
        finally:logging.getLogger().removeHandler(handler)
        row=db.batch_commands.docs[0]
        self.assertNotIn(marker,stream.getvalue());self.assertEqual(row['last_error'],'WriteError:code=121')
        self.assertEqual((row['status'],row['attempts']),('pending',1))


if __name__=='__main__':unittest.main()
