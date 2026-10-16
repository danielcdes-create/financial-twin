"""HTTP and replica-set checks with synthetic statements in a new test DB."""
import sys
import json
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from decimal import Decimal
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
from app.transaction_service import TransactionService


def run(output):
    settings = get_settings()
    require_synthetic_target(configured_mongodb_uri(settings))
    name = 'financial_twin_test_batch_' + uuid4().hex[:8]
    assert name != settings.mongodb_database
    target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
    os.environ['MEDIA_STORAGE_PATH'] = str(target.parent / 'synthetic-media')
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000, tz_aware=True)
    db = client[name]
    app.state.db = db
    app.state.service = TransactionService(db)
    app.dependency_overrides[require_session] = lambda: True
    http = TestClient(app, raise_server_exceptions=False)
    db.capture_batch_locks.insert_one({'_id': 'movement-insert', 'revision': 0})
    db.provisional_transactions.create_index('provisional_id', unique=True)
    db.raw_events.create_index('event_id', unique=True)
    checks = []
    def check(label, condition):
        assert condition, label
        checks.append({'check': label, 'passed': True})
    def call(method, path, body=None, status=200):
        response = http.request(method, path, json=body)
        assert response.status_code == status, (method, path, response.status_code, response.text[:500])
        return response.json()
    account = ObjectId()
    db.accounts.insert_one({'_id': account, 'instituicao': 'Itaú', 'tipo': 'conta_corrente'})
    def ledger(desc, amount, day, bank_ref=None):
        return db.transactions.insert_one({'data_evento': datetime(2026, 9, day, 15, tzinfo=timezone.utc),
            'valor': Decimal128(amount), 'descricao_original': desc, 'account_id': account,
            'fluxo': 'saida', 'bank_transaction_ref': bank_ref, 'classification': {'migration_reason': 'despesa_real'}}).inserted_id
    original = ledger('Padaria sintética', '-12.59', 21, 'BANK-1')
    csv = ('data;descricao;valor;direcao;moeda;identificador_transacao\n21/09/2026;Padaria sintética;-12,59;debito;BRL;BANK-1\n'
        '22/09/2026;Loja sintética;-20,48;debito;BRL;BANK-2\n22/09/2026;Loja sintética;-20,48;debito;BRL;BANK-2\n'
        '23/09/2026;Salário sintético;2500,00;credito;BRL;BANK-3\n24/09/2026;Fatura sintética;-400,00;debito;BRL;BANK-4\n'
        '25/09/2026;Despesa tardia;-31,37;debito;BRL;BANK-5\n').encode('utf-8')
    def scan(data=csv):
        identity = str(uuid4())
        response = http.post('/v1/capture-batches/scan', data={'batch_id': identity}, files={'file': ('extrato.csv', data, 'text/csv')})
        assert response.status_code == 200, response.text
        return identity, response.json()
    batch_id, ack = scan()
    check('scan stores a draft, not financial movements', ack['draft_persisted'] and db.provisional_transactions.count_documents({}) == 0)
    retry = http.post('/v1/capture-batches/scan', data={'batch_id': batch_id}, files={'file': ('extrato.csv', csv, 'text/csv')})
    check('scan retry is idempotent', retry.status_code == 200 and db.capture_batches.count_documents({}) == 1)
    conflict = http.post('/v1/capture-batches/scan', data={'batch_id': batch_id}, files={'file': ('extrato.csv', csv + b' ', 'text/csv')})
    check('same batch ID cannot replace original file', conflict.status_code == 409)
    body = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True, 'confirmed_distinct': False} for row in ack['batch']['rows']]}
    body['rows'][4]['kind'] = 'bill_payment'
    base = '/v1/capture-batches/' + batch_id
    preview = call('POST', base + '/preview', body)
    check('snapshot blocks existing and intra-batch duplicate', preview['read_consistency'] == 'snapshot' and preview['duplicate_count'] == 2 and preview['insert_count'] == 4)
    ledger('Despesa tardia', '-31.37', 25, 'BANK-5')
    commit_body = {**body, 'preview_token': preview['preview_token']}
    result = call('POST', base + '/commit', commit_body)
    check('duplicate arriving after preview is blocked at insertion', result['inserted_count'] == 3 and result['rows'][5]['status'] == 'duplicate')
    documents = list(db.provisional_transactions.find({'capture_batch_id': batch_id}))
    check('reviewed cents, day and account are exact', sorted(str(row['valor']) for row in documents) == ['-20.48', '-400.00', '2500.00'] and all(row['account_id'] == account and row['data_evento'].hour == 15 for row in documents))
    check('bill payment remains economically neutral', next(row for row in documents if row['tipo'] == 'pagamento_fatura')['natureza'] is None)
    check('raw events reference actual retained documents', all(db.provisional_transactions.find_one({'_id': row['linked_transaction_id']}) for row in db.raw_events.find({})))
    check('original ledger preserved', db.transactions.find_one({'_id': original}) is not None)
    check('account-filtered statement includes batch entries', call('GET', '/v1/movements?cursor_mode=true&account_id=' + str(account))['total'] == 5)
    repeat = call('POST', base + '/commit', commit_body)
    check('lost response retry inserts nothing twice', repeat['duplicate_request'] and db.provisional_transactions.count_documents({}) == 3 and db.audit_log.count_documents({}) == 1)
    second, same = scan()
    same_body = {**body, 'rows': [{**row, 'selected': True, 'confirmed_distinct': False} for row in same['batch']['rows']]}
    same_body['rows'][4]['kind'] = 'bill_payment'
    recheck = call('POST', '/v1/capture-batches/' + second + '/preview', same_body)
    check('reimport with another batch ID is blocked by scoped bank references', recheck['insert_count'] == 0 and recheck['duplicate_count'] == 6)
    # Distinct files containing the same charge compete for the same insert fence.
    race_csv = b'data;descricao;valor;direcao;moeda;identificador_transacao\n26/09/2026;Corrida sinteticamente duplicada;-77,21;debito;BRL;RACE-1\n'
    race_bodies = []
    for suffix in (b'', b'\n'):
        identity, draft = scan(race_csv + suffix)
        review = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True} for row in draft['batch']['rows']]}
        token = call('POST', f'/v1/capture-batches/{identity}/preview', review)['preview_token']
        race_bodies.append((identity, {**review, 'preview_token': token}))
    def execute(item):
        identity, reviewed = item
        response = http.post(f'/v1/capture-batches/{identity}/commit', json=reviewed)
        assert response.status_code in (200, 409), response.text
        return response
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(execute, race_bodies))
    results = [call('POST', f'/v1/capture-batches/{identity}/commit', reviewed) if reply.status_code == 409 else reply.json()
               for (identity, reviewed), reply in zip(race_bodies, replies)]
    check('concurrent confirmations insert exactly one charge', sum(row['inserted_count'] for row in results) == 1 and db.provisional_transactions.count_documents({'valor': Decimal128('-77.21')}) == 1)
    # An audit failure must roll back captures, raw events and the batch result together.
    identity, draft = scan('data;descricao;valor;direcao;moeda\n27/09/2026;Rollback sintético;-18,93;debito;BRL\n'.encode('utf-8'))
    reviewed = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True} for row in draft['batch']['rows']]}
    token = call('POST', f'/v1/capture-batches/{identity}/preview', reviewed)['preview_token']
    reviewed['preview_token'] = token
    count = db.provisional_transactions.count_documents({}); raw_count = db.raw_events.count_documents({})
    insert = Collection.insert_one
    def fail_audit(collection, *args, **kwargs):
        if collection.database.name == name and collection.name == 'audit_log': raise RuntimeError('synthetic audit outage')
        return insert(collection, *args, **kwargs)
    with patch.object(Collection, 'insert_one', fail_audit):
        response = http.post(f'/v1/capture-batches/{identity}/commit', json=reviewed)
        check('injected audit failure reported', response.status_code == 500)
    check('failure rolls back rows, events and workflow', db.provisional_transactions.count_documents({}) == count and db.raw_events.count_documents({}) == raw_count and db.capture_batches.find_one({'_id': identity})['status'] == 'draft')
    check('same confirmation can recover after rollback', call('POST', f'/v1/capture-batches/{identity}/commit', reviewed)['inserted_count'] == 1)
    check('source file is retained', Path(db.capture_batches.find_one({'_id': batch_id})['storage_key']).read_bytes() == csv)
    service = TransactionService(db)
    lock_revision = db.capture_batch_locks.find_one({'_id': 'movement-insert'})['revision']
    service.process({'event_id': str(uuid4()), 'source_type': 'android_notification', 'source_provider': 'synthetic_test',
        'captured_at': datetime(2026, 9, 28, 15, tzinfo=timezone.utc), 'raw_payload': {'text': 'Synthetic notification'},
        'parsed_payload': {'amount': '91.57', 'currency': 'BRL', 'merchant_raw': 'Notificação sintética', 'direction': 'expense'},
        'parser_version': 'synthetic-v1'})
    check('existing notification ingestion takes the shared Mongo fence', db.capture_batch_locks.find_one({'_id': 'movement-insert'})['revision'] == lock_revision + 1)
    identity, draft = scan('data;descricao;valor;direcao;moeda\n28/09/2026;Notificação sintética;-91,57;debito;BRL\n'.encode('utf-8'))
    reviewed = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True} for row in draft['batch']['rows']]}
    notification_preview = call('POST', f'/v1/capture-batches/{identity}/preview', reviewed)
    check('notification with unknown account cannot silently be duplicated', notification_preview['insert_count'] == 0 and notification_preview['rows'][0]['status'] == 'possible_duplicate')
    # Removal is a retained workflow tombstone, not deletion of the source/ledger.
    discard_csv = b'data;descricao;valor;direcao;moeda\n29/09/2026;Lote para remover;-19,31;debito;BRL\n'
    identity, draft = scan(discard_csv); sibling, sibling_draft = scan(discard_csv)
    path = f'/v1/capture-batches/{identity}'
    reviewed = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True} for row in draft['batch']['rows']]}
    token = call('POST', path + '/preview', reviewed)['preview_token']
    discarded_body = {'expected_sha256': draft['batch']['sha256']}
    from bson import json_util
    def financial_state():
        return {name: json_util.dumps(list(db[name].find({}).sort('_id', 1)), sort_keys=True)
                for name in ('transactions', 'provisional_transactions', 'deleted_movements', 'raw_events', 'accounts')}
    before = financial_state()
    call('DELETE', path, {'expected_sha256': '0' * 64}, status=409)
    check('stale file hash cannot remove a batch', db.capture_batches.find_one({'_id': identity})['status'] == 'draft')
    removed = call('DELETE', path, discarded_body)
    check('removal has persisted acknowledgement and no financial mutation', removed['persisted'] and removed['status'] == 'discarded' and financial_state() == before)
    audit_count = db.audit_log.count_documents({})
    check('lost removal response is retryable without repeated audit', call('DELETE', path, discarded_body)['duplicate_request'] and db.audit_log.count_documents({}) == audit_count)
    check('only chosen batch disappears from pending list', identity not in [row['batch_id'] for row in call('GET', '/v1/capture-batches')['items']] and db.capture_batches.find_one({'_id': sibling})['status'] == 'draft')
    check('shared source bytes and extraction survive removal', Path(db.capture_batches.find_one({'_id': identity})['storage_key']).read_bytes() == discard_csv and db.statement_extractions.find_one({'_id': discarded_body['expected_sha256']}) is not None)
    call('POST', path + '/preview', reviewed, status=409)
    call('POST', path + '/commit', {**reviewed, 'preview_token': token}, status=409)
    retry = http.post('/v1/capture-batches/scan', data={'batch_id': identity}, files={'file': ('extrato.csv', discard_csv, 'text/csv')})
    check('stale preview and upload retry cannot resurrect discarded batch', retry.status_code == 200 and retry.json()['batch']['status'] == 'discarded' and not retry.json()['needs_review'] and financial_state() == before)
    call('DELETE', base, {'expected_sha256': ack['batch']['sha256']}, status=409)
    check('committed batch cannot be removed as pending', db.capture_batches.find_one({'_id': batch_id})['status'] == 'committed')
    # Inject failure after the status write: both status and audit must roll back.
    with patch.object(Collection, 'insert_one', fail_audit):
        response = http.request('DELETE', f'/v1/capture-batches/{sibling}', json=discarded_body)
        check('injected removal audit outage reported', response.status_code == 500)
    check('removal audit outage rolls back tombstone atomically', db.capture_batches.find_one({'_id': sibling})['status'] == 'draft')
    for index in range(6):
        identity, draft = scan(f'data;descricao;valor;direcao;moeda\n30/09/2026;Disputa remover {index};-{55+index},01;debito;BRL\n'.encode())
        reviewed = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True} for row in draft['batch']['rows']]}
        path = f'/v1/capture-batches/{identity}'
        reviewed['preview_token'] = call('POST', path + '/preview', reviewed)['preview_token']
        def compete(action):
            return http.request(action, path if action == 'DELETE' else path + '/commit',
                                json={'expected_sha256': draft['batch']['sha256']} if action == 'DELETE' else reviewed)
        with ThreadPoolExecutor(max_workers=2) as pool: replies = list(pool.map(compete, ['DELETE', 'POST']))
        assert all(reply.status_code in (200, 409) for reply in replies), [reply.text for reply in replies]
        status = db.capture_batches.find_one({'_id': identity})['status']
        count = db.provisional_transactions.count_documents({'capture_batch_id': identity})
        check(f'concurrent discard and commit {index+1} have one atomic outcome', (status == 'discarded' and count == 0) or (status == 'committed' and count == 1))
        losing_action = 'POST' if status == 'discarded' else 'DELETE'
        check(f'losing action {index+1} stays rejected on retry', compete(losing_action).status_code == 409)
    # Domain audit regressions: currencies and weak coincidences cannot apply a prefix.
    for currency in ('USD', 'EUR', ''):
        foreign_bytes = (f'data;descricao;valor;direcao;moeda\n01/10/2026;Moeda valida sintetica;-9,53;debito;BRL\n'
                         f'01/10/2026;Moeda a conferir sintetica;-12,59;debito;{currency}\n').encode()
        identity, draft = scan(foreign_bytes)
        reviewed = {'account_ref': 'ledger:' + str(account), 'acknowledge_brl': False,
                    'rows': [{**row, 'selected': True} for row in draft['batch']['rows']]}
        prefix_count = db.provisional_transactions.count_documents({}); prefix_events = db.raw_events.count_documents({})
        compared = call('POST', f'/v1/capture-batches/{identity}/preview', reviewed)
        call('POST', f'/v1/capture-batches/{identity}/commit', {**reviewed, 'preview_token': compared['preview_token']}, status=422)
        check(f'currency {currency or "unknown"} prevents the complete financial prefix', db.provisional_transactions.count_documents({}) == prefix_count and db.raw_events.count_documents({}) == prefix_events and db.capture_batches.find_one({'_id': identity})['status'] == 'draft')
        check(f'currency {currency or "unknown"} retains exact original bytes', Path(db.capture_batches.find_one({'_id': identity})['storage_key']).read_bytes() == foreign_bytes)
    equal_bytes = b'data;descricao;valor;direcao;moeda\n02/10/2026;Duas compras legitimas;-12,59;debito;BRL\n02/10/2026;Duas compras legitimas;-12,59;debito;BRL\n'
    identity, draft = scan(equal_bytes)
    reviewed = {'account_ref': 'ledger:' + str(account), 'rows': [{**row, 'selected': True, 'confirmed_distinct': row['row_id'] == 2} for row in draft['batch']['rows']]}
    compared = call('POST', f'/v1/capture-batches/{identity}/preview', reviewed)
    reviewed['preview_token'] = compared['preview_token']
    inserted = call('POST', f'/v1/capture-batches/{identity}/commit', reviewed)
    check('two unreferenced equal purchases explicitly confirmed distinct insert twice', inserted['inserted_count'] == 2 and sum(row['valor'].to_decimal() for row in db.provisional_transactions.find({'capture_batch_id': identity})) == Decimal('-25.18'))
    check('distinct equal purchases preserve command idempotency', call('POST', f'/v1/capture-batches/{identity}/commit', reviewed)['duplicate_request'] and db.provisional_transactions.count_documents({'capture_batch_id': identity}) == 2)
    legacy_bytes = (b'data;descricao;valor;direcao;moeda;conta;identificador_transacao\n03/10/2026;Legacy reference shop;-12,59;debito;BRL;SYNTHETIC-ACCOUNT;LEGACY-1\n'
                    b'03/10/2026;Legacy reference shop;-12,59;debito;BRL;SYNTHETIC-ACCOUNT;LEGACY-2\n')
    operation = str(uuid4())
    def legacy(data, operation):
        response = http.post('/v1/import', data={'event_id': operation, 'source_provider': 'synthetic_bank'}, files={'file': ('legacy.csv', data, 'text/csv')})
        assert response.status_code == 200, response.text
        return response.json()
    initial = legacy(legacy_bytes, operation)
    query = {'source_provider': 'synthetic_bank', 'account_identity': 'SYNTHETIC-ACCOUNT'}
    check('legacy references persist as two reviewable movements', initial['accepted'] == 2 and db.provisional_transactions.count_documents(query) == 2)
    check('legacy command retry preserves exact aggregate ACK', legacy(legacy_bytes, operation) == initial)
    lines = legacy_bytes.splitlines()
    legacy(b'\n'.join([lines[0], lines[2], lines[1]]) + b'\n\n', str(uuid4()))
    check('legacy bank references survive bytes and row order changes', db.provisional_transactions.count_documents(query) == 2)
    failed_bytes = legacy_bytes.replace(b'LEGACY-1', b'LEGACY-FAIL-1').replace(b'LEGACY-2', b'LEGACY-FAIL-2')
    failed_operation = str(uuid4()); raw_before = db.raw_events.count_documents({}); capture_before = db.provisional_transactions.count_documents({})
    original_insert = Collection.insert_one
    def fail_second_legacy(collection, document, *args, **kwargs):
        if collection.database.name == name and collection.name == 'provisional_transactions' and document.get('bank_transaction_ref') == 'LEGACY-FAIL-2':
            raise RuntimeError('synthetic second legacy movement outage')
        return original_insert(collection, document, *args, **kwargs)
    with patch.object(Collection, 'insert_one', fail_second_legacy):
        failed = http.post('/v1/import', data={'event_id': failed_operation, 'source_provider': 'synthetic_bank'}, files={'file': ('legacy.csv', failed_bytes, 'text/csv')})
    failed_command = db.batch_commands.find_one({'_id': 'upload:' + failed_operation})
    check('second legacy reference outage atomically rolls back financial prefix and ACK', failed.status_code == 500 and db.raw_events.count_documents({}) == raw_before and db.provisional_transactions.count_documents({}) == capture_before and failed_command['status'] == 'awaiting_upload' and not failed_command.get('result'))
    recovered = legacy(failed_bytes, failed_operation)
    check('same legacy command recovers once after second-row rollback', recovered['accepted'] == 2 and legacy(failed_bytes, failed_operation) == recovered and db.provisional_transactions.count_documents({}) == capture_before + 2)
    app.dependency_overrides.pop(require_session)
    call('DELETE', path, {'expected_sha256': draft['batch']['sha256']}, status=401)
    check('batch removal requires an authenticated session', True)
    report = {'database': name, 'configured_financial_database_untouched': True, 'checks': checks, 'passed': True}
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2)); client.close()


if __name__ == '__main__': run(sys.argv[1])
