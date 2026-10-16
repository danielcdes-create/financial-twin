"""Synthetic HTTP/replica-set checks in a new, retained test database only."""
import sys
import copy
import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from bson import ObjectId
from bson.decimal128 import Decimal128
from bson import json_util
from pymongo import MongoClient
from pymongo.collection import Collection
from observed_client import ObservedTestClient as TestClient
from app.main import app, configured_mongodb_uri
from app.config import get_settings
from app.synthetic_testing import require_synthetic_target
from app.auth import require_session


def run(output):
    settings = get_settings()
    require_synthetic_target(configured_mongodb_uri(settings))
    name = 'financial_twin_test_delete_' + uuid4().hex[:8]
    assert name != settings.mongodb_database and name.startswith('financial_twin_test_delete_')
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000, tz_aware=True)
    db = client[name]
    # No lifespan/recovery/index writes against the configured financial database.
    app.state.db = db
    app.dependency_overrides[require_session] = lambda: True
    http = TestClient(app, raise_server_exceptions=False)
    checks = []
    def check(label, condition):
        assert condition, label
        checks.append({'check': label, 'passed': True})
    def call(method, path, body=None, status=200):
        result = http.request(method, path, json=body)
        assert result.status_code == status, (method, path, result.status_code, result.text[:300])
        return result.json()
    stamp = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    account = ObjectId()
    db.accounts.insert_one({'_id': account, 'instituicao': 'Itaú', 'tipo': 'conta_corrente'})
    db.account_balance_snapshots.insert_one({'account_key': 'itau', 'as_of': datetime(2026, 9, 30, tzinfo=timezone.utc), 'amount': Decimal128('50')})
    row = {'_id': ObjectId(), 'valor': Decimal128('-12.59'), 'data_evento': stamp, 'account_id': account,
        'fluxo': 'saida', 'categoria': 'Alimentação', 'descricao_original': 'Duplicado sintético',
        'classification': {'migration_reason': 'despesa_real'}}
    other = {**row, '_id': ObjectId()}
    db.transactions.insert_many([row, other])
    db.raw_events.insert_one({'event_id': 'synthetic-evidence', 'processing_status': 'matched', 'captured_at': stamp,
        'parsed_payload':{'amount':'12.59','merchant_raw':'Duplicado sintético','event_timestamp':stamp,'direction':'expense'}})
    db.transaction_evidence.insert_one({'event_id': 'synthetic-evidence', 'transaction_id': row['_id']})
    base = '/v1/movements/ledger/' + str(row['_id'])
    body = {'preview_token': call('GET', base + '/deletion')['preview_token']}
    check('duplicate appears twice before deletion', call('GET', '/v1/movements?cursor_mode=true')['total'] == 2)
    call('DELETE', base, {}, 422)
    db.transactions.update_one({'_id': row['_id']}, {'$set': {'categoria': 'Saúde'}})
    call('DELETE', base, body, 409)
    check('stale preview does not delete', db.transactions.count_documents({}) == 2)
    check('active evidence blocks deletion instead of leaving a dangling link',not call('GET',base+'/deletion')['can_delete'])
    undone=call('POST','/v1/insights/evidence/synthetic-evidence/undo',{'request_id':str(uuid4()),'expected_revision':1,'reason':'Conferência sintética do vínculo antes de excluir'})
    call('POST','/v1/provisional/'+undone['provisional_id']+'/decision',{'request_id':str(uuid4()),'expected_revision':1,'action':'cancel'})
    check('explicit unlink preserves evidence history and source event',db.transaction_evidence.find_one({'event_id':'synthetic-evidence'})['status']=='reversed' and db.raw_events.count_documents({'event_id':'synthetic-evidence'})==1)
    body = {'preview_token': call('GET', base + '/deletion')['preview_token']}
    original = db.transactions.find_one({'_id': row['_id']})
    result = call('DELETE', base, body)
    check('one selected duplicate deleted', result['deleted'] and db.transactions.count_documents({}) == 1)
    archived = db.deleted_movements.find_one({'_id': 'ledger:' + str(row['_id'])})
    check('exact original retained', archived['document'] == original)
    check('evidence and raw retained', db.transaction_evidence.count_documents({}) == 1 and db.raw_events.count_documents({}) == 1)
    check('retry idempotent', call('DELETE', base, body)['duplicate'] and db.audit_log.count_documents({'event': 'movement_deleted'}) == 1)
    check('statement excludes deleted row', call('GET', '/v1/movements?cursor_mode=true')['total'] == 1)
    dashboard = call('GET', '/v1/dashboard?date_from=2026-10-01&date_to=2026-10-01')
    check('dashboard excludes deleted expense', dashboard['expenses'] == 12.59 and dashboard['transaction_count'] == 1)
    positions = call('GET', '/v1/account-positions')
    items = positions['items'] if isinstance(positions, dict) and 'items' in positions else positions
    if isinstance(items, dict): items = items.get('accounts', [])
    check('balance excludes deleted debit', next(item for item in items if item['key'] == 'itau')['balance'] == 37.41)
    # Commit failure must leave both the active row and archive consistent.
    other_base = '/v1/movements/ledger/' + str(other['_id'])
    other_body = {'preview_token': call('GET', other_base + '/deletion')['preview_token']}
    insert = Collection.insert_one
    def fail_audit(collection, *args, **kwargs):
        if collection.database.name == name and collection.name == 'audit_log':
            raise RuntimeError('synthetic audit outage')
        return insert(collection, *args, **kwargs)
    with patch.object(Collection, 'insert_one', fail_audit):
        response = http.request('DELETE', other_base, json=other_body)
        check('injected audit failure reported', response.status_code == 500)
    check('Mongo transaction rollback preserved active row', db.transactions.find_one({'_id': other['_id']}) is not None)
    check('Mongo transaction rollback removed incomplete archive', db.deleted_movements.count_documents({'_id': 'ledger:' + str(other['_id'])}) == 0)
    db.reconciliations.insert_one({'origem': {'entity': 'transactions', 'id': other['_id']}, 'destino': {'entity': 'obligations', 'id': ObjectId()}})
    check('reconciled payment blocked in preview', not call('GET', other_base + '/deletion')['can_delete'])
    call('DELETE', other_base, other_body, 409)
    capture = {'provisional_id': 'capture-delete-test', 'event_id': 'capture-delete-event', 'status': 'confirmed', 'revision': 1,
        'valor': Decimal128('-20.48'), 'data_evento': stamp, 'fluxo': 'saida', 'merchant_raw': 'Captura sintética'}
    db.provisional_transactions.insert_one(copy.deepcopy(capture))
    db.raw_events.insert_one({'event_id': capture['event_id'], 'processing_status': 'confirmed', 'captured_at': stamp})
    capture_base = '/v1/movements/capture/' + capture['provisional_id']
    capture_body = {'preview_token': call('GET', capture_base + '/deletion')['preview_token']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: http.request('DELETE', capture_base, json=capture_body).status_code, range(2)))
    check('concurrent confirmation never creates partial failure', sorted(replies) in ([200, 200], [200, 409]))
    check('capture retry after concurrency succeeds', call('DELETE', capture_base, capture_body)['duplicate'])
    check('capture cancelled; event identity retained', db.provisional_transactions.find_one({'provisional_id': capture['provisional_id']})['status'] == 'cancelled'
        and db.raw_events.find_one({'event_id': capture['event_id']})['processing_status'] == 'cancelled')
    check('capture excluded from statement', call('GET', '/v1/movements?cursor_mode=true')['total'] == 1)
    report = {'database': name, 'configured_financial_database_untouched': True, 'checks': checks, 'passed': True}
    Path(output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    client.close()


if __name__ == '__main__': run(sys.argv[1])
