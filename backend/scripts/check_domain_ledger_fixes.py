"""NF01/02/03/09 against an isolated local MongoDB replica set. No app lifespan."""
import copy
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from fastapi import HTTPException
from pymongo import MongoClient
from app.atomic import _SessionCollection
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.main import (configured_mongodb_uri, CommitmentPaymentLink, link_commitment_payment,
                      TransactionClassificationUpdate, update_transaction_classification, _dashboard)
from app.account_positions import account_positions
from app.commitments import CommitmentIn, stored_document, schedule, future_pending_total
from app.financial_time import day_start
from app.source_tokens import source_token
from app.month_close import get_checklist, ItemUpdate, update_item, Version, close_month
from app.insights import planning_context, closure


def run(output):
    target = Path(output).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    uri = configured_mongodb_uri(get_settings()); require_synthetic_target(uri)
    name = 'financial_twin_test_domain_' + uuid4().hex[:12]
    assert name.startswith('financial_twin_test_domain_')
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000)
    client.admin.command('ping')
    assert client.admin.command('hello').get('setName'), 'A replica set is required for this probe.'
    db = client[name]; checks = []
    collections = ('accounts','transactions','account_profiles','account_balance_snapshots','provisional_transactions',
        'audit_log','capture_batch_locks','commitments','review_queue','credit_card_bills','obligations',
        'installment_plans','reconciliations','future_schedule_overrides','batch_commands','classification_commands',
        'source_records','investments','transaction_descriptor_index','merchants','merchant_aliases','platforms',
        'platform_aliases','transaction_evidence','raw_events','deleted_movements')
    fixture = Path(__file__).parents[1] / 'tests' / 'fixtures'
    for collection in collections:
        options = json.loads((fixture / (collection + '-validator.json')).read_text(encoding='utf-8')) if collection in {'accounts','transactions'} else {}
        db.create_collection(collection, **options)
    db.commitments.create_index('commitment_id', unique=True)
    db.commitments.create_index('linked_transactions.transaction_id', unique=True, sparse=True)

    def reset():
        for collection in collections: db[collection].delete_many({})
        db.capture_batch_locks.insert_one({'_id':'movement-insert','revision':0})

    def check(label, condition):
        assert condition, label
        checks.append({'check':label,'passed':True})

    def account(institution, kind='conta_corrente'):
        now=datetime.now(timezone.utc); identity=ObjectId()
        db.accounts.insert_one({'_id':identity,'external_key':str(identity),'schema_version':2,'instituicao':institution,
            'tipo':kind,'titular':'Pessoa sintética','moeda':'BRL','status':'ativo','created_at':now,'updated_at':now})
        return identity

    def movement(identity, day, value='-29.90', competence=None, nature=None):
        now=datetime.now(timezone.utc); oid=ObjectId(); source=ObjectId()
        row={'_id':oid,'external_key':str(oid),'schema_version':2,'account_id':identity,'data_evento':day_start(day),
            'competencia':competence or day.strftime('%Y-%m'),'valor':Decimal128(value),'moeda':'BRL',
            'fluxo':'saida' if value.startswith('-') else 'entrada','tipo':'compra','descricao_original':'Fonte sintética '+str(oid),
            'categoria':'Sintética','localidade_contexto':'nao_determinado','localidade_real_validada':False,
            'origem_localidade':'nao_informada','source':{'source_record_id':source,'identificador_original':str(oid),'fonte':'synthetic'},
            'status_conciliacao':'nao_aplicavel','classification':{'migration_reason':'despesa_real'},'created_at':now,'updated_at':now}
        if nature:row['natureza']=nature
        db.transactions.insert_one(row); return row

    def payment(item, tx, **values):
        return link_commitment_payment(item['commitment_id'], CommitmentPaymentLink(
            expected_revision=item['revision'],expected_source_token=source_token(tx),transaction_id=str(tx['_id']),**values),db)

    try:
        reset(); origin=account('Itaú'); destination=account('Santander')
        source=movement(origin,date(2026,9,30),'-100.00',nature='TRANSFERENCIA')
        opposite=movement(destination,date(2026,9,30),'100.00',nature='TRANSFERENCIA')
        for key,value in (('itau','900.00'),('santander','600.00')):
            db.account_balance_snapshots.insert_one({'account_key':key,'amount':Decimal128(value),
                'as_of':day_start(date(2026,10,1)),'reference_kind':'civil_day','as_of_date':'2026-09-30'})
        captured={'status':'confirmed','data_evento':day_start(date(2026,10,1)),'valor':Decimal128('-100.00'),
            'natureza':'TRANSFERENCIA','transfer':{'from_account_key':'itau','to_account_key':'santander',
                'source_transaction_id':str(source['_id']),'destination_transaction_id':str(opposite['_id']),'reconciliation_status':'conciliada'}}
        db.provisional_transactions.insert_one(captured)
        positions={row['key']:row for row in account_positions(db)}
        check('NF01 linked September legs preserve October references and zero movement counts',
              [positions[key]['balance'] for key in ('itau','santander')]==[900,600] and
              [positions[key]['movement_count'] for key in ('itau','santander')]==[0,0])
        monthly=get_checklist('2026-10',db)
        check('NF01 October account flows remain zero in Mongo snapshot',all(row['balance']['flow']=='0.00'
            for row in monthly['groups'] if row['id'] in {'account:itau','account:santander'}) and monthly['read_consistency']=='snapshot')
        db.transactions.delete_one({'_id':opposite['_id']})
        check('NF01 broken link is pending without inventing a capture leg',next(row for row in account_positions(db)
            if row['key']=='santander')['pending_transfer_count']==1)

        reset(); bank=account('Itaú')
        item=stored_document(CommitmentIn(kind='parcelamento',name='Parcelas sintéticas',purchase_date=date(2026,1,1),
            next_due_date=date(2026,1,31),amount='29.90',installment_count=3,next_installment_number=1,payment_method='pix'))
        db.commitments.insert_one(item); first=movement(bank,date(2026,2,1)); second=movement(bank,date(2026,2,28))
        request=uuid4(); result=payment(item,first,request_id=request)
        check('NF02 lost response replays exact occurrence ACK before stale revision/token',payment(item,first,request_id=request)==result)
        current=db.commitments.find_one({'commitment_id':item['commitment_id']}); payment(current,second)
        current=db.commitments.find_one({'commitment_id':item['commitment_id']})
        check('NF02 distinct January/February occurrences paid in February leave exactly one March installment',
            current['next_installment_number']==3 and current['next_due_date'].date()==date(2026,3,31)
            and future_pending_total(current)==Decimal128('29.90').to_decimal()
            and [link['due_date'] for link in current['linked_transactions']]==['2026-01-31','2026-02-28'])
        final=movement(bank,date(2026,3,31)); before=copy.deepcopy(current)
        final_before=db.transactions.find_one({'_id':final['_id']})
        original_insert=_SessionCollection.__getattr__
        def fail_audit(collection,method):
            if method=='insert_one' and collection.collection.name=='audit_log':
                def fail(*args,**kwargs):raise RuntimeError('Synthetic audit failure')
                return fail
            return original_insert(collection,method)
        with patch.object(_SessionCollection,'__getattr__',fail_audit):
            try:payment(current,final,request_id=uuid4())
            except RuntimeError:pass
            else:raise AssertionError('Audit failure was not triggered.')
        check('NF02 Mongo transaction rolls back payment occurrence, revision and agenda after audit failure',
            db.commitments.find_one({'commitment_id':item['commitment_id']})==before
            and db.transactions.find_one({'_id':final['_id']})==final_before)
        try:payment(current,final,historical_only=True,occurrence_due_date=date(2026,3,31))
        except HTTPException as exc:assert exc.status_code==422
        else:raise AssertionError('Historical-only current occurrence was accepted.')
        check('NF02 historical-only current occurrence is rejected without changing Mongo history',
              db.commitments.find_one({'commitment_id':item['commitment_id']})==before)
        db.commitments.update_one({'commitment_id':item['commitment_id']},{'$push':{'linked_transactions':{
            'transaction_id':str(final['_id']),'due_date':'2026-03-31','paid_date':'2026-03-31',
            'occurrence_key':'due:2026-03-31','settled_next':False}}})
        contradicted=db.commitments.find_one({'commitment_id':item['commitment_id']}); preserved=copy.deepcopy(contradicted)
        checklist=get_checklist('2026-03',db)
        review=next(entry for group in checklist['groups'] for entry in group['items'] if entry['id']=='general:commitments')
        check('NF02 contradictory persisted historical occurrence is preserved pending without financial forecasts',
            schedule(contradicted)==[] and future_pending_total(contradicted) is None and bool(review['blockers'])
            and planning_context(date(2026,3,1),date(2026,3,31),db)['flows']==[]
            and db.commitments.find_one({'commitment_id':item['commitment_id']})==preserved)

        reset(); bank=account('Itaú')
        db.account_balance_snapshots.insert_one({'account_key':'itau','amount':Decimal128('100.00'),
            'as_of':day_start(date(2026,10,4)),'reference_kind':'civil_day'})
        item=stored_document(CommitmentIn(kind='assinatura',name='Última assinatura',purchase_date=date(2026,1,1),
            next_due_date=date(2026,10,9),end_date=date(2026,10,9),amount='29.90',payment_method='pix',
            account_id='profile:itau',renewal_status='automatica'))
        db.commitments.insert_one(item); tx=movement(bank,date(2026,10,4)); payment(item,tx)
        current=db.commitments.find_one({'commitment_id':item['commitment_id']})
        check('NF09 final early subscription payment concludes and removes every future projection',
            current['status']=='concluido' and schedule(current)==[] and future_pending_total(current)==0
            and planning_context(date(2026,10,4),date(2026,10,31),db)['flows']==[]
            and next(row for row in account_positions(db) if row['key']=='itau')['balance']==70.1)

        reset(); card=account('Cartão sintético','cartao_credito'); tx=movement(card,date(2026,8,31),'-100.00')
        update_transaction_classification(str(tx['_id']),TransactionClassificationUpdate(competencia='2026-09',expected_token=source_token(tx)),db)
        check('NF03 declared September competence governs MonthClose, dashboard optimized read and FinancialLab',
            get_checklist('2026-08',db)['totals']['expenses']=='0.00'
            and get_checklist('2026-09',db)['totals']['expenses']=='100.00'
            and _dashboard('month',date(2026,9,1),date(2026,9,30),db)['expenses']==100
            and closure(date(2026,9,1),date(2026,9,30),db)['expenses']=='100.00'
            and db.transactions.find_one({'_id':tx['_id']})['data_evento']==tx['data_evento'])
        for group in get_checklist('2026-09',db)['groups']:
            for entry in group['items']:
                state=get_checklist('2026-09',db)
                current_entry=next(item for section in state['groups'] for item in section['items'] if item['id']==entry['id'])
                update_item('2026-09',entry['id'],ItemUpdate(expected_revision=state['revision'],
                    expected_source_token=current_entry['source_token'],status='not_applicable' if group['kind']=='account' else 'done',
                    note='Conta sem uso neste teste sintético' if group['kind']=='account' else ''),db)
        closed=close_month('2026-09',Version(expected_revision=get_checklist('2026-09',db)['revision']),db)
        current_tx=db.transactions.find_one({'_id':tx['_id']})
        update_transaction_classification(str(tx['_id']),TransactionClassificationUpdate(competencia='2026-10',expected_token=source_token(current_tx)),db)
        current=get_checklist('2026-09',db)
        check('NF03 competence change preserves closed snapshot and signals explicit reconference',
            current['status']=='closed' and current['source_changed'] and current['totals']['expenses']=='0.00'
            and current['last_close']==closed['last_close'] and current['last_close']['totals']['expenses']=='100.00')
        report={'database':name,'synthetic_only':True,'real_financial_data_accessed':False,'strict_legacy_validators':True,
            'mongo_replica_set':True,'checks':checks,'passed':len(checks),'physical_device_tested':False}
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        assert db.name.startswith('financial_twin_test_domain_')
        client.drop_database(db.name);client.close()


if __name__=='__main__':run(sys.argv[1])
