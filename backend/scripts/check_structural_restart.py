"""Actual API process restart after discarding response bodies; synthetic only."""
import io
import json
import os
import sys
import time
import subprocess
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
sys.path.insert(0,str(Path(__file__).parents[1]))
from app.synthetic_testing import require_synthetic_target
from app.config import get_settings
from app.main import configured_mongodb_uri
from pymongo import MongoClient


def worker(name,port):
    if not name.startswith('financial_twin_test_'):raise RuntimeError('Synthetic database identity required')
    uri=configured_mongodb_uri(get_settings());require_synthetic_target(uri)
    import uvicorn
    from app.main import app
    from app.auth import require_session
    from app.transaction_service import TransactionService
    @asynccontextmanager
    async def startup(instance):
        with MongoClient(uri,tz_aware=True) as client:
            instance.state.mongo_client=client
            instance.state.db=client[name]
            instance.state.service=TransactionService(instance.state.db)
            instance.state.db.capture_batch_locks.update_one({'_id':'movement-insert'},{'$setOnInsert':{'revision':0}},upsert=True)
            yield
    app.router.lifespan_context=startup
    app.dependency_overrides[require_session]=lambda:True
    uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')


def run(output):
    import httpx
    from openpyxl import Workbook
    uri=configured_mongodb_uri(get_settings());require_synthetic_target(uri)
    target=Path(output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    name='financial_twin_test_restart_'+uuid4().hex[:10]
    port=18013;base='http://127.0.0.1:'+str(port)
    log=(target.parent/'restart-worker.log').open('w',encoding='utf8')
    environment=dict(os.environ,MONGODB_DATABASE=name,MONGODB_URI=uri,MONGODB_DIRECT_HOSTS='',MEDIA_STORAGE_PATH=str(target.parent/'restart-synthetic-media'))
    process=None;checks=[]
    def start():
        nonlocal process
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',name,str(port)],env=environment,stdout=log,stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Synthetic worker exited; inspect restart-worker.log')
            try:
                if httpx.get(base+'/health',timeout=1).status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.2)
        raise RuntimeError('Synthetic worker did not become ready')
    def crash():
        nonlocal process
        process.kill();process.wait(timeout=10);process=None
    def check(label,condition):
        assert condition,label;checks.append({'check':label,'passed':True})
    client=MongoClient(uri,tz_aware=True);db=client[name]
    try:
        start()
        identity=str(uuid4());payload={'event_id':identity,'source_type':'manual','source_provider':'synthetic-process-restart','captured_at':'2026-10-01T15:00:00Z',
            'raw_payload':{'text':'Retained original synthetic operation'},'parsed_payload':{'amount':'12.59','merchant_raw':'Synthetic restart manual','event_timestamp':'2026-10-01T15:00:00Z','direction':'expense','event_type':'card_purchase'}}
        accepted=httpx.post(base+'/v1/events',json=payload,timeout=20);assert accepted.status_code==202,accepted.text
        pid=db.provisional_transactions.find_one({'event_id':identity})['provisional_id']
        command={'request_id':str(uuid4()),'expected_revision':1,'valor':'-13.59','action':'confirm'}
        with httpx.stream('PATCH',base+'/v1/provisional/'+pid,json=command,timeout=20) as response:
            check('confirmed edit response headers arrive while response body is deliberately lost',response.status_code==200)
        crash();start()
        replay=httpx.patch(base+'/v1/provisional/'+pid,json=command,timeout=20)
        result=replay.json()
        check('actual killed process restart recognizes original confirmed command ACK',replay.status_code==200 and result['persisted'] and result['request_id']==command['request_id'] and result['item']['status']=='confirmed')
        check('edit and confirmation not repeated after lost response and process crash',db.audit_log.count_documents({'event':'provisional_transaction_updated','provisional_id':pid})==1 and db.audit_log.count_documents({'event':'provisional_transaction_decision','provisional_id':pid})==1)
        again=httpx.post(base+'/v1/events',json=payload,timeout=20)
        check('original raw operation resend cannot recreate the confirmed projection after process restart',again.status_code==202 and again.json()['status']=='confirmed' and db.provisional_transactions.count_documents({'event_id':identity})==1)

        csv=b'data;descricao;valor;fluxo;moeda\n01/10/2026;CSV restart first synthetic;-17,31;saida;BRL\n02/10/2026;CSV restart second synthetic;-18,43;saida;BRL\n'
        workbook=Workbook();sheet=workbook.active;sheet.append(['data','descricao','valor','fluxo','moeda']);sheet.append(['01/10/2026','XLSX restart first synthetic','-19,27','saida','BRL']);sheet.append(['02/10/2026','XLSX restart second synthetic','-20,39','saida','BRL']);xlsx=io.BytesIO();workbook.save(xlsx);workbook.close()
        for filename,data,mime in [('restart.csv',csv,'text/csv'),('restart.xlsx',xlsx.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]:
            event_id=str(uuid4());before=db.raw_events.count_documents({})
            with httpx.stream('POST',base+'/v1/import',data={'event_id':event_id},files={'file':(filename,data,mime)},timeout=30) as response:
                check(filename+' commits atomically before deliberately discarded response body',response.status_code==200)
            crash();start()
            repeated=httpx.post(base+'/v1/import',data={'event_id':event_id},files={'file':(filename,data,mime)},timeout=30)
            ack=repeated.json()
            check(filename+' lost response replay after real process restart returns durable identity',repeated.status_code==200 and ack['persisted'] and ack['event_id']==event_id and ack['accepted']==2)
            check(filename+' original files and exactly two events survive process restart',db.raw_events.count_documents({})==before+2 and db.media_assets.find_one({'sha256':ack['file_sha256']}) is not None and any(Path(row['raw_payload']['storage_key']).is_file() for row in db.raw_events.find({'raw_payload.file_sha256':ack['file_sha256']})))
        target.write_text(json.dumps({'database':name,'passed':len(checks),'checks':checks,'actual_process_restarts':3,'discarded_response_bodies':3,'production_writes':False},ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'passed':len(checks),'output':str(target),'database':name}),flush=True)
    finally:
        if process is not None:process.terminate();process.wait(timeout=10)
        log.close();client.close()


if __name__=='__main__':
    if sys.argv[1]=='--worker':worker(sys.argv[2],int(sys.argv[3]))
    else:run(sys.argv[1])
