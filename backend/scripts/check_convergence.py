"""Cross-workflow probes in a fresh database; production mode is strictly read-only."""
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from observed_client import ObservedTestClient as TestClient
from app.main import app, configured_mongodb_uri
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.auth import require_session
from app.atomic import mongo_snapshot
from app.future_commitments import effective_documents


def run(output, readonly=False):
    target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    if not readonly:
        require_synthetic_target(configured_mongodb_uri(settings))
    mongo = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000, tz_aware=True)
    name = settings.mongodb_database if readonly else 'financial_twin_test_conv_' + uuid4().hex[:8]
    db = mongo[name]
    checks = []
    def check(label, condition, detail=None):
        checks.append({'check': label, 'passed': bool(condition), **({'detail': detail} if detail is not None else {})})

    if readonly:
        with mongo_snapshot(db) as (reading, consistency):
            fingerprints = {}
            for collection in ['transactions', 'installment_plans', 'obligations', 'commitments', 'provisional_transactions', 'deleted_movements', 'future_schedule_overrides']:
                rows = list(reading[collection].find().sort('_id', 1))
                fingerprints[collection] = {'count': len(rows), 'sha256': hashlib.sha256(json_util.dumps(rows, sort_keys=True).encode()).hexdigest()}
            archives = list(reading.deleted_movements.find())
            resurrected = [row['_id'] for row in archives if (reading.transactions.find_one({'_id': row['document']['_id']}) if row['source'] == 'ledger' else reading.provisional_transactions.find_one({'provisional_id': row['identity'], 'status': {'$in': ['confirmed','enriched','provisional']}}))]
            check('no deleted financial movement resurrected', not resurrected, resurrected)
            targets = {row['_id'] for collection in ['transactions','provisional_transactions'] for row in reading[collection].find({}, {'_id':1})}
            targets.update(row['document']['_id'] for row in archives)
            orphaned = []
            for event in reading.raw_events.find({'linked_transaction_id': {'$ne': None}, 'processing_status': {'$in': ['matched','confirmed']}}):
                identity = event.get('linked_transaction_id')
                if identity not in targets: orphaned.append(event['event_id'])
            check('terminal events have active or archived targets', not orphaned, orphaned)
            overrides = list(reading.future_schedule_overrides.find())
            marker = reading.schema_migrations.find_one({'_id':'descriptor_index_v1'}) or {}
            ledger_ids = {row['_id'] for row in reading.transactions.find({}, {'_id':1})}
            descriptor_ids = {row['_id'] for row in reading.transaction_descriptor_index.find({}, {'_id':1})}
            check('descriptor index covers the exact active ledger',marker.get('complete') and marker.get('scanned')==len(ledger_ids) and descriptor_ids==ledger_ids)
            stale = []
            for source, collection in [('installment','installment_plans'),('obligation','obligations')]:
                originals = list(reading[collection].find())
                for original, current in zip(originals, effective_documents(reading, source, originals)):
                    if current.get('future_correction_needs_review'): stale.append(f'{source}:{original["_id"]}')
            check('no stale future correction in current financial data', not stale, stale)
        report = {'read_only': True, 'read_consistency': consistency, 'fingerprints': fingerprints, 'override_count': len(overrides), 'checks': checks}
    else:
        assert name != settings.mongodb_database and name.startswith('financial_twin_test_conv_')
        os.environ['MEDIA_STORAGE_PATH'] = str(target.parent/'synthetic-media')
        app.state.db = db; app.dependency_overrides[require_session] = lambda: True
        http = TestClient(app, raise_server_exceptions=False)
        def call(method, path, body=None, status=200):
            result = http.request(method, path, json=body)
            assert result.status_code == status, (path,result.status_code,result.text[:400])
            return result.json()
        account = ObjectId()
        db.accounts.insert_one({'_id':account,'instituicao':'Itaú','tipo':'conta_corrente'})
        db.account_balance_snapshots.insert_one({'account_key':'itau','as_of':datetime(2026,9,30,tzinfo=timezone.utc),'amount':Decimal128('100.00')})
        db.capture_batch_locks.insert_one({'_id':'movement-insert','revision':0})
        csv = b'data;descricao;valor;direcao;moeda;identificador_transacao\n01/10/2026;Compra convergencia;-12,59;debito;BRL;CONVERGENCE-1\n'
        def batch():
            identity = str(uuid4())
            response = http.post('/v1/capture-batches/scan',data={'batch_id':identity},files={'file':('extrato.csv',csv,'text/csv')})
            assert response.status_code == 200,response.text
            review = {'account_ref':'ledger:'+str(account),'rows':[dict(row,selected=True) for row in response.json()['batch']['rows']]}
            return '/v1/capture-batches/'+identity,review
        base, review = batch(); token = call('POST',base+'/preview',review)['preview_token']
        body = {**review,'preview_token':token}; result = call('POST',base+'/commit',body)
        check('batch insertion is one confirmed financial movement',result['inserted_count']==1)
        row = db.provisional_transactions.find_one({'status':'confirmed'}); original = copy.deepcopy(row)
        movement = '/v1/movements/capture/'+row['provisional_id']
        deletion = {'preview_token':call('GET',movement+'/deletion')['preview_token']}
        call('DELETE',movement,deletion)
        check('delete archives exact batch capture',db.deleted_movements.find_one({'_id':'capture:'+row['provisional_id']})['document']==original)
        check('raw event becomes terminal cancellation',db.raw_events.find_one({'event_id':row['event_id']})['processing_status']=='cancelled')
        check('old successful batch retry cannot resurrect deleted row',call('POST',base+'/commit',body)['duplicate_request'] and db.provisional_transactions.count_documents({'status':'confirmed'})==0)
        again, repeated = batch(); preview = call('POST',again+'/preview',repeated)
        check('new batch cannot recreate archived financial row',preview['insert_count']==0 and preview['rows'][0]['status']=='duplicate')
        check('statement excludes deleted batch row',call('GET','/v1/movements?cursor_mode=true')['total']==0)
        dashboard = call('GET','/v1/dashboard?period=all')
        check('dashboard removes deleted expense',dashboard['expenses']==0 and dashboard['transaction_count']==0)
        from app.account_positions import account_positions
        check('account reference balance restored after capture deletion',next(row for row in account_positions(db) if row['key']=='itau')['balance']==100.0)

        from app.main import batch_classification_matches, canonical_key
        ledger_rows=[{'_id':ObjectId(),'data_evento':datetime(2026,9,1,15,tzinfo=timezone.utc),'valor':Decimal128('-10.01'),'fluxo':'saida','descricao_original':'Loja convergencia sintética','classification':{'migration_reason':'despesa_real'}} for _ in range(3)]
        db.transactions.insert_many(copy.deepcopy(ledger_rows))
        descriptor={'_id':ledger_rows[1]['_id'],'key':canonical_key(ledger_rows[1]['descricao_original'])}
        db.transaction_descriptor_index.insert_many([{'_id':row['_id'],'key':descriptor['key']} for row in ledger_rows])
        db.schema_migrations.insert_one({'_id':'descriptor_index_v1','complete':True,'scanned':3})
        check('classification batch index is usable before individual deletion',len(batch_classification_matches(db,ledger_rows[0]))==2)
        path='/v1/movements/ledger/'+str(ledger_rows[1]['_id'])
        call('DELETE',path,{'preview_token':call('GET',path+'/deletion')['preview_token']})
        try: usable=len(batch_classification_matches(db,ledger_rows[0]))==1
        except Exception: usable=False
        check('individual ledger deletion keeps unrelated classification batches usable',usable)
        archive=db.deleted_movements.find_one({'_id':'ledger:'+str(ledger_rows[1]['_id'])})
        check('deleted descriptor preserved outside active auxiliary index',archive.get('descriptor_index')==descriptor and not db.transaction_descriptor_index.find_one({'_id':descriptor['_id']}))
        deletions=[]
        for row in [ledger_rows[0],ledger_rows[2]]:
            path='/v1/movements/ledger/'+str(row['_id'])
            deletions.append((path,{'preview_token':call('GET',path+'/deletion')['preview_token']}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda entry:http.request('DELETE',entry[0],json=entry[1]),deletions))
        check('different concurrent deletions return success or explicit conflict',all(reply.status_code in [200,409] for reply in replies))
        for path, deletion in deletions: call('DELETE',path,deletion)
        check('retry of concurrent deletions converges active ledger, index and coverage',db.transactions.count_documents({})==db.transaction_descriptor_index.count_documents({})==db.schema_migrations.find_one({'_id':'descriptor_index_v1'})['scanned']==0)
        check('imported schedules and future management use coherent snapshot reads',all(call('GET',path).get('read_consistency')=='snapshot' for path in ['/v1/installments','/v1/obligations','/v1/future-commitments/obligations']))

        original = {'_id':ObjectId(),'tipo':'financiamento','status':'futura','competencia':'2026-12','data_vencimento':datetime(2026,12,11,tzinfo=timezone.utc),'saldo_aberto':Decimal128('3700.25'),'valor_pago':Decimal128('0')}
        db.obligations.insert_one(copy.deepcopy(original)); path='/v1/future-commitments/obligation/'+str(original['_id'])
        with patch('app.future_commitments.today',return_value=date(2026,10,1)):
            body={'request_id':str(uuid4()),'preview_token':call('GET',path)['preview_token'],'rows':[{'row_id':1,'amount':'3600.13','due_date':'2026-10-20'}]}
            call('PUT',path,body)
        with patch('app.future_commitments.today',return_value=date(2026,11,1)):
            listing=call('GET','/v1/future-commitments/obligations')
            check('future management does not list corrected overdue obligation',not listing['items'])
            context=call('GET','/v1/insights/planning-context?start=2026-11-01&end=2026-11-30')
            flow=context['suggested_flows'][0]
            check('overdue correction remains in cash planning, with original due date',flow['overdue'] and flow['original_due_date']=='2026-10-20' and flow['amount']=='-3600.13')
        with patch('app.future_commitments.today',return_value=date(2026,10,1)):
            body={'request_id':str(uuid4()),'preview_token':call('GET',path)['preview_token'],'rows':[{'row_id':1,'amount':'3599.00'}]}
            db.reconciliations.insert_one({'obligation_id':original['_id']})
            call('PUT',path,body,409)
            check('payment link arriving after preview prevents correction',db.future_schedule_overrides.find_one({'_id':'obligation:'+str(original['_id'])})['revision']==1)
        managed=call('POST','/v1/commitments',{'creation_id':str(uuid4()),'kind':'assinatura','name':'Renovação antiga sintética','purchase_date':'2010-01-01','next_due_date':'2010-01-01','amount':'9.90','frequency':'semanal','payment_method':'pix','renewal_status':'automatica'},201)
        path='/v1/future-commitments/managed/'+managed['commitment_id']
        with patch('app.future_commitments.today',return_value=date(2026,10,1)):
            preview=call('GET',path)
            check('old weekly recurring subscription still exposes future deletion',preview['editable_count']>0 and len(preview['rows'])<=600)
            from app.main import _dashboard
            with patch('app.main.local_today',return_value=date(2026,10,1)):
                check('dashboard next thirty days agrees with old weekly planning',_dashboard('all',None,None,db)['commitment_due_count']==5)
            if preview['editable_count']:
                call('DELETE',path,{'request_id':str(uuid4()),'preview_token':preview['preview_token']})
                check('old weekly deletion converges with purchase planning',all(not flow['id'].startswith('commitment:'+managed['commitment_id']+':') for flow in call('GET','/v1/insights/planning-context?start=2026-10-01&end=2026-10-31')['flows']))
        check('legacy obligation remained exactly untouched through edits and payment conflict',db.obligations.find_one({'_id':original['_id']})==original)
        report={'database':name,'configured_financial_database_untouched':True,'checks':checks}
    report['passed']=all(row['passed'] for row in checks)
    target.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2));mongo.close()
    return report['passed']

if __name__=='__main__':
    sys.exit(0 if run(sys.argv[1],'--readonly' in sys.argv) else 1)
