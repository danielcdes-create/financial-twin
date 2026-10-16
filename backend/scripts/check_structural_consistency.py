"""HTTP, restart, lost-reply and concurrency probes; local synthetic DB only."""
import io
import json
import os
import sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.collection import Collection
from app.main import app, configured_mongodb_uri, review_view
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.auth import require_session
from app.transaction_service import TransactionService
from app.source_tokens import source_token
from app.schemas_document import DocumentExtraction


def run(output):
    uri=configured_mongodb_uri(get_settings())
    require_synthetic_target(uri)  # before authentication or database creation
    target=Path(output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    os.environ['MEDIA_STORAGE_PATH']=str(target.parent/'structural-synthetic-media')
    client=MongoClient(uri,tz_aware=True,serverSelectionTimeoutMS=10000)
    name='financial_twin_test_structural_'+uuid4().hex[:10]
    db=client[name]
    app.state.db=db;app.state.service=TransactionService(db)
    app.dependency_overrides[require_session]=lambda:True
    http=TestClient(app,raise_server_exceptions=False)
    checks=[]
    def check(label,condition):
        assert condition,label
        checks.append({'check':label,'passed':True})
    def call(method,path,body=None,status=200):
        response=http.request(method,path,json=body)
        assert response.status_code==status,(method,path,response.status_code,response.text[:600])
        return response.json() if status!=500 else {'status':500}
    def document(identity,data=b'synthetic spoken receipt',filename='voice.txt'):
        return http.post('/v1/documents/extract',data={'event_id':identity,'source_provider':'mobile_voice'},
            files={'file':(filename,data,'text/plain')})
    try:
        stamp=datetime(2026,10,1,12,tzinfo=timezone.utc)
        db.capture_batch_locks.insert_one({'_id':'movement-insert','revision':0})
        account=ObjectId();db.accounts.insert_one({'_id':account,'instituicao':'Itaú','tipo':'conta_corrente'})
        receipt=DocumentExtraction(document_type='receipt',total=26,currency='BRL',merchant='Padaria sintética')
        identity=str(uuid4())
        with patch('app.main.XAITextProvider.extract_text',return_value=receipt) as ai:
            first=document(identity);check('document ACK includes immutable event/file identity',first.status_code==200 and first.json()['event_id']==identity and len(first.json()['file_sha256'])==64)
            ack=first.json();raw_id=ack['processing']['persisted_event_id']
            app.state.service=TransactionService(db)  # simulated process restart
            again=document(identity)
            check('lost response and restart replay without provider or extra capture',again.json()==ack and ai.call_count==1 and db.raw_events.count_documents({})==1 and db.provisional_transactions.count_documents({})==1)
            check('same upload id cannot replace immutable original bytes',document(identity,b'other synthetic bytes').status_code==409)
            renamed=document(str(uuid4()),filename='same-original-renamed.txt')
            check('renamed original with new local id recognizes original event',renamed.status_code==200 and db.raw_events.count_documents({})==1 and ai.call_count==1)
        capture=db.provisional_transactions.find_one({'event_id':raw_id});pid=capture['provisional_id']
        body={'request_id':str(uuid4()),'expected_revision':capture['revision'],'valor':'-27.00','action':'confirm'}
        confirmed=call('PATCH','/v1/provisional/'+pid,body)
        check('edit and confirmation share one persisted command',confirmed['persisted'] and confirmed['request_id']==body['request_id'] and confirmed['provisional_id']==pid and confirmed['item']['status']=='confirmed')
        count=db.audit_log.count_documents({})
        check('replayed edit confirmation returns original ACK before stale revision check',call('PATCH','/v1/provisional/'+pid,body)==confirmed and db.audit_log.count_documents({})==count)
        call('PATCH','/v1/provisional/'+pid,{**body,'valor':'-28.00'},409)
        check('same mutation id with different payload is rejected',db.provisional_transactions.find_one({'provisional_id':pid})['valor']==Decimal128('-27.00'))
        current=call('GET','/v1/provisional/'+pid)['item']
        check('direct capture read exposes terminal status revision and source token',current['status']=='confirmed' and current['revision']>1 and len(current['source_token'])==64)
        call('PATCH','/v1/provisional/'+pid,{'request_id':str(uuid4()),'expected_revision':1,'valor':'-29.00'},409)
        check('stale draft does not overwrite current confirmed source',db.provisional_transactions.find_one({'provisional_id':pid})['valor']==Decimal128('-27.00'))

        pending=str(uuid4())
        incomplete={'event_id':pending,'source_type':'android_notification','source_provider':'synthetic-bank','captured_at':stamp.isoformat(),'raw_payload':{'text':'Synthetic notification without recognized value'},'parsed_payload':{}}
        received=call('POST','/v1/events',incomplete,202)
        review=db.review_queue.find_one({'event_id':pending})
        check('unparsed event is acknowledged and visible as a review without invented amount',received['persisted'] and received['status']=='needs_parse' and review['reason']=='event_needs_parse' and not db.provisional_transactions.find_one({'event_id':pending}))
        view=review_view(db,review)
        discarded=call('POST','/v1/reviews/'+review['review_id']+'/decision',{'action':'discard','request_id':str(uuid4()),**{key:view[key] for key in ('review_token','source_token','candidate_token')}})
        check('discard resolves unparsed review while retaining its source bytes/text',discarded['persisted'] and db.raw_events.find_one({'event_id':pending})['raw_payload']==incomplete['raw_payload'] and db.review_queue.find_one({'event_id':pending})['status']=='resolved')
        check('notification replay cannot resurrect resolved pending state',call('POST','/v1/events',incomplete,202)['status']=='cancelled' and db.review_queue.count_documents({'event_id':pending,'status':'pending'})==0)

        # A mutable parsing projection cannot change the immutable resend identity.
        db.raw_events.update_one({'event_id':pending},{'$set':{'parsed_payload':{'amount':99,'merchant_raw':'Human reviewed source'},'parser_version':'reparsed-v9'}})
        check('immutable ingestion command survives parsing projection mutation',call('POST','/v1/events',incomplete,202)['status']=='cancelled')
        call('POST','/v1/events',{**incomplete,'raw_payload':{'text':'Different original'}},409)
        check('altered original still conflicts after parsing mutation',db.raw_events.count_documents({'event_id':pending})==1)

        csv=b'data;descricao;valor;fluxo;moeda\n01/10/2026;Valid synthetic;-10,00;saida;BRL\n02/10/2026;Invalid synthetic;?;saida;BRL\n'
        before=db.raw_events.count_documents({})
        response=http.post('/v1/import',data={'event_id':str(uuid4())},files={'file':('synthetic.csv',csv,'text/csv')})
        check('CSV validates all rows before posting a valid prefix',response.status_code==422 and db.raw_events.count_documents({})==before)
        good=b'data;descricao;valor;fluxo;moeda\n01/10/2026;CSV synthetic unique;-10,00;saida;BRL\n'
        csv_id=str(uuid4())
        def csv_send():return http.post('/v1/import',data={'event_id':csv_id},files={'file':('synthetic.csv',good,'text/csv')})
        initial=csv_send();replay=csv_send()
        check('CSV ACK and replay belong to immutable original local event',initial.status_code==200 and initial.json()['persisted'] and initial.json()['event_id']==csv_id and initial.json()==replay.json())

        atomic_csv=b'data;descricao;valor;fluxo;moeda\n01/10/2026;CSV first atomic synthetic;-17,31;saida;BRL\n02/10/2026;CSV second atomic synthetic;-18,43;saida;BRL\n'
        atomic_csv_id=str(uuid4());raw_count=db.raw_events.count_documents({});capture_count=db.provisional_transactions.count_documents({})
        original_insert=Collection.insert_one
        def fail_second_import(collection,document,*args,**kwargs):
            if collection.name=='raw_events' and document.get('source_type')=='csv' and document.get('raw_payload',{}).get('row_number')==3:
                raise RuntimeError('synthetic second imported event failure')
            return original_insert(collection,document,*args,**kwargs)
        def atomic_csv_send():return http.post('/v1/import',data={'event_id':atomic_csv_id},files={'file':('atomic.csv',atomic_csv,'text/csv')})
        with patch.object(Collection,'insert_one',fail_second_import):failed=atomic_csv_send()
        command=db.batch_commands.find_one({'_id':'upload:'+atomic_csv_id})
        check('second CSV row failure rolls back every row and final ACK together',failed.status_code==500 and db.raw_events.count_documents({})==raw_count and db.provisional_transactions.count_documents({})==capture_count and command['status']=='awaiting_upload' and not command.get('result'))
        app.state.service=TransactionService(db)
        recovered=atomic_csv_send();replayed=atomic_csv_send()
        check('CSV retry after interrupted transaction and restart commits exactly once',recovered.status_code==200 and recovered.json()['accepted']==2 and recovered.json()==replayed.json() and db.raw_events.count_documents({})==raw_count+2)

        # Confirmation, ACK command and audit all roll back when the last audit write fails.
        raw={'event_id':str(uuid4()),'source_type':'manual','source_provider':'synthetic','captured_at':stamp,
            'raw_payload':{'text':'Rollback synthetic'},'parsed_payload':{'amount':19,'merchant_raw':'Rollback unique synthetic','event_timestamp':stamp,'direction':'expense','event_type':'card_purchase'}}
        processed=app.state.service.process(raw);rollback_id=processed['provisional_id']
        atomic={'request_id':str(uuid4()),'expected_revision':1,'valor':'-20.00','action':'confirm'}
        original_insert=Collection.insert_one
        def fail_audit(collection,document,*args,**kwargs):
            if collection.name=='audit_log' and document.get('event')=='provisional_transaction_decision':raise RuntimeError('synthetic last audit failure')
            return original_insert(collection,document,*args,**kwargs)
        with patch.object(Collection,'insert_one',fail_audit):call('PATCH','/v1/provisional/'+rollback_id,atomic,500)
        unchanged=db.provisional_transactions.find_one({'provisional_id':rollback_id})
        check('failure rolls back edit confirmation ACK command and review together',unchanged['revision']==1 and unchanged['valor']==Decimal128('-19.00') and not db.batch_commands.find_one({'_id':'mutation:'+atomic['request_id']}))
        call('PATCH','/v1/provisional/'+rollback_id,atomic)
        check('same command recovers after connection-equivalent aborted write',db.provisional_transactions.find_one({'provisional_id':rollback_id})['status']=='confirmed')

        managed=call('POST','/v1/commitments',{'creation_id':str(uuid4()),'kind':'assinatura','name':'Commitment synthetic','purchase_date':'2026-01-01','next_due_date':'2026-10-01','amount':'33.21','frequency':'mensal','payment_method':'pix','renewal_status':'automatica'},201)
        check('creation commitment ACK echoes immutable creation identity',managed['persisted'] and len(managed['creation_id'])==36)
        transaction=db.transactions.insert_one({'account_id':account,'data_evento':stamp,'valor':Decimal128('-33.21'),'fluxo':'saida','descricao_original':'Commitment synthetic','classification':{'migration_reason':'despesa_real'}}).inserted_id
        base='/v1/commitments/'+managed['commitment_id']+'/payment'
        call('POST',base,{'transaction_id':str(transaction)},428)
        source=db.transactions.find_one({'_id':transaction})
        payment={'transaction_id':str(transaction),'expected_revision':managed['revision'],'expected_source_token':source_token(source),'request_id':str(uuid4())}
        call('POST',base,{**payment,'expected_revision':managed['revision']+1},409)
        linked=call('POST',base,payment)
        check('commitment source/agenda observed versions required and exact replay accepted',linked['persisted'] and call('POST',base,payment)==linked and len(db.commitments.find_one({'commitment_id':managed['commitment_id']})['linked_transactions'])==1)
        check('commitment link advances schedule once without creating a cash entry',db.transactions.count_documents({})==1 and db.commitments.find_one({'commitment_id':managed['commitment_id']})['next_due_date'].month==11)

        month=call('GET','/v1/month-close/2026-08');item=month['groups'][0]['items'][0]
        marker={'request_id':str(uuid4()),'expected_revision':month['revision'],'expected_source_token':item['source_token'],'status':'pending','note':'Synthetic saved note'}
        month_path='/v1/month-close/2026-08/items/'+item['id']
        saved=call('PATCH',month_path,marker)
        check('monthly checklist replay recovers confirmed marker before revision conflict',saved['persisted'] and call('PATCH',month_path,marker)==saved)

        # Two sessions race the same observed revision. Exactly one new command may win.
        race=db.provisional_transactions.find_one({'provisional_id':rollback_id});start_revision=race['revision']
        commands=[{'request_id':str(uuid4()),'expected_revision':start_revision,'detalhe':'Synthetic concurrent edit '+str(index)} for index in range(2)]
        def race_write(body):return http.patch('/v1/provisional/'+rollback_id,json=body).status_code
        with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(race_write,commands))
        check('concurrent different commands serialize one winner and one explicit conflict',sorted(statuses)==[200,409] and db.provisional_transactions.find_one({'provisional_id':rollback_id})['revision']==start_revision+1)

        # Auth invalidation prevents any old command or read from crossing sessions.
        app.dependency_overrides.pop(require_session,None)
        denied=http.get('/v1/provisional/'+pid)
        denied_write=http.patch('/v1/provisional/'+pid,json=body)
        check('session removal rejects reads and replayed writes before state access',denied.status_code==401 and denied_write.status_code==401)
        app.dependency_overrides[require_session]=lambda:True
        report={'database':name,'uri':'loopback replica set','checks':checks,'passed':len(checks),'financial_target_written':False}
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':len(checks),'output':str(target),'database':name}),flush=True)
    finally:
        app.dependency_overrides.pop(require_session,None)
        client.close()


if __name__=='__main__':run(sys.argv[1])
