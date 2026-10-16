"""Adversarial acceptance for C02-C13/C19/C20. All writes use disposable local DBs."""
import asyncio,copy,hashlib,json,os,sys,threading,time
from pathlib import Path
from datetime import datetime,timezone,date,timedelta
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import ObjectId,json_util
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.collection import Collection
from fastapi.testclient import TestClient
from app.main import app,configured_mongodb_uri,review_view,enrich_notification_event
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.source_tokens import source_token
from app.auth import require_session
from app.transaction_service import TransactionService
from app.financial_time import day_start
from app.economic_policy import effects
from app.schemas_document import DocumentExtraction
from app.media_store import store_media
from app.capture_batches import BatchCommit,BatchReview,analyze
from scripts.backup_restore import backup,restore


def run(output):
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True)
    settings=get_settings();uri=configured_mongodb_uri(settings);require_synthetic_target(uri)
    os.environ['MEDIA_STORAGE_PATH']=str(out/('original-media-'+uuid4().hex[:8]));get_settings.cache_clear()
    mongo=MongoClient(uri,tz_aware=True,serverSelectionTimeoutMS=6000)
    name='financial_twin_test_C_'+uuid4().hex[:8];db=mongo[name];restored_name=None
    original_db=getattr(app.state,'db',None);original_service=getattr(app.state,'service',None)
    app.state.db=db;app.state.service=TransactionService(db);old_override=app.dependency_overrides.get(require_session)
    app.dependency_overrides[require_session]=lambda:True
    http=TestClient(app,raise_server_exceptions=False);results=[]
    def check(identity,label,passed,**details):
        results.append({'id':identity,'scenario':label,'passed':bool(passed),**details})
        (out/'results.json').write_text(json.dumps(results,indent=2,default=str,ensure_ascii=False),encoding='utf8')
        assert passed,(identity,label,details)
    def request(method,path,body=None,**kwargs):
        response=http.request(method,path,json=body,**kwargs)
        try:value=response.json()
        except ValueError:value={'detail':response.text[:200]}
        return response.status_code,value
    def clear():
        for collection in db.list_collection_names():db[collection].delete_many({})
        db.capture_batch_locks.insert_one({'_id':'movement-insert','revision':0})
    def capture(identity,**values):
        row={'provisional_id':identity,'event_id':str(uuid4()),'revision':1,'status':'provisional','valor':Decimal128('-10.00'),'fluxo':'saida','tipo':'compra','natureza':'COMPRA','merchant_raw':'Synthetic '+identity,'data_evento':day_start(date(2026,9,20)),'created_at':datetime.now(timezone.utc),**values}
        row['_id']=db.provisional_transactions.insert_one(row).inserted_id;return row
    def balance_body(amount,token=None,**extra):
        if token is None:token=source_token(db.account_balance_snapshots.find_one({'account_key':'itau'}))
        return {'amount':amount,'as_of_date':'2026-09-10','expected_token':token,'request_id':str(uuid4()),**extra}
    def ledger():
        now=datetime.now(timezone.utc)
        row={'_id':ObjectId(),'external_key':str(uuid4()),'schema_version':2,'account_id':ObjectId(),'data_evento':now,'competencia':'2026-10','valor':Decimal128('-10.00'),'moeda':'BRL','fluxo':'saida','tipo':'compra','descricao_original':'Synthetic strict ledger','categoria':'Inicial','localidade_contexto':'nao_determinado','localidade_real_validada':False,'origem_localidade':'nao_informada','source':{'source_record_id':ObjectId(),'identificador_original':'synthetic','fonte':'audit'},'status_conciliacao':'pendente','classification':{'migration_reason':'despesa_real'},'created_at':now,'updated_at':now}
        db.transactions.insert_one(row);return row
    try:
        clear();initial=balance_body('100.00');first=request('PUT','/v1/account-positions/itau/balance-reference',initial)
        check('C02','initial reference observed explicitly',first[0]==200,status=first[0])
        token=first[1]['item']['balance_reference_token']
        fresh=balance_body('200.00',token,as_of_date='2026-09-20');new=request('PUT','/v1/account-positions/itau/balance-reference',fresh)
        stale=request('PUT','/v1/account-positions/itau/balance-reference',balance_body('90.00',token))
        check('C02','two editors: old token cannot overwrite new balance',new[0]==200 and stale[0]==409 and str(db.account_balance_snapshots.find_one({})['amount'])=='200.00',statuses=[new[0],stale[0]])
        replay=request('PUT','/v1/account-positions/itau/balance-reference',fresh)
        check('C02','uncertain ACK retry returns identical saved response',replay==new and db.audit_log.count_documents({'event':'account_balance_reference_set'})==2)
        changed=request('PUT','/v1/account-positions/itau/balance-reference',{**fresh,'amount':'201'})
        missing=request('PUT','/v1/account-positions/itau/balance-reference',{'amount':'300'})
        check('C02','identity payload collision and old clients rejected',changed[0]==409 and missing[0]==428,statuses=[changed[0],missing[0]])
        # Keep the production validator, never add a revision or token to historical ledger rows.
        clear();db.drop_collection('transactions')
        db.create_collection('transactions',**json_util.loads((Path(__file__).parents[1]/'tests/fixtures/transactions-validator.json').read_text(encoding='utf8')))
        row=ledger();observed=source_token(db.transactions.find_one({'_id':row['_id']}));path=f"/v1/transactions/{row['_id']}/classification"
        fresh=request('PATCH',path,{'expected_token':observed,'categoria':'Atualizada','natureza':'TRANSFERENCIA'})
        stale=request('PATCH',path,{'expected_token':observed,'categoria':'Inicial','natureza':'COMPRA','detalhe':'Old editor'})
        final=db.transactions.find_one({'_id':row['_id']})
        check('C03','strict ledger preserves financial classification against old editor',fresh[0]==200 and stale[0]==409 and effects(final)['role']=='neutral' and 'source_token' not in final and 'revision' not in final,statuses=[fresh[0],stale[0]])
        check('C03','missing token rejected without changes',request('PATCH',path,{'categoria':'Old client'})[0]==428)
        # Every candidate financial field invalidates a decision made from the previous preview.
        db.drop_collection('transactions')
        for field,value in [('valor',Decimal128('-100.00')),('data_evento',day_start(date(2026,9,21))),('natureza','TARIFA_ENCARGO'),('account_profile_id','santander')]:
            clear();source=capture('source');candidate=capture('candidate',status='confirmed')
            db.raw_events.insert_one({'event_id':source['event_id'],'source_type':'manual','captured_at':source['data_evento']})
            review={'review_id':'review','event_id':source['event_id'],'provisional_id':'source','candidate_type':'provisional','candidate_provisional_id':'candidate','match_score':.9,'status':'pending','created_at':datetime.now(timezone.utc)}
            review['_id']=db.review_queue.insert_one(review).inserted_id
            view=review_view(db,review);body={'action':'confirm_match',**{key:view[key] for key in ('review_token','source_token','candidate_token')}}
            edit=request('PATCH','/v1/provisional/candidate',{'expected_revision':1,field:str(value) if isinstance(value,Decimal128) else value.isoformat() if isinstance(value,datetime) else value})
            stale=request('POST','/v1/reviews/review/decision',body)
            check('C04','candidate '+field+' changed after preview',edit[0]==200 and stale[0]==409 and db.transaction_evidence.count_documents({})==0 and db.provisional_transactions.find_one({'provisional_id':'source'})['status']=='provisional',statuses=[edit[0],stale[0]])
            fresh_view=review_view(db,db.review_queue.find_one({'review_id':'review'}))
            refreshed={**body,**{key:fresh_view[key] for key in ('review_token','source_token','candidate_token')}}
            mismatch=request('POST','/v1/reviews/review/decision',refreshed)
            acknowledged=request('POST','/v1/reviews/review/decision',{**refreshed,'acknowledge_difference':True})
            check('C04','differences require explicit reconfirmation: '+field,mismatch[0]==409 and acknowledged[0]==200,statuses=[mismatch[0],acknowledged[0]])
        def notification(identity):
            row=capture(identity,merchant_raw='Original')
            db.raw_events.insert_one({'event_id':row['event_id'],'source_type':'android_notification','captured_at':row['data_evento'],'raw_payload':{'text':'Compra aprovada de R$ 10,00 em LOJA NOVA no dia 21/09/2026 às 12:00'},'parsed_payload':{'amount':'10.00','direction':'expense'}})
            return row
        clear();row=notification('reparse');before=copy.deepcopy(db.provisional_transactions.find_one({}));raw_before=copy.deepcopy(db.raw_events.find_one({}));insert=Collection.insert_one
        def fail_audit(collection,document,*args,**kwargs):
            if collection.database.name==name and collection.name=='audit_log' and document.get('event')=='provisional_notification_reparsed':raise RuntimeError('Synthetic audit failure')
            return insert(collection,document,*args,**kwargs)
        with patch.object(Collection,'insert_one',fail_audit):reply=request('POST','/v1/provisional/reparse/reparse',{'expected_revision':1})
        check('C05','audit failure rolls back capture and parsed source',reply[0]==500 and before==db.provisional_transactions.find_one({}) and raw_before==db.raw_events.find_one({}),status=reply[0])
        okay=request('POST','/v1/provisional/reparse/reparse',{'expected_revision':1})
        check('C05','successful reparse advances revision',okay[0]==200 and db.provisional_transactions.find_one({})['revision']==2,status=okay[0])
        clear();row=notification('race');old_date=row['data_evento']
        def concurrent(event):
            confirmed=request('POST','/v1/provisional/race/decision',{'action':'confirm','expected_revision':1})
            assert confirmed[0]==200
            db.commitments.insert_one({'commitment_id':'synthetic-linked','linked_transactions':[{'provisional_id':'race','amount':Decimal128('10.00'),'charge_date':'2026-09-20'}]})
            return enrich_notification_event(event)
        with patch('app.main.enrich_notification_event',side_effect=concurrent):reply=request('POST','/v1/provisional/race/reparse',{'expected_revision':1})
        after=db.provisional_transactions.find_one({'provisional_id':'race'})
        check('C05','confirmation and link while parser runs invalidate reparse',reply[0]==409 and after['data_evento']==old_date and after['status']=='confirmed',status=reply[0])
        clear();import pymupdf as fitz
        for pages in (6,10):
            pdf=fitz.open()
            for _ in range(pages):pdf.new_page(width=200,height=200)
            data=pdf.tobytes();pdf.close()
            config=SimpleNamespace(xai_api_key='synthetic-only',xai_vision_model='synthetic',xai_base_url='https://invalid.test')
            with patch('app.services.ai.xai.provider.get_settings',return_value=config),patch('app.services.ai.xai.provider._post',return_value=DocumentExtraction(document_type='receipt',merchant='Synthetic',datetime=datetime.now(timezone.utc),total=10,currency='BRL')) as network:
                reply=request('POST','/v1/documents/extract',files={'file':(f'{pages}.pdf',data,'application/pdf')})
                sent=network.call_args.args[2]['input'][1]['content'] if network.called else []
            check('C06',str(pages)+' scanned pages sent without omission',reply[0]==200 and sum(item['type']=='input_image' for item in sent)==pages,status=reply[0],images=sum(item['type']=='input_image' for item in sent))
        clear()
        for route in ('/v1/import','/v1/capture-batches/scan'):
            extra={'data':{'batch_id':str(uuid4())}} if 'batches' in route else {}
            reply=request('POST',route,files={'file':('corrupt.xlsx',b'not-a-zip','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},**extra)
            check('C07','invalid XLSX permanently rejected at '+route,reply[0]==422,status=reply[0])
        clear();now=datetime.now(timezone.utc)
        db.capture_batches.insert_many([{'batch_id':(identity:=str(uuid4())),'_id':identity,'sha256':'a'*64,'filename':'synthetic.csv','rows':[],'status':'draft','created_at':now+timedelta(seconds=n//2)} for n in range(51)])
        page=request('GET','/v1/capture-batches')[1];first_ids={r['batch_id'] for r in page['items']};next_page=request('GET','/v1/capture-batches?cursor='+page['next_cursor'])[1]
        check('C08','51 drafts reachable including tied timestamps',page['total']==51 and page['has_more'] and len(first_ids|{r['batch_id'] for r in next_page['items']})==51 and not next_page['has_more'])
        clear();db.obligations.insert_many([{'status':'paga' if n<500 else 'futura','data_vencimento':day_start(date(2020,1,1) if n<500 else date(2026,10,15)),'saldo_aberto':Decimal128('1.00')} for n in range(501)])
        db.installment_plans.insert_many([{'data_compra':now,'installments':[]} for _ in range(201)])
        for route,total,limit in [('/v1/obligations',501,500),('/v1/installments',201,200)]:
            page=request('GET',f'{route}?limit={limit}')[1];rest=request('GET',f"{route}?limit={limit}&offset={page['next_offset']}")[1]
            check('C09',route+' complete continuation',page['total']==total and len({r['id'] for r in page['items']+rest['items']})==total and not rest['has_more'])
        filtered=request('GET','/v1/obligations?status_filter=futura')[1]
        check('C09','future filter applied before pagination',len(filtered['items'])==1 and filtered['total']==1)
        clear();status,profile=request('POST','/v1/account-profiles',{'request_id':str(uuid4()),'instituicao':'Banco Novo','tipo':'conta_corrente'})
        identity=profile['item']['key'];body={'creation_id':str(uuid4()),'kind':'assinatura','name':'Synthetic','purchase_date':'2026-09-01','next_due_date':'2026-10-20','amount':'10','payment_method':'debito','frequency':'mensal','renewal_status':'automatica','account_id':'profile:'+identity}
        reply=request('POST','/v1/commitments',body)
        check('C10','new additive open account accepts commitment',status==201 and reply[0]==201,status=reply[0])
        closure=request('PATCH','/v1/account-profiles/'+identity,{'expected_revision':1,'status':'encerrada','instituicao':'Banco Novo','tipo':'conta_corrente'})
        check('C10','closing the account succeeds',closure[0]==200,status=closure[0])
        closed=request('POST','/v1/commitments',{**body,'creation_id':str(uuid4())})
        check('C10','closed account rejects new commitment',closed[0]==422,status=closed[0])
        clear();blob=b'data;descricao;valor;tipo;moeda\n20/09/2026;Synthetic shop;-10.00;debito;BRL\n';digest=hashlib.sha256(blob).hexdigest();stored=store_media(db,blob,digest,'')
        batch_id=str(uuid4());now=datetime.now(timezone.utc)
        db.capture_batches.insert_one({'_id':batch_id,'batch_id':batch_id,'sha256':digest,'storage_key':str(stored),'status':'draft','filename':'synthetic.csv','created_at':now,'warnings':[],'complete':True,'account_hint':None,'rows':[{'row_id':1,'date':'2026-09-20','amount':'-10','description':'Synthetic shop','kind':'expense','currency':'BRL'}]})
        folder=out/('backup-'+uuid4().hex[:8]);backup(db,folder);restored_name='financial_twin_test_restore_'+uuid4().hex[:8];destination=out/('restored-media-'+uuid4().hex[:8]);restore(mongo,restored_name,folder,destination)
        stored.rename(stored.with_name(stored.name+'.unavailable'));restored=mongo[restored_name];batch=restored.capture_batches.find_one({})
        check('C11','restored batch resolves original bytes while origin unavailable',Path(batch['storage_key']).is_file() and hashlib.sha256(Path(batch['storage_key']).read_bytes()).hexdigest()==digest and restored.media_assets.find_one({})['path']==batch['storage_key'])
        app.state.db=restored;app.state.service=TransactionService(restored)
        review={'account_ref':'profile:itau','rows':[{**batch['rows'][0],'selected':True,'confirmed_distinct':False}],'acknowledge_partial':False}
        report=request('POST',f'/v1/capture-batches/{batch_id}/preview',review)
        committed=request('POST',f'/v1/capture-batches/{batch_id}/commit',{**review,'preview_token':report[1]['preview_token']})
        new_raw=restored.raw_events.find_one({})
        check('C11','confirmation propagates only restored storage paths',committed[0]==200 and Path(new_raw['raw_payload']['storage_key']).is_file(),status=committed[0])
        app.state.db=db;app.state.service=TransactionService(db)
        clear();source,dest=ObjectId(),ObjectId();db.accounts.insert_many([{'_id':source,'tipo':'conta_corrente','instituicao':'Itaú'},{'_id':dest,'tipo':'conta_corrente','instituicao':'Santander'}]);row=capture('transfer',status='confirmed',natureza='TRANSFERENCIA',tipo='transferencia',transfer={'from_account_key':'itau','to_account_key':'santander','reconciliation_status':'aguardando_extratos'})
        db.transactions.insert_many([{'account_id':source,'valor':Decimal128('-10.00'),'data_evento':row['data_evento'],'descricao_original':'PIX Santander'},{'account_id':dest,'valor':Decimal128('10.00'),'data_evento':row['data_evento'],'descricao_original':'PIX Itaú'}])
        reply=request('POST','/v1/transfers/reconcile');stale=request('PATCH','/v1/provisional/transfer',{'expected_revision':1,'categoria_sugerida':'Old editor'})
        check('C12','reconciliation invalidates pre-existing editor',reply[0]==200 and reply[1]['updated_count']==1 and db.provisional_transactions.find_one({'provisional_id':'transfer'})['revision']==2 and stale[0]==409,statuses=[reply[0],stale[0]])
        clear();row=capture('refund',natureza='ESTORNO',tipo='estorno');wrong=request('POST','/v1/provisional/refund/decision',{'action':'confirm','expected_revision':1});edit=request('PATCH','/v1/provisional/refund',{'expected_revision':1,'valor':'10','fluxo':'entrada'});okay=request('POST','/v1/provisional/refund/decision',{'action':'confirm','expected_revision':2});effect=effects(db.provisional_transactions.find_one({'provisional_id':'refund'}),True)
        check('C13','negative refund rejected; positive offsets consumption without income',wrong[0]==422 and edit[0]==200 and okay[0]==200 and effect['expenses']==-10 and effect['income']==0,statuses=[wrong[0],edit[0],okay[0]])
        # Snapshot isolation across a real edit and commitment link while assembling a trail.
        clear();row=capture('trail',status='confirmed');body={'creation_id':str(uuid4()),'kind':'assinatura','name':'Synthetic trail','purchase_date':'2026-09-01','next_due_date':'2026-09-20','amount':'100','payment_method':'pix','frequency':'mensal','renewal_status':'automatica'};commitment=request('POST','/v1/commitments',body)[1]['commitment_id'];find=Collection.find_one;mutations=[];triggered=False
        def interleave(collection,query,*args,**kwargs):
            nonlocal triggered
            result=find(collection,query,*args,**kwargs)
            if not triggered and collection.database.name==name and collection.name=='provisional_transactions' and query.get('provisional_id')=='trail' and kwargs.get('session'):
                triggered=True
                mutations.append(request('PATCH','/v1/provisional/trail',{'expected_revision':1,'valor':'-100'})[0])
                observed_source=db.provisional_transactions.find_one({'provisional_id':'trail'})
                observed_commitment=db.commitments.find_one({'commitment_id':commitment})
                mutations.append(request('POST',f'/v1/commitments/{commitment}/payment',{'provisional_id':'trail',
                    'expected_revision':observed_commitment.get('revision',1),'expected_source_token':source_token(observed_source),'request_id':str(uuid4())})[0])
            return result
        with patch.object(Collection,'find_one',interleave):reply=request('GET','/v1/insights/trail?reference=capture:trail')
        nodes=reply[1]['nodes'];origin=next(n['data'] for n in nodes if n['kind']=='capture');links=[n for n in nodes if n['kind']=='commitment']
        check('C20','trail entirely represents old snapshot during concurrent mutation',mutations==[200,200] and reply[1]['read_consistency']=='snapshot' and origin['valor']=='-10.00' and not links,status=reply[0],mutations=mutations)
        import httpx
        async def burst():
            lock=threading.Lock();active=0;peak=0
            def slow(*args):
                nonlocal active,peak
                with lock:active+=1;peak=max(peak,active)
                time.sleep(.2)
                with lock:active-=1
                return {'text':'Synthetic','persisted':False}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.local') as async_http:
                for route,target in [('/v1/capture/transcribe','app.voice_capture.XAIVoiceProvider.transcribe'),('/v1/capture/interpret','app.voice_capture.interpret')]:
                    peak=0
                    with patch(target,side_effect=slow):
                        jobs=[async_http.post(route,json={'text':'Paguei doze reais.','context':'movement'}) if route.endswith('interpret') else async_http.post(route,files={'file':('synthetic.ogg',b'OggS'+b'0'*100,'audio/ogg')}) for _ in range(8)]
                        replies=await asyncio.gather(*jobs)
                    statuses=[r.status_code for r in replies]
                    check('C19',route+' bounds provider concurrency and rejects excess work',peak<=2 and statuses.count(200)==2 and statuses.count(429)==6,peak=peak,statuses=statuses)
        asyncio.run(burst())
        print(json.dumps({'checks':len(results),'passed':sum(r['passed'] for r in results),'database':name}))
    finally:
        app.state.db=original_db;app.state.service=original_service
        if old_override is None:app.dependency_overrides.pop(require_session,None)
        else:app.dependency_overrides[require_session]=old_override
        http.close();mongo.drop_database(name)
        if restored_name:mongo.drop_database(restored_name)
        mongo.close()

if __name__=='__main__':run(sys.argv[1])
