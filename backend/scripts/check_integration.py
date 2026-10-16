"""Disposable synthetic integration suite. Never writes configured production DB.

No application lifespan: avoids recovery workers or production index changes.
Databases are retained for inspection and may be removed explicitly afterwards.
"""
import sys,json
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from observed_client import ObservedTestClient as TestClient
from app.main import app,configured_mongodb_uri
from app.auth import require_session
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from backup_restore import backup,restore

def run(output):
    settings=get_settings();name='financial_twin_test_'+uuid4().hex[:8]
    require_synthetic_target(configured_mongodb_uri(settings))
    assert name!=settings.mongodb_database and name.startswith('financial_twin_test_')
    client=MongoClient(configured_mongodb_uri(settings),serverSelectionTimeoutMS=10000,tz_aware=True)
    db=client[name];app.state.db=db;app.dependency_overrides[require_session]=lambda:True
    http=TestClient(app);checks=[]
    def call(method,path,body=None,expected=200):
        response=http.request(method,path,json=body)
        if response.status_code!=expected:raise AssertionError(f'{method} {path}: {response.status_code} {response.text[:900]}')
        return response.json()
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    stamp=datetime(2026,9,28,12,tzinfo=timezone.utc)
    refund={'provisional_id':'refund','event_id':'refund-event','status':'confirmed','revision':1,'tipo':'estorno','valor':Decimal128('15.50'),'data_evento':stamp,'natureza':'ESTORNO','fluxo':'entrada','categoria_sugerida':'Alimentação'}
    db.provisional_transactions.insert_one(refund)
    debt={'_id':ObjectId(),'valor':Decimal128('-100'),'data_evento':stamp,'tipo':'compra','fluxo':'saida','classification':{'migration_reason':'despesa_real'},'categoria':'Alimentação','descricao_original':'Padaria ABC'}
    db.transactions.insert_one(debt)
    obligation_id=ObjectId();db.obligations.insert_one({'_id':obligation_id,'status':'futura','saldo_aberto':Decimal128('100')})
    db.reconciliations.insert_one({'origem':{'entity':'transactions','id':debt['_id']},'destino':{'entity':'obligations','id':obligation_id}})
    closure=call('GET','/v1/insights/closure?start=2026-09-28&end=2026-09-28')
    check('closure refund offsets consumption',closure['income']=='0.00' and closure['expenses']=='84.50' and closure['read_consistency']=='snapshot')
    graph=call('GET','/v1/insights/trail?reference=ledger:'+str(debt['_id']))
    check('polymorphic reconciliation graph',any(n['kind']=='obligations' for n in graph['nodes']))
    dashboard=call('GET','/v1/dashboard?date_from=2026-09-28&date_to=2026-09-28')
    check('bounded dashboard preserves local end date and refund total',dashboard['period_end']=='2026-09-28' and dashboard['expenses']==84.5)
    call('GET','/v1/dashboard/overview')
    # Undo retains the original evidence, is idempotent and permits later explicit re-link.
    event_id=str(uuid4());db.raw_events.insert_one({'event_id':event_id,'captured_at':stamp,'parsed_payload':{'amount':'100','merchant_raw':'Padaria ABC','direction':'expense'}})
    db.transaction_evidence.insert_one({'event_id':event_id,'target_type':'transaction','transaction_id':debt['_id'],'revision':1})
    undo={'request_id':str(uuid4()),'expected_revision':1,'reason':'Revisão sintética de conciliação'}
    reopened=call('POST','/v1/insights/evidence/'+event_id+'/undo',undo)
    check('undo idempotence',call('POST','/v1/insights/evidence/'+event_id+'/undo',undo)==reopened)
    check('undo preserves original event and audit',db.raw_events.count_documents({'event_id':event_id})==1 and db.transaction_evidence.find_one({'event_id':event_id})['status']=='reversed')
    review_id=str(uuid4());db.review_queue.insert_one({'review_id':review_id,'event_id':event_id,'status':'pending','candidate_type':'transaction','candidate_transaction_id':debt['_id'],'match_score':1,'created_at':stamp})
    call('POST','/v1/reviews/'+review_id+'/decision',{'action':'confirm_match'})
    check('relink after undo keeps evidence unique',db.transaction_evidence.count_documents({'event_id':event_id})==1 and db.transaction_evidence.find_one({'event_id':event_id})['revision']==3)
    call('POST','/v1/insights/evidence/'+event_id+'/undo',{**undo,'request_id':str(uuid4())},409)
    body={'request_id':str(uuid4()),'title':'Reembolso teste','kind':'missing_refund','expected_amount':'15.50','source_refs':['ledger:'+str(debt['_id'])]}
    case=call('POST','/v1/insights/cases',body,201)
    repeated=call('POST','/v1/insights/cases',body,201)
    check('idempotent case create',case['id']==repeated['id'] and db.financial_cases.count_documents({})==1)
    call('POST','/v1/insights/cases',{**body,'title':'Alterado'},409)
    resolved=call('PATCH','/v1/insights/cases/'+case['id'],{'revision':1,'status':'resolved','refund_ref':'capture:refund'})
    check('refund resolution',resolved['recovered_amount']=='15.50')
    other=call('POST','/v1/insights/cases',{**body,'request_id':str(uuid4())},201)
    call('PATCH','/v1/insights/cases/'+other['id'],{'revision':1,'status':'resolved','refund_ref':'capture:refund'},409)
    check('refund cannot be counted twice',db.financial_cases.find_one({'_id':other['id']})['status']=='open')
    call('PATCH','/v1/insights/cases/'+case['id'],{'revision':1,'status':'open'},409)
    call('PATCH','/v1/provisional/refund',{'expected_revision':1,'valor':'18.00'},409)
    dossier=call('GET','/v1/insights/cases/'+case['id']+'/dossier')
    check('dossier has sources and audit',len(dossier['sources'])==2 and len(dossier['history'])==2 and not dossier['sent_externally'])
    # Inject a failure after primary writes, using a real Mongo transaction.
    failing={**body,'request_id':str(uuid4())}
    with patch('app.insights.audit',side_effect=RuntimeError('injected failure')):
        try:call('POST','/v1/insights/cases',failing,201)
        except RuntimeError:pass
    check('real transaction rolls back audit failure',db.financial_cases.find_one({'_id':failing['request_id']}) is None)
    exp={'request_id':str(uuid4()),'title':'Experimento teste','category':'Alimentação','protocol':'Planejar o almoço','baseline_start':'2026-09-01','baseline_end':'2026-09-14','start':'2026-09-15','end':'2026-10-15','target_reduction_pct':'10'}
    experiment=call('POST','/v1/insights/experiments',exp,201)
    call('POST','/v1/insights/experiments',{**exp,'request_id':str(uuid4())},409)
    call('PATCH','/v1/insights/experiments/'+experiment['id'],{'revision':1,'status':'completed','satisfaction':4})
    report=call('GET','/v1/insights/experiments/'+experiment['id']+'/report')
    check('experiment locks and completed horizon',db.experiment_locks.count_documents({})==0 and report['experiment'].get('actual_end') is not None)
    scenario=call('POST','/v1/insights/simulate',{'start':'2026-09-29','first_payment':'2026-09-29','opening_balance':'100','purchase':'10','installments':3})
    check('four scenarios through HTTP',len(scenario['scenarios'])==4)
    liquidity=call('POST','/v1/insights/liquidity',{'start':'2026-09-29','accounts':[{'key':'itau','balance':'100'},{'key':'inter','balance':'-10'}]})
    check('liquidity proposal is economically neutral',sum(float(v) for v in liquidity['closing_balances'].values())==90)
    db.review_queue.insert_many([{'review_id':str(uuid4()),'created_at':stamp,'status':'pending'} for _ in range(241)])
    identities=[];cursor=None
    while True:
        result=call('GET','/v1/reviews?limit=37'+('&cursor='+cursor if cursor else ''));identities.extend(r['review_id'] for r in result['items']);cursor=result['next_cursor']
        if not result['has_more']:break
    check('241 review rows with identical timestamps accessible once',len(identities)==241 and len(set(identities))==241)
    output.mkdir(parents=True,exist_ok=True)
    manifest=backup(db,output/'backup');restored_name=name+'_restore'
    restore(client,restored_name,output/'backup',output/'restored-media')
    check('lossless backup restore counts',all(client[restored_name][c].count_documents({})==m['count'] for c,m in manifest['collections'].items()))
    check('Decimal128 survives restore',client[restored_name].provisional_transactions.find_one({'provisional_id':'refund'})['valor']==Decimal128('15.50'))
    report={'database':name,'restore_database':restored_name,'production_writes':False,'checks':checks,'count':len(checks)}
    (output/'integration-results.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,indent=2));http.close();client.close()

if __name__=='__main__':run(Path(sys.argv[1]).resolve())
