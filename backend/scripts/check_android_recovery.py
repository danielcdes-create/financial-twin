"""Android recovery acceptance on an explicit, isolated Mongo replica set.

No lifespan, production settings, financial DB reads, cleanup or external AI.
Fresh namespaces and media are retained with the JSON report for inspection.
"""
import argparse
import copy
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

PREFIX = 'financial_twin_test_android_recovery_'
REPLICA_SET = 'financialTwinTest'


def isolated_uri(uri):
    if not uri or not re.match(r'^mongodb://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?/', uri, re.I):
        raise ValueError('Envie uma MONGODB_URI explícita, local e isolada; não use a configuração financeira.')
    if parse_qs(urlsplit(uri).query).get('replicaSet') != [REPLICA_SET]:
        raise ValueError('Este gate exige o replicaSet sintético financialTwinTest.')
    return uri


def run(output, uri, race_iterations=4):
    uri = isolated_uri(uri)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Imports of app.main can load Settings; establish the explicit test target
    # first, without allowing any routine to use a financial default database.
    os.environ['MONGODB_URI'] = uri
    os.environ['MONGODB_DATABASE'] = PREFIX + 'unused_default'
    os.environ['MEDIA_STORAGE_PATH'] = str(output / 'synthetic-media')
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from bson import ObjectId
    from bson.codec_options import CodecOptions
    from bson.decimal128 import Decimal128
    from fastapi import HTTPException, UploadFile
    from pymongo import MongoClient, version as pymongo_version
    from starlette.datastructures import Headers
    import app.main as main
    import app.movement_insert_lock as insert_lock
    from app.account_profiles import AccountProfileCreate, AccountProfileUpdate, AccountProfileDelete, account_usage
    from app.cards import BillEdit, edit_bill, token
    from app.commitment_projection import monthly_projection
    from app.commitments import CommitmentIn, schedule
    from app.financial_time import day_start
    from app.insights import _planning_context
    from app.media_store import reconcile_media
    from app.schemas_document import DocumentExtraction
    from app.source_tokens import source_token
    from app.transaction_service import TransactionService, stored_instant

    client = MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)
    checks, databases = [], []
    report = {'production_access': False, 'replica_set': REPLICA_SET, 'pymongo_version':pymongo_version,
              'database_prefix': PREFIX, 'checks': checks, 'databases': databases}
    collections = ('accounts account_profiles account_balance_snapshots commitments commitment_amount_rules '
                   'batch_commands audit_log capture_batch_locks raw_events transaction_evidence '
                   'provisional_transactions review_queue transactions credit_card_bills obligations '
                   'future_schedule_overrides reconciliations capture_batches deleted_movements '
                   'saving_experiments merchants categories category_overrides subcategories '
                   'media_assets workflow_state').split()

    def check(name, condition, db=None, **evidence):
        if not condition:
            raise AssertionError(name)
        checks.append({'check': name, 'passed': True, **({'database': db.name} if db is not None else {}), **evidence})

    def new_db():
        name = PREFIX + uuid4().hex[:24]
        if not re.fullmatch(re.escape(PREFIX) + r'[0-9a-f]{24}', name):
            raise ValueError('Namespace sintético inválido.')
        db = client[name]
        if db.list_collection_names():
            raise ValueError('O namespace deve estar vazio e ser exclusivo desta execução.')
        databases.append(name)
        for collection in collections:
            db.create_collection(collection)
        db.commitments.create_index('commitment_id', unique=True)
        db.raw_events.create_index('event_id', unique=True)
        db.media_assets.create_index('sha256', unique=True)
        return db

    def profile(db):
        return main.add_account_profile(AccountProfileCreate(request_id=uuid4(), instituicao='Banco sintético',
            apelido='Conta de aceitação', tipo='conta_corrente'), db)['item']

    def commitment(db, account=None, **changes):
        values = dict(kind='assinatura', name='Assinatura sintética', purchase_date='2026-10-01',
                      next_due_date='2026-10-20', amount='100.00', payment_method='pix',
                      renewal_status='automatica', creation_id=uuid4())
        if account is not None:
            values['account_id'] = 'profile:' + account['key']
        body = CommitmentIn(**{**values, **changes})
        return body, main.create_commitment(body, db)

    def change_profile(db, item, **changes):
        values = {key: item[key] for key in AccountProfileUpdate.model_fields if key in item}
        return main.edit_account_profile(item['key'], AccountProfileUpdate(**{
            **values, 'expected_revision': item['revision'], 'request_id': uuid4(), **changes}), db)

    def expect_http(code, task):
        try:
            task()
        except HTTPException as error:
            if error.status_code != code:
                raise AssertionError(f'HTTP {error.status_code}; esperado {code}: {error.detail}') from error
            return
        raise AssertionError(f'Esperado HTTP {code}')

    def voice(db, identity, captured_at=None):
        upload = UploadFile(filename=f'voice-{identity}.txt', file=io.BytesIO(b'Gastei oito reais no cafe hoje'),
                            headers=Headers({'content-type': 'text/plain'}))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, service=TransactionService(db))))
        kwargs = {'event_id': identity}
        if captured_at is not None:
            kwargs['captured_at'] = captured_at
        return main.extract_document_sync(upload, 'mobile_voice', db, request, **kwargs)

    def race(kind, iteration):
        db = new_db()
        item = profile(db)
        if kind == 'create':
            body = CommitmentIn(kind='assinatura', name='Corrida sintética', purchase_date='2026-10-01',
                next_due_date='2026-10-20', amount='100.00', payment_method='pix',
                account_id='profile:'+item['key'], renewal_status='automatica', creation_id=uuid4())
            posting = lambda: main.create_commitment(body, db)
        else:
            body, created = commitment(db)
            editing = CommitmentIn(**{**body.model_dump(), 'account_id':'profile:'+item['key'],
                                     'revision':1, 'request_id':uuid4()})
            posting = lambda: main.update_commitment(created['commitment_id'], editing, db)
        deleting = lambda: main.remove_account_profile(item['key'],
            AccountProfileDelete(expected_revision=1, request_id=uuid4()), db)
        original, barrier, seen, guard = insert_lock.lock_movement_insert, Barrier(2), set(), Lock()

        def synchronized(writing, *args, **kwargs):
            thread = get_ident()
            with guard:
                first = thread not in seen
                seen.add(thread)
            # A transaction retry must not wait for an already completed peer.
            if first:
                barrier.wait(timeout=15)
            return original(writing, *args, **kwargs)

        def outcome(task):
            try:
                task()
                return {'status': 'success'}
            except HTTPException as error:
                if error.status_code not in (409, 422):
                    raise
                return {'status': error.status_code, 'detail': str(error.detail)}

        with patch('app.main.lock_movement_insert', synchronized), patch.object(insert_lock, 'lock_movement_insert', synchronized):
            with ThreadPoolExecutor(max_workers=2) as workers:
                tasks = [workers.submit(outcome, posting), workers.submit(outcome, deleting)]
                outcomes = [task.result(timeout=30) for task in tasks]
        deleted = bool(db.account_profiles.find_one({'key': item['key']}).get('deleted'))
        references = db.commitments.count_documents({'account_id': 'profile:'+item['key']})
        references += db.commitment_amount_rules.count_documents({'account_id': 'profile:'+item['key']})
        check(f'BE01 {kind} versus delete race {iteration}',
              not (deleted and references) and any(result['status'] == 'success' for result in outcomes),
              db, profile_deleted=deleted, references=references, outcomes=outcomes)

    try:
        hello = client.admin.command('hello')
        if hello.get('setName') != REPLICA_SET or not hello.get('isWritablePrimary'):
            raise ValueError('MongoDB não é o primary do replicaSet sintético esperado.')
        for state in ('ativo', 'pausado', 'cancelado', 'concluido'):
            db = new_db()
            item = profile(db)
            commitment(db, item, status=state)
            db.commitments.update_many({}, {'$set': {'future_deleted_at': datetime.now(timezone.utc)}})
            expect_http(409, lambda: main.remove_account_profile(item['key'],
                AccountProfileDelete(expected_revision=1, request_id=uuid4()), db))
            check(f'BE01 {state} history blocks account deletion',
                  account_usage(db, item['key']).get('compromissos') == 1 and
                  not db.account_profiles.find_one({'key': item['key']}).get('deleted'), db)

        db = new_db()
        legacy = ObjectId()
        db.accounts.insert_one({'_id': legacy, 'tipo':'conta_corrente', 'instituicao':'Itaú'})
        for identity in ('profile:itau', str(legacy), legacy):
            db.commitments.insert_one({'commitment_id':str(uuid4()), 'account_id': identity, 'status':'concluido'})
            db.commitment_amount_rules.insert_one({'account_id': identity})
        usage = account_usage(db, 'itau')
        check('BE01 legacy references and amount rules counted',
              usage.get('compromissos') == 3 and usage.get('regras_de_valor') == 3, db)

        for changes in ({'status':'encerrada'}, {'archived':True}):
            db = new_db()
            item = profile(db)
            body, created = commitment(db, item)
            change_profile(db, item, **changes)
            edited = main.update_commitment(created['commitment_id'], CommitmentIn(**{
                **body.model_dump(), 'revision':1, 'status':'cancelado', 'notes':'Conta histórica', 'request_id':uuid4()}), db)
            expect_http(422, lambda: main.create_commitment(CommitmentIn(**{
                **body.model_dump(), 'creation_id':uuid4()}), db))
            check('BE02 historical account retained '+next(iter(changes)),
                  edited['account_id'] == body.account_id and edited['status'] == 'cancelado', db)

        db = new_db()
        card_id, bill_id, obligation_id = ObjectId(), ObjectId(), ObjectId()
        db.accounts.insert_one({'_id':card_id, 'tipo':'cartao_credito', 'instituicao':'Cartão sintético'})
        due = day_start(date(2026, 10, 20))
        bill = {'_id':bill_id, 'card_account_id':card_id, 'competencia':'2026-10', 'data_vencimento':due,
                'status':'aberta', 'valor_total':Decimal128('100'), 'valor_pago':Decimal128('0'),
                'saldo_aberto':Decimal128('100'), 'novos_lancamentos':Decimal128('100')}
        obligation = {'_id':obligation_id, 'tipo':'fatura_cartao', 'origem_id':bill_id, 'competencia':'2026-10',
                      'data_vencimento':due, 'status':'futura', 'saldo_aberto':Decimal128('100'),
                      'valor_pago':Decimal128('0'), 'valor_original':Decimal128('100'), 'principal':Decimal128('100')}
        db.credit_card_bills.insert_one(bill)
        db.obligations.insert_one(obligation)
        with patch('app.cards.today', return_value=date(2026, 10, 9)):
            edit_bill(str(bill_id), BillEdit(expected_token=token(bill, {}, [obligation]), competencia='2026-10',
                data_vencimento='2026-10-20', valor_total='120.00', request_id=uuid4()), db)
        context = _planning_context(date(2026, 10, 9), date(2026, 11, 9), db)
        with patch('app.commitment_projection.today', return_value=date(2026, 10, 9)):
            projection = monthly_projection(db, 1)
        check('BE03 edited bill offered once and agrees with projection',
              len(context['suggested_flows']) == 1 and context['suggested_flows'][0]['amount'] == '-120.00' and
              projection['items'][0]['bill_balance'] == '120.00' and context['read_consistency'] == 'snapshot', db)

        db = new_db()
        stamp = datetime(2026, 10, 8, 15, tzinfo=timezone.utc)
        account, transaction = ObjectId(), ObjectId()
        db.accounts.insert_one({'_id':account, 'tipo':'conta_corrente', 'instituicao':'Itaú'})
        tx = {'_id':transaction, 'account_id':account, 'data_evento':stamp, 'valor':Decimal128('-8.00'),
              'fluxo':'saida', 'tipo':'compra', 'descricao_original':'LOJA EXEMPLO',
              'classification':{'migration_reason':'despesa_real'}}
        db.transactions.insert_one(tx)
        event = {'event_id':str(uuid4()), 'source_type':'android_notification', 'source_provider':'com.itau',
                 'captured_at':stamp+timedelta(days=1), 'raw_payload':{'package':'com.itau', 'text':'Nova compra LOJA EXEMPLO'},
                 'parsed_payload':{'amount':'8.00', 'currency':'BRL', 'merchant_raw':'LOJA EXEMPLO',
                 'direction':'expense', 'event_type':'card_purchase', 'event_timestamp':(stamp+timedelta(days=1)).isoformat()}}
        service = TransactionService(db)
        result = service.process(event)
        check('BE04 next-day independent purchase remains reviewable', result['status'] == 'review_required' and
              db.provisional_transactions.count_documents({}) == 1 and db.transaction_evidence.count_documents({}) == 0, db)
        check('BE04 replay keeps one event and capture', service.process(event)['duplicate'] and
              db.raw_events.count_documents({}) == 1 and db.provisional_transactions.count_documents({}) == 1, db)
        reference = 'durable-bank-reference'
        db.transactions.update_one({'_id':transaction}, {'$set':{'bank_transaction_ref':reference,
            'source_provider':'com.itau', 'account_identity':'synthetic-account'}})
        scoped = copy.deepcopy(event)
        scoped.update({'event_id':str(uuid4()), 'captured_at':stamp})
        scoped['parsed_payload'].update({'event_timestamp':stamp.isoformat(), 'bank_transaction_ref':reference,
                                        'account_identity':'synthetic-account'})
        check('BE04 strong scoped reference still reconciles', service.process(scoped)['status'] == 'matched', db)

        for count in (1, 600):
            db = new_db()
            body, created = commitment(db, kind='parcelamento', installment_count=count,
                next_installment_number=count, next_due_date='2026-10-09', renewal_status='nao_aplicavel')
            tx = {'_id':ObjectId(), 'data_evento':day_start(date(2026, 10, 9)), 'valor':Decimal128('-100'),
                  'tipo':'compra', 'fluxo':'saida', 'categoria':'Sintética', 'classification':{'migration_reason':'despesa_real'}}
            db.transactions.insert_one(tx)
            main.link_commitment_payment(created['commitment_id'], main.CommitmentPaymentLink(transaction_id=str(tx['_id']),
                expected_revision=1, expected_source_token=source_token(tx), request_id=uuid4()), db)
            completed = db.commitments.find_one({'commitment_id':created['commitment_id']})
            values = {key:value for key,value in completed.items() if key in CommitmentIn.model_fields}
            main.update_commitment(created['commitment_id'], CommitmentIn(**{
                **values, 'notes':'Nota após quitação', 'request_id':uuid4()}), db)
            saved = db.commitments.find_one({'commitment_id':created['commitment_id']})
            check(f'BE05 {count}/{count} notes edit preserves completion and links',
                  saved['status'] == 'concluido' and saved['next_installment_number'] == count+1 and
                  saved['linked_transactions'] == completed['linked_transactions'] and
                  saved['next_due_date'] == completed['next_due_date'] and not schedule(saved), db)

        extraction = DocumentExtraction(document_type='receipt', total='8.00', currency='BRL', merchant='Café sintético')
        db = new_db()
        first_id, second_id = uuid4(), uuid4()
        with patch('app.main.XAITextProvider.extract_text', return_value=extraction) as ai:
            first = voice(db, first_id, stamp)
            replay = voice(db, first_id, stamp)
            second = voice(db, second_id, stamp+timedelta(days=1))
        rows = list(db.provisional_transactions.find({}).sort('data_evento', 1))
        check('BE06 equal text new UUIDs preserve occurrences and capture dates',
              first['processing']['persisted_event_id'] == str(first_id) and
              second['processing']['persisted_event_id'] == str(second_id) and
              db.raw_events.count_documents({}) == 2 and len(rows) == 2 and
              [stored_instant(row['data_evento']) for row in rows] == [stamp, stamp+timedelta(days=1)], db)
        check('BE06 same UUID reproduces ACK without extraction or extra media',
              first == replay and ai.call_count == 2 and db.media_assets.count_documents({}) == 1, db)
        expect_http(409, lambda: voice(db, first_id, stamp+timedelta(days=1)))
        check('BE06 changed capture time conflicts without new event', db.raw_events.count_documents({}) == 2, db)

        db = new_db()
        with patch('app.main.XAITextProvider.extract_text', return_value=extraction) as ai:
            legacy_first = voice(db, uuid4())
            legacy_second = voice(db, uuid4())
        check('BE06 legacy receipt identity and upload context retained', ai.call_count == 1 and
              db.raw_events.count_documents({}) == 1 and
              legacy_first['processing']['persisted_event_id'] == legacy_second['processing']['persisted_event_id'] and
              all(row['context'] == {'source_provider':'mobile_voice', 'mime_type':'text/plain'}
                  for row in db.batch_commands.find({'kind':'capture_upload'})), db)

        db = new_db()
        lost_id = uuid4()
        with patch('app.main.XAITextProvider.extract_text', return_value=extraction) as ai:
            with patch('app.main.complete_upload', side_effect=RuntimeError('Synthetic ACK interruption')):
                try:
                    voice(db, lost_id, stamp)
                except RuntimeError as error:
                    if str(error) != 'Synthetic ACK interruption':
                        raise
                else:
                    raise AssertionError('ACK interruption did not run')
            resumed = voice(db, lost_id, stamp)
        check('BE06 lost ACK resumes original raw event without extraction', ai.call_count == 1 and
              db.raw_events.count_documents({}) == 1 and db.provisional_transactions.count_documents({}) == 1 and
              resumed['processing']['persisted_event_id'] == str(lost_id), db)

        for kind in ('create', 'update'):
            for iteration in range(1, race_iterations+1):
                race(kind, iteration)

        db = new_db()
        data = b'Synthetic retained BSON media'
        media = output / 'synthetic-media' / 'bson-orphan-original'
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(data)
        db.media_assets.insert_one({'sha256':sha256(data).hexdigest(), 'path':str(media), 'status':'media_ready',
                                    'created_at':datetime.now(timezone.utc)-timedelta(hours=2)})
        naive_db = db.with_options(codec_options=CodecOptions(tz_aware=False))
        check('OP BSON UTC-naive media reconciles and preserves bytes',
              naive_db.media_assets.find_one({})['created_at'].tzinfo is None and
              reconcile_media(naive_db)['orphaned'] == 1 and media.read_bytes() == data, db)
        report.update({'passed':True, 'count':len(checks)})
    except Exception as error:
        report.update({'passed':False, 'count':len(checks), 'error':f'{type(error).__name__}: {error}'})
        raise
    finally:
        (output / 'android-recovery-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--uri', default=os.environ.get('MONGODB_URI'))
    parser.add_argument('--race-iterations', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.race_iterations <= 20:
        parser.error('--race-iterations deve estar entre 1 e 20')
    run(args.output, args.uri, args.race_iterations)
