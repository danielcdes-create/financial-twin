"""NF07/08/10/11/12 and capture clock gates on a disposable local replica set.

No application lifespan, financial database, device or external AI is started.
"""
import copy
import io
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.errors import WriteError

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parents[1] / 'tests')]
from app import main
from app.account_positions import account_positions
from app.analytics import calculate_financial_totals
from app.capture_batches import BatchCommit, BatchReview, commit, fingerprint
from app.economic_policy import effects
from app.financial_time import local_date
from app.month_close import view
from app.services.ai.xai.provider import _post
from app.source_tokens import source_token
from app.synthetic_testing import require_synthetic_target
from app.transaction_service import TransactionService
from test_domain_account_time_privacy import fixture
from test_matching_scope_conservative import ConservativeMatchingScopeTests


def run(output, uri='mongodb://127.0.0.1:27017/?replicaSet=financialTwinTest'):
    require_synthetic_target(uri)
    assert urlsplit(uri).hostname in {'127.0.0.1', 'localhost', '::1'}, 'Local replica set only'
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)
    hello = client.admin.command('hello')
    assert hello.get('setName') == 'financialTwinTest', 'Explicit synthetic replica set required'
    assert hello.get('isWritablePrimary'), 'Synthetic primary must be writable'
    name = 'financial_twin_test_domain_account_' + uuid4().hex[:24]
    assert name not in client.list_database_names()
    db = client[name]
    results = []
    report = {'database': name, 'replica_set': hello['setName'], 'target': 'loopback',
              'financial_database_accessed': False, 'external_ai': False, 'lifespan_started': False,
              'checks': results, 'database_dropped': False}
    collections = ('accounts', 'account_profiles', 'account_balance_snapshots', 'transactions',
        'merchants', 'provisional_transactions', 'raw_events', 'audit_log', 'capture_batch_locks',
        'capture_batches', 'batch_commands', 'refund_claims', 'commitments', 'transaction_evidence',
        'review_queue', 'schema_migrations', 'transaction_descriptor_index')

    def save():
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding='utf-8')

    def check(identity, scenario, condition, **details):
        results.append({'id': identity, 'scenario': scenario, 'passed': bool(condition), **details})
        save()
        assert condition, (identity, scenario, details)

    def clear():
        for collection in db.list_collection_names():
            db[collection].delete_many({})
        db.capture_batch_locks.insert_one({'_id': 'movement-insert', 'revision': 0})

    def seed(fake):
        for collection, rows in fake.items():
            if rows.docs:
                db[collection].insert_many(copy.deepcopy(rows.docs))

    def income():
        return {'_id': ObjectId(), 'valor': Decimal128('100.00'), 'fluxo': 'entrada', 'tipo': 'receita',
                'categoria': 'Viagem', 'descricao_original': 'REEMBOLSO AMIGO VIAGEM',
                'data_evento': datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
                'classification': {'migration_reason': 'receita_ou_movimentacao'}}

    try:
        for collection in collections:
            db.create_collection(collection)
        clear()
        row = income()
        db.transactions.insert_one(row)
        before = effects(row)
        ledger_body = main.TransactionClassificationUpdate(request_id=uuid4(), expected_token=source_token(row),
            detalhe='Nota explícita sintética', validate_review=True)
        result = main.update_transaction_classification(str(row['_id']), ledger_body, db)
        final = db.transactions.find_one({'_id': row['_id']})
        audit = db.audit_log.find_one({'event': 'transaction_classification_updated'})
        check('NF07', 'Ledger note and review retain empty economic nature',
            'natureza' not in final and effects(final) == before and audit['after'] == {'detalhe': 'Nota explícita sintética'},
            before_effects=before, after_effects=effects(final), after_audit=audit['after'])
        digest = source_token(final)
        repeated = main.update_transaction_classification(str(row['_id']), ledger_body, db)
        check('NF07', 'Ledger command replay precedes old-token validation',
            repeated['request_id'] == result['request_id'] and source_token(db.transactions.find_one({'_id': row['_id']})) == digest
            and db.audit_log.count_documents({'event': 'transaction_classification_updated'}) == 1)

        capture = {**income(), 'provisional_id': 'synthetic-note', 'event_id': str(uuid4()),
            'revision': 1, 'status': 'confirmed', 'categoria_sugerida': 'Viagem', 'merchant_raw': 'REEMBOLSO AMIGO VIAGEM'}
        db.provisional_transactions.insert_one(capture)
        capture_body = main.ProvisionalUpdate(request_id=uuid4(), expected_revision=1,
            detalhe='Nota explícita da captura', validate_review=True)
        main.update_provisional(capture['provisional_id'], capture_body, db)
        final = db.provisional_transactions.find_one({'_id': capture['_id']})
        digest = source_token(final)
        main.update_provisional(capture['provisional_id'], capture_body, db)
        check('NF07', 'Confirmed capture note retains effects and UUID replay does not bump revision',
            'natureza' not in final and effects(final, True) == effects(capture, True)
            and final['revision'] == 2 and source_token(db.provisional_transactions.find_one({'_id': capture['_id']})) == digest)
        accepted = main.TransactionClassificationUpdate(expected_token=source_token(db.transactions.find_one({'_id': row['_id']})), natureza='ESTORNO')
        main.update_transaction_classification(str(row['_id']), accepted, db)
        final = db.transactions.find_one({'_id': row['_id']})
        check('NF07', 'Explicit nature acceptance changes economics and audits ESTORNO',
            effects(final)['income'] == 0 and effects(final)['expenses'] == -100
            and db.audit_log.count_documents({'event': 'transaction_classification_updated', 'after.natureza': 'ESTORNO'}) == 1)

        clear()
        source = income()
        platform = {'_id': ObjectId(), 'nome_padrao': 'Plataforma sintética'}
        peers = [{**income(), 'subcategoria': 'Cinema', 'metodo_pagamento': 'debito',
                  'platform_id': platform['_id'], 'plataforma': 'Plataforma legada'},
                 {**income(), 'subcategoria': 'Passeios', 'plataforma': 'Somente legada'}]
        db.merchants.insert_one(platform)
        db.transactions.insert_many([source, *peers])
        db.transaction_descriptor_index.insert_many([{'_id': row['_id'], 'key': main.canonical_key(row['descricao_original'])}
                                                     for row in [source, *peers]])
        db.schema_migrations.insert_one({'_id': 'descriptor_index_v1', 'complete': True, 'scanned': 3})
        token = main.classification_match_preview(str(source['_id']), db)['preview_token']
        body = main.TransactionClassificationUpdate(expected_token=source_token(source),
            subcategoria='', metodo_pagamento='', plataforma='', apply_similar_classification=True,
            classification_preview_token=token)
        command_id = 'synthetic-classification-' + uuid4().hex
        db.batch_commands.insert_one({'_id': command_id, 'kind': 'classification', 'transaction_id': str(source['_id']),
            'payload': body.model_dump(mode='json'), 'status': 'pending', 'attempts': 0, 'created_at': datetime.now(timezone.utc)})
        before = list(db.transactions.find({}).sort('_id', 1))
        db.command('collMod', 'audit_log', validator={'event': {'$ne': 'transaction_classification_bulk_propagated'}},
                   validationLevel='strict', validationAction='error')
        rejected = False
        try:
            main.run_classification_command(db, command_id)
        except WriteError as failure:
            rejected = failure.code == 121
        finally:
            db.command('collMod', 'audit_log', validator={})
        check('NF08', 'Real Mongo validator failure rolls back source, every peer, audit and running command',
            rejected and list(db.transactions.find({}).sort('_id', 1)) == before
            and db.audit_log.count_documents({}) == 0
            and db.batch_commands.find_one({'_id': command_id})['status'] == 'pending')
        result = main.run_classification_command(db, command_id)
        main.run_classification_command(db, command_id)
        audit = db.audit_log.find_one({'event': 'transaction_classification_bulk_propagated'})
        records = {entry['id']: entry for entry in audit['matched_records']}
        reconstructed = audit['transaction_count'] == 2 and set(records) == {peer['_id'] for peer in peers}
        for peer in peers:
            record = records[peer['_id']]
            final = db.transactions.find_one({'_id': peer['_id']})
            reconstructed &= record['before']['subcategoria'] == peer['subcategoria'] and record['after']['subcategoria'] == ''
            for field in ('platform_id', 'plataforma'):
                reconstructed &= record['before'][field] == peer.get(field) and record['after'][field] is None
                reconstructed &= field in record['after_missing'] and field not in final
                reconstructed &= (field in record['before_missing']) == (field not in peer)
        check('NF08', 'Every committed peer retains actual before/after new and legacy values; retry audit occurs once',
            reconstructed and result['classification_propagated_count'] == 2
            and db.audit_log.count_documents({'event': 'transaction_classification_bulk_propagated'}) == 1,
            matched_records=audit['matched_records'])

        clear()
        fake, _, accounts = fixture('itau')
        fake.transactions.docs.clear()
        seed(fake)
        batch_id = str(uuid4())
        review = BatchReview(account_ref='ledger:' + str(accounts['itau']), rows=[{
            'row_id': 1, 'date': '2026-10-01', 'amount': '-26.00', 'description': 'PADARIA SINTÉTICA',
            'kind': 'expense', 'currency': 'BRL'}])
        batch = {'_id': batch_id, 'batch_id': batch_id, 'sha256': 'a' * 64, 'status': 'draft',
            'complete': True, 'storage_key': 'synthetic:statement', 'rows': [row.model_dump(mode='json') for row in review.rows]}
        db.capture_batches.insert_one(batch)
        ack = commit(batch_id, BatchCommit(**review.model_dump(), preview_token=fingerprint(batch, review)), db)
        identity = ack['inserted'][0]['provisional_id']
        assignment = main.ProvisionalUpdate(request_id=uuid4(), expected_revision=1, account_profile_id='santander')
        main.update_provisional(identity, assignment, db)
        persisted = db.provisional_transactions.find_one({'provisional_id': identity})
        digest = source_token(persisted)
        main.update_provisional(identity, assignment, db)
        flows = {group['id']: group['balance']['flow'] for group in view(db, '2026-10')['groups'] if group.get('balance')}
        positions = {row['key']: row['balance'] for row in account_positions(db)}
        audit = db.audit_log.find_one({'event': 'provisional_transaction_updated'})
        check('NF10', 'Assignment and replay agree across filters, cash positions, month flows and provenance',
            main.cursor_movements(50, None, None, None, str(accounts['itau']), None, None, db)['total'] == 0
            and main.cursor_movements(50, None, None, None, 'profile:santander', None, None, db)['total'] == 1
            and (flows['account:itau'], flows['account:santander']) == ('0.00', '-26.00')
            and (positions['itau'], positions['santander']) == (100, 74)
            and persisted['source_account_identity']['account_id'] == accounts['itau']
            and persisted['account_id'] == accounts['santander']
            and audit['before']['account_id'] == str(accounts['itau'])
            and audit['after']['account_id'] == str(accounts['santander'])
            and source_token(db.provisional_transactions.find_one({'provisional_id': identity})) == digest,
            flows=flows, positions={'itau': positions['itau'], 'santander': positions['santander']})

        clear()
        fake, event, _ = fixture('santander')
        seed(fake)
        result = TransactionService(db).process(event)
        capture = db.provisional_transactions.find_one({'event_id': event['event_id']})
        check('NF11', 'Itaú notification cannot absorb Santander ledger or invent its account',
            result['status'] != 'matched' and db.transaction_evidence.count_documents({}) == 0
            and capture is not None and 'account_profile_id' not in capture)
        main.update_provisional(capture['provisional_id'], main.ProvisionalUpdate(expected_revision=1,
            account_profile_id='itau', natureza='COMPRA', action='confirm'), db)
        totals = calculate_financial_totals(list(db.transactions.find({})), list(db.provisional_transactions.find({'status': 'confirmed'})))
        positions = {row['key']: row['balance'] for row in account_positions(db)}
        check('NF11', 'Explicitly confirmed distinct purchases total R$52 and debit each own bank once',
            totals['expenses'] == 52 and (positions['itau'], positions['santander']) == (74, 74),
            expenses=totals['expenses'], positions={'itau': positions['itau'], 'santander': positions['santander']})
        for bank, matched in (('itau', True), ('santander', False)):
            clear()
            fake, event, _ = fixture(bank)
            seed(fake)
            result = TransactionService(db).process(event)
            check('NF11', 'Bank control: ' + bank, (result['status'] == 'matched') == matched)
        for account in (None, '', 'ACCOUNT-1'):
            clear()
            fake, event, _ = ConservativeMatchingScopeTests().fixture(account)
            seed(fake)
            result = TransactionService(db).process(event)
            check('NF11', 'Strong reference requires known account scope: ' + repr(account),
                (result['status'] == 'matched') == (account == 'ACCOUNT-1'))
        clear()
        fake, event, _ = ConservativeMatchingScopeTests().fixture(provider='com.unknown.bank')
        seed(fake)
        event.update(source_type='android_notification', raw_payload={'package': 'com.unknown.bank'})
        event['parsed_payload'].pop('bank_transaction_ref')
        result = TransactionService(db).process(event)
        check('NF11', 'Unknown native package never auto reconciles', result['status'] != 'matched')

        clear()
        fake, event, _ = fixture('itau')
        fake.transactions.docs.clear()
        seed(fake)
        db.raw_events.insert_one({**event, 'processing_status': 'pending',
            'received_at': datetime.now(timezone.utc) - timedelta(minutes=2)})
        service = TransactionService(db)
        marker = 'SYNTHETIC-FINANCIAL-SECRET'
        failure = WriteError(marker, 121, {'errInfo': {'consideredValue': marker}})
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        try:
            with patch.object(service, '_finish', side_effect=failure):
                service.recover_pending()
            db.batch_commands.insert_one({'_id': 'synthetic-recovery', 'status': 'pending', 'attempts': 0,
                'created_at': datetime.now(timezone.utc) - timedelta(minutes=2)})
            with patch.object(main, 'run_classification_command', side_effect=failure):
                main.recover_classification_commands(db)
        finally:
            logging.getLogger().removeHandler(handler)
        event_row = db.raw_events.find_one({'event_id': event['event_id']})
        command_row = db.batch_commands.find_one({'_id': 'synthetic-recovery'})
        check('NF12', 'Driver consideredValue never reaches logs or durable recovery errors; retry identity survives',
            marker not in stream.getvalue() and marker not in event_row['processing_error']
            and marker not in command_row['last_error'] and event_row['processing_status'] == 'pending'
            and event_row['processing_attempts'] == 1 and event_row['event_id'] == event['event_id']
            and event_row.get('next_attempt_at') and command_row['status'] == 'pending' and command_row['attempts'] == 1
            and 'code=121' in event_row['processing_error'] and 'code=121' in command_row['last_error'],
            event_error=event_row['processing_error'], command_error=command_row['last_error'])

        for stamp, expected in (('2026-10-01T00:10:00', '2026-10-01T03:10:00+00:00'),
                                ('2026-10-01T23:40:00', '2026-10-02T02:40:00+00:00'),
                                ('2026-10-02T02:40:00Z', '2026-10-02T02:40:00+00:00')):
            clear()
            fake, event, _ = fixture()
            fake.transactions.docs.clear()
            seed(fake)
            event.update(source_type='receipt', source_provider='synthetic-receipt', raw_payload={})
            event['parsed_payload']['event_timestamp'] = stamp
            TransactionService(db).process(event)
            row = db.provisional_transactions.find_one({'event_id': event['event_id']})
            raw = db.raw_events.find_one({'event_id': event['event_id']})
            check('UTC', 'Naive receipt wall clock and explicit UTC preserve Brasília civil date: ' + stamp,
                row['data_evento'].isoformat() == expected and local_date(row['data_evento']).isoformat() == '2026-10-01'
                and raw['ingest_payload']['parsed_payload']['event_timestamp'] == stamp,
                stored_instant=row['data_evento'], local_day=local_date(row['data_evento']))
        clear()
        fake, event, _ = fixture('itau')
        fake.transactions.docs[0]['data_evento'] = datetime(2026, 10, 2, 2, 40, tzinfo=timezone.utc)
        seed(fake)
        event.update(source_type='receipt', source_provider='synthetic-receipt', raw_payload={})
        event['parsed_payload']['event_timestamp'] = '2026-10-01T23:40:00'
        result = TransactionService(db).process(event)
        evidence = db.transaction_evidence.find_one({'event_id': event['event_id']})
        raw = db.raw_events.find_one({'event_id': event['event_id']})
        check('UTC', 'Matching scores equivalent Brasília wall clock and UTC ledger as the same instant',
            result['status'] == 'matched' and evidence['score_components']['time'] == 1
            and raw['ingest_payload']['parsed_payload']['event_timestamp'] == '2026-10-01T23:40:00',
            temporal_score=evidence['score_components']['time'])
        update = main.ProvisionalUpdate(data_evento='2026-10-01T23:40:00')
        check('UTC', 'Naive manual review edit means Brasília wall clock',
            update.data_evento.isoformat() == '2026-10-02T02:40:00+00:00')
        for stamp, evidence, expected in (('2026-10-01T23:40:00Z', None, '2026-10-02T02:40:00+00:00'),
                                           ('2026-10-02T02:40:00Z', 'UTC', '2026-10-02T02:40:00+00:00')):
            document = {'document_type': 'receipt', 'datetime': stamp, 'timezone_evidence': evidence,
                'total': 26, 'currency': 'BRL', 'merchant': 'PADARIA SINTÉTICA'}
            payload = {'output': [{'content': [{'type': 'output_text', 'text': json.dumps(document)}]}]}
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                parsed = _post('https://synthetic.invalid', 'synthetic-placeholder', {'model': 'synthetic'})
            check('UTC', 'Mocked AI Z requires printed timezone evidence: ' + repr(evidence),
                parsed.datetime.astimezone(timezone.utc).isoformat() == expected,
                extracted_wall_clock=parsed.datetime, timezone_evidence=parsed.timezone_evidence)
        report['passed'] = all(row['passed'] for row in results)
    except Exception as failure:
        report['passed'] = False
        report['failure'] = type(failure).__name__ + ': ' + str(failure)
        raise
    finally:
        assert db.name == name and name.startswith('financial_twin_test_domain_account_')
        client.drop_database(name)
        report['database_dropped'] = name not in client.list_database_names()
        client.close()
        save()
    print(json.dumps({'checks': len(results), 'passed': report['passed'], 'database_dropped': report['database_dropped']}))


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'mongodb://127.0.0.1:27017/?replicaSet=financialTwinTest')
