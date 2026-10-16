"""Account lifecycle and concurrency probes in a disposable local replica set."""
import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from observed_client import ObservedTestClient as TestClient
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import app, database
from app.auth import require_session
from app.analytics import calculate_financial_totals
from app.atomic import _SessionCollection
from app.synthetic_testing import require_synthetic_target
from app.main import configured_mongodb_uri
from app.config import get_settings
from app.transaction_service import TransactionService


def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    uri=configured_mongodb_uri(get_settings())
    require_synthetic_target(uri)
    name='financial_twin_test_accounts_'+uuid4().hex[:8]
    assert name.startswith('financial_twin_test_accounts_')
    client=MongoClient(uri,serverSelectionTimeoutMS=10000,tz_aware=True)
    db=client[name]
    checks=[]
    now=datetime.now(timezone.utc)
    validator_file=output/'account-validator.json'
    if not validator_file.exists():validator_file=Path(__file__).parents[1]/'tests/fixtures/accounts-validator.json'
    if validator_file.exists():
        db.create_collection('accounts',**json.loads(validator_file.read_text(encoding='utf-8')))
    for collection in ('accounts','account_profiles','account_balance_snapshots','provisional_transactions',
        'transactions','audit_log','raw_events','capture_batches','capture_batch_locks','deleted_movements',
        'saving_experiments','commitments','review_queue','transaction_evidence','merchants'):
        if collection not in db.list_collection_names():db.create_collection(collection)
    db.accounts.create_index('external_key',unique=True)
    db.account_profiles.create_index('key',unique=True)
    db.account_balance_snapshots.create_index('account_key',unique=True)
    db.capture_batch_locks.insert_one({'_id':'movement-insert','revision':0})
    legacy_id=ObjectId()
    legacy={'_id':legacy_id,'external_key':'test-itau','schema_version':2,'instituicao':'Itaú',
        'tipo':'conta_corrente','titular':'Teste sintético','moeda':'BRL','status':'ativo',
        'created_at':now,'updated_at':now}
    db.accounts.insert_one(legacy)
    legacy=db.accounts.find_one({'_id':legacy_id})
    app.dependency_overrides[database]=lambda:db
    app.state.db=db
    app.state.service=TransactionService(db)
    app.dependency_overrides[require_session]=lambda:True
    http=TestClient(app)
    def call(method,path,body=None,expected=200):
        response=http.request(method,path,json=body)
        assert response.status_code==expected,(method,path,response.status_code,response.text[:900])
        return response.json()
    def check(label,condition):
        assert condition,label
        checks.append(label)
    def create(nickname):
        body={'request_id':str(uuid4()),'instituicao':'Itaú','apelido':nickname,'tipo':'poupanca',
            'agencia':'0012','agencia_digito':'X','numero':'00001234','numero_digito':'0'}
        return call('POST','/v1/account-profiles',body,201)['item'],body
    def edit(item,**changes):
        fields={key:item[key] for key in ('instituicao','apelido','tipo','status','moeda','archived',
            'agencia','agencia_digito','numero','numero_digito','data_abertura','data_encerramento','observacoes') if key in item}
        return call('PATCH','/v1/account-profiles/'+item['key'],{**fields,'expected_revision':item['revision'],**changes})['item']
    try:
        first,body=create('Reserva')
        check('create retry retains same key',call('POST','/v1/account-profiles',body,201)['item']['key']==first['key'])
        call('POST','/v1/account-profiles',{**body,'apelido':'Outro'},409)
        second,_=create('Dia a dia')
        check('same bank has separate account identities',first['key']!=second['key'])
        check('strict imported accounts untouched',list(db.accounts.find({}))==[legacy])
        check('metadata preserves leading zeros and digits',first['numero']=='00001234' and first['agencia_digito']=='X')
        for item,amount in ((first,'1000.50'),(second,'2000.25')):
            call('PUT',f"/v1/account-positions/{item['key']}/balance-reference",{'amount':amount,'as_of_date':(now-timedelta(days=2)).date().isoformat()})
        stamp=now.isoformat()
        event={'event_id':str(uuid4()),'source_type':'manual','source_provider':'mobile','captured_at':stamp,
            'raw_payload':{},'parsed_payload':{'event_type':'own_transfer','amount':'100','currency':'BRL','merchant_raw':'Reserva → Dia a dia',
            'direction':'expense','event_timestamp':stamp,'from_account_key':first['key'],'to_account_key':second['key']},'parser_version':'test'}
        service=TransactionService(db)
        result=service.process(event)
        capture=db.provisional_transactions.find_one({'event_id':event['event_id']})
        call('POST',f"/v1/provisional/{capture['provisional_id']}/decision",{'action':'confirm','expected_revision':1})
        rows={row['key']:row for row in call('GET','/v1/account-positions')['items']}
        check('transfer adjusts exactly the two selected balances',rows[first['key']]['balance']==900.5 and rows[second['key']]['balance']==2100.25)
        totals=calculate_financial_totals([],list(db.provisional_transactions.find({'status':'confirmed'})))
        check('own transfer remains economically neutral',totals['income']==0 and totals['expenses']==0)
        for key in (first['key'],second['key']):
            for query in ('','&cursor_mode=true'):
                page=call('GET',f'/v1/movements?account_id=profile:{key}'+query)
                check('statement filter supports '+key+query,len(page['items'])==1)
        renamed=edit(first,apelido='Reserva nova',instituicao='Itaú Unibanco',tipo='investimento',observacoes='Cadastro editado')
        rows={row['key']:row for row in call('GET','/v1/account-positions')['items']}
        check('metadata edit retains reference and movements',rows[first['key']]['balance']==900.5)
        call('PATCH','/v1/account-profiles/'+first['key'],{**body,'request_id':None},422)
        stale={key:value for key,value in body.items() if key!='request_id'}
        call('PATCH','/v1/account-profiles/'+first['key'],{**stale,'expected_revision':1},409)
        closed=edit(renamed,status='encerrada',data_encerramento=now.date().isoformat())
        archived=edit(closed,archived=True)
        check('closed archived account retains financial history',db.provisional_transactions.count_documents({'event_id':event['event_id']})==1)
        restored=edit(archived,archived=False)
        check('restore preserves closed status',restored['status']=='encerrada' and not restored['archived'])
        blocked=call('GET',f"/v1/account-profiles/{first['key']}/deletion")
        check('deletion preview exposes reference and capture links',not blocked['can_delete'] and blocked['usage']['saldos']==1 and blocked['usage']['capturas']==1)
        call('DELETE',f"/v1/account-profiles/{first['key']}",{'expected_revision':restored['revision']},409)
        closed_event={**event,'event_id':str(uuid4())}
        try:service.process(closed_event)
        except ValueError:pass
        check('new transfer rejects closed account',db.raw_events.find_one({'event_id':closed_event['event_id']})['processing_status']=='rejected')
        empty,_=create('Excluir')
        check('empty account can be deleted',call('GET',f"/v1/account-profiles/{empty['key']}/deletion")['can_delete'])
        call('DELETE',f"/v1/account-profiles/{empty['key']}",{'expected_revision':1})
        check('deleted account does not resurrect',empty['key'] not in {p['key'] for p in call('GET','/v1/account-positions')['items']})
        # Inject a failure after the metadata write; the real transaction must abort.
        failure_body={**body,'request_id':str(uuid4()),'apelido':'Falha de auditoria'}
        original=_SessionCollection.__getattr__
        def failing_audit(self,attribute):
            if self.collection.name=='audit_log' and attribute=='insert_one':
                def fail(*args,**kwargs):raise RuntimeError('injected audit failure')
                return fail
            return original(self,attribute)
        with patch.object(_SessionCollection,'__getattr__',failing_audit):
            try:call('POST','/v1/account-profiles',failure_body,201)
            except RuntimeError:pass
        failure_key='account_'+failure_body['request_id'].replace('-','')
        check('real Mongo rollback includes metadata and audit',db.account_profiles.count_documents({'key':failure_key})==0)
        # A reference insertion and deletion compete for the same transaction fence.
        contested,_=create('Concorrência')
        def insert_balance():
            return http.put(f"/v1/account-positions/{contested['key']}/balance-reference",json={'amount':'50'}).status_code
        def delete_account():
            return http.request('DELETE',f"/v1/account-profiles/{contested['key']}",json={'expected_revision':1}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(insert_balance),executor.submit(delete_account)]
            statuses=[f.result() for f in futures]
        stored=db.account_profiles.find_one({'key':contested['key']})
        check('concurrent deletion cannot orphan balance reference',not (stored.get('deleted') and db.account_balance_snapshots.count_documents({'account_key':contested['key']})))
        check('only one conflicting mutation succeeds',statuses.count(200)==1 and all(code in (200,409,422) for code in statuses))
        check('imported account remains byte-for-byte unchanged',list(db.accounts.find({}))==[legacy])
        report={'checks':checks,'concurrency_statuses':statuses,'database':name,'local_replica_set':True,
            'production_data_modified':False,'http_checks_passed':True}
        (output/'integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        app.dependency_overrides.clear()
        assert name.startswith('financial_twin_test_accounts_')
        client.drop_database(name)
        client.close()


if __name__=='__main__':run(sys.argv[1])
