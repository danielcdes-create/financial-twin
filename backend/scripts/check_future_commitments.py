"""Future schedules, dashboard/planning, concurrency and rollback in a new test DB."""
import copy, json, sys
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.collection import Collection
from observed_client import ObservedTestClient as TestClient
from app.main import app, configured_mongodb_uri
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.auth import require_session

def run(output):
    settings=get_settings(); name='financial_twin_test_future_'+uuid4().hex[:8]
    require_synthetic_target(configured_mongodb_uri(settings))
    assert name != settings.mongodb_database
    mongo=MongoClient(configured_mongodb_uri(settings),serverSelectionTimeoutMS=10000,tz_aware=True); db=mongo[name]
    app.state.db=db; app.dependency_overrides[require_session]=lambda:True
    http=TestClient(app,raise_server_exceptions=False); checks=[]
    def check(label,condition):
        assert condition,label
        checks.append({'check':label,'passed':True})
    def call(method,path,body=None,status=200):
        response=http.request(method,path,json=body)
        assert response.status_code==status,(path,response.status_code,response.text[:600])
        return response.text if status == 500 else response.json()
    plan={'_id':ObjectId(),'descricao':'AMAZON TESTE','status':'ativo','numero_parcelas':4,'data_compra':datetime(2026,8,1,tzinfo=timezone.utc),
        'installments':[{'numero':1,'competencia':'2026-09','valor':Decimal128('10.00'),'status':'paga','transaction_id':ObjectId()},
          *[{'numero':i,'competencia':f'2026-{i+8:02d}','valor':Decimal128('10.00'),'status':'projetada'} for i in [2,3,4]]]}
    db.installment_plans.insert_one(copy.deepcopy(plan))
    original_transaction={'_id':plan['installments'][0]['transaction_id'],'data_evento':datetime(2026,9,15,15,tzinfo=timezone.utc),'valor':Decimal128('-10.00'),'fluxo':'saida','classification':{'migration_reason':'despesa_real'}}
    db.transactions.insert_one(copy.deepcopy(original_transaction))
    base='/v1/future-commitments/installment/'+str(plan['_id'])
    before=call('GET',base)
    check('preview protects paid installment and exposes only forecasts',before['editable_count']==3 and not before['rows'][0]['editable'] and before['rows'][1]['due_date'] is None)
    body={'request_id':str(uuid4()),'preview_token':before['preview_token'],'rows':[{'row_id':2,'amount':'12.59','due_date':'2026-10-20','cancelled':False}]}
    result=call('PUT',base,body)
    check('future edit confirmed',result['persisted'] and result['affected_count']==1)
    effective=call('GET','/v1/installments')['items'][0]
    check('statement schedule uses exact edited cents and due date',effective['installments'][1]['valor']==12.59 and effective['installments'][1]['data_vencimento'].startswith('2026-10-20'))
    check('legacy plan and paid transaction unchanged',db.installment_plans.find_one({'_id':plan['_id']})==plan and db.transactions.find_one({'_id':original_transaction['_id']})==original_transaction)
    check('lost response retry uses one correction and audit',call('PUT',base,body)['duplicate_request'] and db.audit_log.count_documents({})==1)
    dashboard='/v1/dashboard/overview?period=month&year_start=2026-01-01&month_start=2026-10-01&month_end=2026-10-31'
    check('dashboard reads corrected future schedule',call('GET',dashboard)['selected']['projected_installment_total']==32.59)
    fresh=call('GET',base); bad={**body,'request_id':str(uuid4()),'preview_token':fresh['preview_token'],'rows':[{'row_id':1,'amount':'99.00'}]}
    call('PUT',base,bad,409)
    check('paid row cannot be edited',db.installment_plans.find_one({'_id':plan['_id']})['installments'][0]==plan['installments'][0])
    call('PUT',base,{**body,'request_id':str(uuid4())},409)
    check('stale confirmation blocked',db.future_schedule_overrides.find_one({'_id':'installment:'+str(plan['_id'])})['revision']==1)
    race=[{**body,'request_id':str(uuid4()),'preview_token':fresh['preview_token'],'rows':[{'row_id':3,'amount':value}]} for value in ['17.59','18.59']]
    with ThreadPoolExecutor(max_workers=2) as pool: replies=list(pool.map(lambda item:http.put(base,json=item),race))
    check('concurrent edits have one winner and one conflict',sorted(reply.status_code for reply in replies)==[200,409])
    current=call('GET',base); failing={**body,'request_id':str(uuid4()),'preview_token':current['preview_token'],'rows':[{'row_id':4,'amount':'21.93'}]}
    snapshot=db.future_schedule_overrides.find_one({}); commands=db.future_commitment_commands.count_documents({}); audits=db.audit_log.count_documents({})
    insert=Collection.insert_one
    def fail_audit(collection,*args,**kwargs):
        if collection.database.name==name and collection.name=='audit_log':raise RuntimeError('synthetic audit outage')
        return insert(collection,*args,**kwargs)
    with patch.object(Collection,'insert_one',fail_audit): call('PUT',base,failing,500)
    check('audit failure rolls back correction and command',db.future_schedule_overrides.find_one({})==snapshot and db.future_commitment_commands.count_documents({})==commands and db.audit_log.count_documents({})==audits)
    check('same command recovers after rollback',call('PUT',base,failing)['affected_count']==1)
    deletion={'request_id':str(uuid4()),'preview_token':call('GET',base)['preview_token']}
    call('DELETE',base,deletion)
    effective=call('GET','/v1/installments')['items'][0]
    check('all remaining forecasts removed; paid installment retained',all(row['status']=='cancelada' for row in effective['installments'][1:]) and effective['installments'][0]['status']=='paga')
    check('dashboard stops summing excluded forecasts',call('GET',dashboard)['selected']['projected_installment_total']==0)
    check('exclusion retry idempotent',call('DELETE',base,deletion)['duplicate_request'])
    check('plan remains exactly preserved after exclusion',db.installment_plans.find_one({'_id':plan['_id']})==plan)
    obligation={'_id':ObjectId(),'tipo':'financiamento','competencia':'2026-11','status':'futura','data_vencimento':datetime(2026,11,11,tzinfo=timezone.utc),'saldo_aberto':Decimal128('3700.25'),'valor_pago':Decimal128('0'),'principal':Decimal128('2000')}
    db.obligations.insert_one(copy.deepcopy(obligation)); obase='/v1/future-commitments/obligation/'+str(obligation['_id'])
    obody={'request_id':str(uuid4()),'preview_token':call('GET',obase)['preview_token'],'rows':[{'row_id':1,'amount':'3600.13','due_date':'2026-11-15'}]}
    call('PUT',obase,obody)
    context='/v1/insights/purchase-context?start=2026-10-01&installments=12&first_payment=2026-10-01'
    suggestion=call('GET',context)['suggested_flows'][0]
    check('purchase simulation uses corrected obligation date and cash amount',suggestion['date']=='2026-11-15' and float(suggestion['amount'])==-3600.13)
    check('original mortgage and paid amount unchanged',db.obligations.find_one({'_id':obligation['_id']})==obligation)
    call('DELETE',obase,{'request_id':str(uuid4()),'preview_token':call('GET',obase)['preview_token']})
    check('excluded mortgage absent from simulation and management',call('GET',context)['suggested_flows']==[] and call('GET','/v1/future-commitments/obligations')['items']==[])
    managed=call('POST','/v1/commitments',{'creation_id':str(uuid4()),'kind':'assinatura','name':'Serviço sintético','purchase_date':'2026-01-01','next_due_date':'2026-10-25','amount':'29.90','frequency':'mensal','payment_method':'pix','renewal_status':'automatica'},201)
    paid_link={'transaction_id':str(original_transaction['_id']),'actual_amount':'10.00','paid_date':'2026-09-15'}
    db.commitments.update_one({'commitment_id':managed['commitment_id']},{'$set':{'linked_transactions':[paid_link]}})
    mbase='/v1/future-commitments/managed/'+managed['commitment_id']; mbody={'request_id':str(uuid4()),'preview_token':call('GET',mbase)['preview_token']}
    call('DELETE',mbase,mbody)
    check('managed future removal keeps real payment links',db.commitments.find_one({'commitment_id':managed['commitment_id']})['linked_transactions']==[paid_link])
    check('removed managed future is hidden from active list and simulator',call('GET','/v1/commitments')['items']==[] and call('GET',context)['flows']==[])
    check('managed removal retry idempotent',call('DELETE',mbase,mbody)['duplicate_request'])
    check('financial movement remains unchanged through all workflows',db.transactions.find_one({'_id':original_transaction['_id']})==original_transaction and db.transactions.count_documents({})==1)
    target=Path(output);target.parent.mkdir(parents=True,exist_ok=True)
    report={'database':name,'configured_financial_database_untouched':True,'checks':checks,'passed':True}
    target.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2));mongo.close()

if __name__=='__main__':run(sys.argv[1])
