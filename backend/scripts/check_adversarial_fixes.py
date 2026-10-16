"""A01–A10 regressions with real HTTP, Mongo transactions and controlled races.

All writes use a fresh synthetic database. Production fingerprints are read-only.
"""
import copy, hashlib, json, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.collection import Collection
from observed_client import ObservedTestClient as TestClient
from app.main import app, configured_mongodb_uri
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.auth import require_session
from app.transaction_service import TransactionService
from app.atomic import mongo_snapshot
from app.media_store import store_media, reconcile_media
from app.account_positions import reconcile_capture_transfer
from migrations.backfill_descriptor_keys import backfill

def run(output):
    folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
    os.environ['MEDIA_STORAGE_PATH']=str(folder/'synthetic-media')
    settings=get_settings()
    require_synthetic_target(configured_mongodb_uri(settings))
    mongo=MongoClient(configured_mongodb_uri(settings),tz_aware=True,serverSelectionTimeoutMS=10000)
    name='financial_twin_test_fixes_'+uuid4().hex[:10]
    assert name!=settings.mongodb_database
    db=mongo[name]
    checks=[]
    def check(label,condition,**details):
        checks.append({'name':label,'passed':bool(condition),**details})
        assert condition,(label,details)
    def fingerprints():
        with mongo_snapshot(mongo[settings.mongodb_database]) as (reading,_):
            return {key:{'count':reading[key].count_documents({}),'sha256':hashlib.sha256(json_util.dumps(list(reading[key].find().sort('_id',1)),sort_keys=True).encode()).hexdigest()} for key in ('transactions','obligations','installment_plans','commitments','provisional_transactions','account_balance_snapshots','future_schedule_overrides','deleted_movements')}
    before=fingerprints()
    (folder/'production-before.json').write_text(json.dumps(before,indent=2),encoding='utf8')
    db.capture_batch_locks.insert_one({'_id':'movement-insert','revision':0})
    db.provisional_transactions.create_index('provisional_id',unique=True)
    db.commitments.create_index('commitment_id',unique=True)
    db.commitments.create_index('linked_transactions.provisional_id',unique=True,sparse=True)
    db.commitments.create_index('linked_transactions.transaction_id',unique=True,sparse=True)
    db.transaction_evidence.create_index('event_id',unique=True)
    app.state.db=db;app.state.service=TransactionService(db)
    app.dependency_overrides[require_session]=lambda:True
    http=TestClient(app,raise_server_exceptions=False)
    def request(method,path,body=None):
        return http.request(method,path,json=body)
    def commitment(**changes):
        return {'creation_id':str(uuid4()),'kind':'assinatura','name':'Synthetic service','purchase_date':'2026-09-01','next_due_date':'2026-09-20','amount':'10.00','payment_method':'pix','frequency':'mensal','renewal_status':'automatica',**changes}
    def create(**changes):
        response=request('POST','/v1/commitments',commitment(**changes));assert response.status_code==201,response.text
        return response.json()['commitment_id']
    def capture(identity,**changes):
        row={'provisional_id':identity,'event_id':str(uuid4()),'status':'confirmed','revision':1,'valor':Decimal128('-10.00'),'fluxo':'saida','tipo':'compra','natureza':'COMPRA','merchant_raw':'Synthetic '+identity,'data_evento':datetime(2026,9,20,15,tzinfo=timezone.utc),'created_at':datetime.now(timezone.utc),**changes}
        row['_id']=db.provisional_transactions.insert_one(row).inserted_id
        return row
    def event(**changes):
        return {'event_id':str(uuid4()),'source_type':'manual','source_provider':'synthetic_fixes','captured_at':'2026-09-20T15:00:00.123456Z','raw_payload':{},'parsed_payload':{'amount':'10.00','merchant_raw':'Synthetic '+uuid4().hex,'direction':'expense','event_type':'card_purchase','event_timestamp':'2026-09-20T12:00:00-03:00',**changes}}

    # A05: same key returns the original response, legitimate equal subscriptions stay distinct.
    body=commitment()
    first=request('POST','/v1/commitments',body);again=request('POST','/v1/commitments',body)
    check('A05 replay returns one creation and one audit',first.status_code==again.status_code==201 and first.json()==again.json() and db.commitments.count_documents({})==1 and db.audit_log.count_documents({'event':'commitment_created'})==1)
    changed=request('POST','/v1/commitments',{**body,'amount':'20.00'})
    check('A05 key with different payload conflicts',changed.status_code==409)
    equal=request('POST','/v1/commitments',{**body,'creation_id':str(uuid4())})
    check('A05 separate equal subscriptions remain legitimate',equal.status_code==201 and equal.json()['commitment_id']!=first.json()['commitment_id'])
    absent=dict(body);absent.pop('creation_id')
    check('A05 missing creation identity cannot duplicate',request('POST','/v1/commitments',absent).status_code==428)
    concurrent=commitment(name='Concurrent creation')
    with ThreadPoolExecutor(2) as pool: responses=list(pool.map(lambda _:request('POST','/v1/commitments',concurrent),range(2)))
    if any(r.status_code==409 for r in responses): responses.append(request('POST','/v1/commitments',concurrent))
    check('A05 concurrent create/retry has one identity',all(r.status_code in {201,409} for r in responses) and len({r.json()['commitment_id'] for r in responses if r.status_code==201})==1 and db.commitments.count_documents({'name':'Concurrent creation'})==1)
    rollback=commitment(name='Rollback creation')
    original_insert=Collection.insert_one
    def fail_audit(self,doc,*args,**kwargs):
        if self.name=='audit_log' and doc.get('event')=='commitment_created':raise RuntimeError('synthetic audit failure')
        return original_insert(self,doc,*args,**kwargs)
    with patch.object(Collection,'insert_one',fail_audit):failure=request('POST','/v1/commitments',rollback)
    check('A05 failed audit rolls back creation and command',failure.status_code==500 and db.commitments.count_documents({'name':'Rollback creation'})==0 and db.batch_commands.count_documents({'_id':'commitment-create:'+rollback['creation_id']})==0)
    check('A05 retry after rollback succeeds',request('POST','/v1/commitments',rollback).status_code==201)

    # A02: every editor protects economic identity, including type-only batch propagation.
    capture('linked-edit');cid=create(name='Linked capture')
    linked=request('POST',f'/v1/commitments/{cid}/payment',{'provisional_id':'linked-edit'})
    edited=request('PATCH','/v1/provisional/linked-edit',{'expected_revision':1,'tipo':'pagamento_fatura'})
    check('A02 type-only capture edit cannot neutralize payment',linked.status_code==200 and edited.status_code==409 and db.provisional_transactions.find_one({'provisional_id':'linked-edit'})['tipo']=='compra')
    ledger={'valor':Decimal128('-10.00'),'fluxo':'saida','tipo':'compra','natureza':'COMPRA','data_evento':datetime(2026,9,20,15,tzinfo=timezone.utc),'descricao_original':'Synthetic ledger service','classification':{'migration_reason':'despesa_real'}}
    lid=db.transactions.insert_one(ledger).inserted_id;cid=create(name='Ledger capture')
    linked=request('POST',f'/v1/commitments/{cid}/payment',{'transaction_id':str(lid)})
    edited=request('PATCH',f'/v1/transactions/{lid}/classification',{'tipo':'pagamento_fatura'})
    check('A02 type-only ledger edit cannot neutralize payment',linked.status_code==200 and edited.status_code==409)
    peer={key:value for key,value in ledger.items() if key!='_id'};peer['data_evento']=datetime(2026,8,20,15,tzinfo=timezone.utc);pid=db.transactions.insert_one(peer).inserted_id
    backfill(db,apply=True)
    preview_response=request('GET',f'/v1/transactions/{pid}/classification-match-preview')
    assert preview_response.status_code==200,preview_response.text
    preview=preview_response.json()
    batch=request('PATCH',f'/v1/transactions/{pid}/classification',{'tipo':'pagamento_fatura','apply_similar_classification':True,'classification_preview_token':preview['preview_token']})
    check('A02 batch cannot neutralize a linked peer',batch.status_code==409 and db.transactions.find_one({'_id':pid})['tipo']=='compra')

    # A07 final normalized confirmation contract.
    capture('blank-type')
    edited=request('PATCH','/v1/provisional/blank-type',{'expected_revision':1,'tipo':' '})
    check('A07 blank normalized type rejected',edited.status_code==422 and db.provisional_transactions.find_one({'provisional_id':'blank-type'})['tipo']=='compra')
    zero=capture('zero-confirm',status='provisional',valor=Decimal128('-0.00'))
    check('A07 legacy zero cannot be confirmed',request('POST','/v1/provisional/zero-confirm/decision',{'action':'confirm','expected_revision':1}).status_code==422)
    for label,fields in [('A07 rounded zero',{'amount':'0.004'}),('A06 unknown direction',{'direction':'debit'}),('A04 numeric timestamp',{'event_timestamp':123}),('A04 numeric merchant',{'merchant_raw':17})]:
        bad=event(**fields);response=request('POST','/v1/events',bad)
        check(label+' rejected before raw persistence',response.status_code==422 and db.raw_events.find_one({'event_id':bad['event_id']}) is None)

    # A03 incomplete candidate remains reviewable; valid promotion increments revision atomically.
    target=capture('match-target',status='provisional',tipo=None)
    incoming=capture('match-incoming',status='provisional')
    db.raw_events.insert_one({'event_id':incoming['event_id'],'processing_status':'review_required'})
    db.review_queue.insert_one({'review_id':'review','event_id':incoming['event_id'],'candidate_type':'provisional','candidate_provisional_id':'match-target','status':'pending','match_score':.7})
    rejected=request('POST','/v1/reviews/review/decision',{'action':'confirm_match'})
    check('A03 invalid match rolls back all decisions',rejected.status_code==422 and db.review_queue.find_one({'review_id':'review'})['status']=='pending' and db.transaction_evidence.count_documents({'event_id':incoming['event_id']})==0)
    db.provisional_transactions.update_one({'_id':target['_id']},{'$set':{'tipo':'compra'}})
    accepted=request('POST','/v1/reviews/review/decision',{'action':'confirm_match'})
    stored=db.provisional_transactions.find_one({'_id':target['_id']})
    check('A03 valid match promotes with revision',accepted.status_code==200 and stored['status']=='confirmed' and stored['revision']==2)
    check('A03 stale editor rejected after match',request('PATCH','/v1/provisional/match-target',{'tipo':'compra','expected_revision':1}).status_code==409)
    check('A03 replay creates no second evidence',request('POST','/v1/reviews/review/decision',{'action':'confirm_match'}).status_code==200 and db.transaction_evidence.count_documents({'event_id':incoming['event_id']})==1)

    # A10 public accepted datetime precision survives Mongo persistence and replay.
    body=event();a=request('POST','/v1/events',body);b=request('POST','/v1/events',body)
    check('A10 identical microsecond event replay accepted',a.status_code==b.status_code==202 and db.raw_events.count_documents({'event_id':body['event_id']})==1)

    # A01 real concurrent HTTP edit immediately before source reservation.
    capture('race-payment');cid=create(name='Race capture')
    original_update=Collection.update_one;triggered=[]
    def race(self,query,update,*args,**kwargs):
        if self.name=='provisional_transactions' and query.get('provisional_id')=='race-payment' and set(update.get('$set',{}))=={'updated_at'} and kwargs.get('session') and not triggered:
            triggered.append(True)
            triggered.append(request('PATCH','/v1/provisional/race-payment',{'expected_revision':1,'valor':'-500.00'}).status_code)
        return original_update(self,query,update,*args,**kwargs)
    with patch.object(Collection,'update_one',race):r=request('POST',f'/v1/commitments/{cid}/payment',{'provisional_id':'race-payment'})
    links=db.commitments.find_one({'commitment_id':cid})['linked_transactions']
    editor_won=triggered==[True,200] and r.status_code==409 and not links
    link_won=triggered==[True,409] and r.status_code==200 and len(links)==1 and links[0]['actual_amount']=='10.00'
    check('A01 shared fence permits one winner without stale settlement',editor_won or link_won,http=r.status_code,editor=triggered)
    if editor_won:
        r=request('POST',f'/v1/commitments/{cid}/payment',{'provisional_id':'race-payment'})
        check('A01 fresh retry settles current amount',r.status_code==200 and r.json()['payment']['actual_amount']=='500.00')
    else:
        check('A01 losing stale edit preserves linked actual amount',db.provisional_transactions.find_one({'provisional_id':'race-payment'})['valor']==Decimal128('-10.00'))

    # A09 both old reference spellings, all referencing events and batches.
    data=b'synthetic import';digest=hashlib.sha256(data).hexdigest();path=store_media(db,data,digest,'.csv')
    db.media_assets.update_one({'sha256':digest},{'$set':{'created_at':datetime.now(timezone.utc)-timedelta(hours=2)}})
    for key in ('sha256','file_sha256'):db.raw_events.insert_one({'event_id':'media-'+key,'raw_payload':{key:digest},'media_status':'media_ready'})
    db.capture_batches.insert_one({'batch_id':'media-batch','sha256':digest})
    reconcile_media(db)
    check('A09 import references remain ready instead of orphaned',db.media_assets.find_one({'sha256':digest})['status']=='media_ready')
    path.write_bytes(b'corrupted');reconcile_media(db)
    check('A09 corruption reaches every event and batch',db.raw_events.count_documents({'event_id':{'$in':['media-sha256','media-file_sha256']},'media_status':'media_missing'})==2 and db.capture_batches.find_one({'batch_id':'media-batch'})['media_status']=='media_missing')
    store_media(db,data,digest,'.csv');reconcile_media(db)
    check('A09 restored bytes heal every reference',db.raw_events.count_documents({'event_id':{'$in':['media-sha256','media-file_sha256']},'media_status':'media_ready'})==2 and db.capture_batches.find_one({'batch_id':'media-batch'})['media_status']=='media_ready')

    # A08 21st candidate prevents false unique match; overflow also remains pending.
    origin=ObjectId();destination=ObjectId();db.accounts.insert_many([{'_id':origin,'tipo':'conta_corrente','instituicao':'Itaú'},{'_id':destination,'tipo':'conta_corrente','instituicao':'Santander'}])
    when=datetime(2026,8,20,15,tzinfo=timezone.utc)
    for i in range(21):db.transactions.insert_one({'account_id':origin,'data_evento':when,'valor':Decimal128('-123.45'),'descricao_original':'Transferência Santander' if i in (0,20) else 'Outro favorecido'})
    transfer=capture('transfer',data_evento=when,valor=Decimal128('-123.45'),natureza='TRANSFERENCIA',transfer={'from_account_key':'itau','to_account_key':'santander','reconciliation_status':'aguardando_extratos'})
    result,_=reconcile_capture_transfer(db,transfer)
    check('A08 21st plausible leg blocks automatic match',not result.get('source_transaction_id'))
    db.transactions.delete_many({'account_id':origin})
    db.transactions.insert_many([{'account_id':origin,'data_evento':when,'valor':Decimal128('-123.45'),'descricao_original':'Transferência Santander' if i==0 else 'Outro favorecido'} for i in range(501)])
    result,_=reconcile_capture_transfer(db,transfer)
    check('A08 overflow cannot establish uniqueness',not result.get('source_transaction_id'))

    # A04 permanent poison and transient retries both let later durable events advance.
    older=datetime.now(timezone.utc)-timedelta(minutes=10)
    def pending(fields,number):
        e=event(**fields);e['captured_at']=datetime(2026,9,20,15,tzinfo=timezone.utc);e.update(processing_status='pending',received_at=older-timedelta(seconds=number));return e
    db.raw_events.insert_many([pending({'event_timestamp':123},200-i) for i in range(100)])
    valid=pending({},0);db.raw_events.insert_one(valid)
    app.state.service.recover_pending(100);app.state.service.recover_pending(100)
    check('A04 permanent poison rejected and later valid event recovered',db.raw_events.find_one({'event_id':valid['event_id']})['processing_status']!='pending' and db.raw_events.count_documents({'processing_status':'rejected'})==100)
    transient=[pending({},200-i) for i in range(100)];db.raw_events.insert_many(transient)
    later=pending({},0);db.raw_events.insert_one(later)
    transient_ids={e['event_id'] for e in transient};finish=app.state.service._finish
    def fail_transient(e,session):
        if e['event_id'] in transient_ids:raise RuntimeError('synthetic retryable interruption')
        return finish(e,session)
    import logging
    logging.disable(logging.CRITICAL)
    try:
        with patch.object(app.state.service,'_finish',fail_transient):app.state.service.recover_pending(100);app.state.service.recover_pending(100)
    finally:logging.disable(logging.NOTSET)
    check('A04 transient backoff allows later valid event',db.raw_events.find_one({'event_id':later['event_id']})['processing_status']!='pending' and db.raw_events.count_documents({'event_id':{'$in':list(transient_ids)},'processing_attempts':1,'next_attempt_at':{'$gt':datetime.now(timezone.utc)}})==100)
    after=fingerprints();check('Financial production collections unchanged',before==after)
    report={'database':name,'passed':True,'checks':checks,'production_before':before,'production_after':after}
    (folder/'regressions.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'passed':True,'checks':len(checks),'database':name}))
    http.close();mongo.close()

if __name__=='__main__':run(sys.argv[1])
