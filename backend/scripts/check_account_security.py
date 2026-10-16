"""Real Mongo/API security probes, only in a disposable synthetic database."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient
from pymongo import MongoClient
from app.main import app, configured_mongodb_uri
from app.config import get_settings
from app.auth import OWNER_ID, MAX_SESSIONS, login_attempts, owner, session_response
from app.synthetic_testing import require_synthetic_target


def main():
    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError('Configure MongoDB para executar a integração isolada.')
    uri=configured_mongodb_uri(settings)
    require_synthetic_target(uri)
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    name = 'financial_twin_test_account_' + uuid4().hex[:8]
    assert name.startswith('financial_twin_test_account_')
    db = client[name]
    old_db = getattr(app.state, 'db', None)
    app.state.db = db
    secure = SimpleNamespace(jwt_secret='isolated-test-signing-' * 3, app_password='',
                             account_setup_token='isolated-setup-only-' * 3, environment='development')
    http = TestClient(app)
    checks = 0

    def expect(response, status):
        nonlocal checks
        assert response.status_code == status, (response.status_code, response.text)
        checks += 1
        assert response.headers.get('cache-control') == 'no-store'
        return response.json()

    def headers(token): return {'Authorization': 'Bearer ' + token}
    def login(password='Synthetic-password-1!'):
        login_attempts.attempts.clear()
        return expect(http.post('/v1/auth/session', json={'username': 'tester', 'password': password, 'device_label': 'Test Android'}), 200)['access_token']

    try:
        with patch('app.auth.get_settings', return_value=secure), patch('app.account_auth.get_settings', return_value=secure):
            login_attempts.attempts.clear()
            db.accounts.insert_one({'_id': 'bank-fixture', 'tipo': 'conta_corrente', 'titular': 'Synthetic'})
            before = list(db.accounts.find({}))
            expect(http.get('/v1/accounts'), 401)
            assert expect(http.get('/v1/auth/status'), 200)['setup_required']
            signup = {'username': 'tester', 'display_name': 'Synthetic Owner', 'password': 'Synthetic-password-1!',
                      'setup_token': 'wrong-code-' * 5, 'device_label': 'Test browser'}
            expect(http.post('/v1/auth/setup', json=signup), 401)
            assert owner(db) is None
            signup['setup_token'] = secure.account_setup_token
            token = expect(http.post('/v1/auth/setup', json=signup), 201)['access_token']
            expect(http.post('/v1/auth/setup', json=signup), 409)
            assert not expect(http.get('/v1/auth/status'), 200)['setup_required']
            data = expect(http.get('/v1/auth/account', headers=headers(token)), 200)
            assert set(data) == {'username', 'display_name'}
            assert 'password_hash' not in http.get('/v1/auth/account', headers=headers(token)).text
            expect(http.post('/v1/auth/session', json={'username': 'intruder', 'password': signup['password']}), 401)
            expect(http.post('/v1/auth/session', json={'username': 'tester', 'password': 'wrong'}), 401)
            expect(http.get('/v1/auth/account', headers=headers(token + 'x')), 401)
            other = login()
            sessions = expect(http.get('/v1/auth/sessions', headers=headers(token)), 200)['items']
            assert len(sessions) == 2 and sum(item['current'] for item in sessions) == 1
            remote = next(item for item in sessions if not item['current'])
            expect(http.delete('/v1/auth/sessions/' + remote['id'], headers=headers(token)), 200)
            expect(http.get('/v1/auth/account', headers=headers(other)), 401)
            expect(http.patch('/v1/auth/account', headers=headers(token), json={'username': 'tester', 'display_name': 'Updated Owner'}), 200)
            other = login()
            login_attempts.attempts.clear()
            expect(http.post('/v1/auth/password', headers=headers(token), json={'current_password': 'wrong', 'new_password': 'Synthetic-password-2!'}), 403)
            expect(http.get('/v1/auth/account', headers=headers(token)), 200)
            body = expect(http.post('/v1/auth/password', headers=headers(token), json={'current_password': signup['password'], 'new_password': 'Synthetic-password-2!'}), 200)
            token2 = body['access_token']
            for revoked in [token, other]: expect(http.get('/v1/auth/account', headers=headers(revoked)), 401)
            expect(http.get('/v1/auth/account', headers=headers(token2)), 200)
            login_attempts.attempts.clear()
            expect(http.post('/v1/auth/session', json={'username': 'tester', 'password': signup['password']}), 401)
            token3 = login('Synthetic-password-2!')
            expect(http.post('/v1/auth/logout', headers=headers(token3)), 200)
            expect(http.get('/v1/auth/account', headers=headers(token3)), 401)
            token3 = login('Synthetic-password-2!')
            expect(http.post('/v1/auth/logout-all', headers=headers(token3)), 200)
            for revoked in [token2, token3]: expect(http.get('/v1/auth/account', headers=headers(revoked)), 401)

            # Real Mongo atomic session append under concurrency, never unbounded or lost appends.
            account = owner(db)
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(lambda index: session_response(db, account, f'Parallel {index}'), range(20)))
            assert len(owner(db)['sessions']) == MAX_SESSIONS
            accepted = [http.get('/v1/auth/account', headers=headers(item['access_token'])).status_code for item in responses]
            assert accepted.count(200) == MAX_SESSIONS and accepted.count(401) == 20 - MAX_SESSIONS
            checks += 2
            login_attempts.attempts.clear()
            for _ in range(10): expect(http.post('/v1/auth/session', json={'username': 'tester', 'password': 'wrong'}), 401)
            expect(http.post('/v1/auth/session', json={'username': 'tester', 'password': 'wrong'}), 429)
            assert list(db.accounts.find({})) == before, 'Financial account history must remain untouched'
            assert len(list(db.account.find({}))) == 1
            assert set(owner(db)) <= {'_id', 'kind', 'schema_version', 'username', 'display_name', 'password_hash', 'auth_version', 'sessions', 'created_at'}
            checks += 3
            # Only disposable fixture identity: competing initial registrations cannot take ownership twice.
            db.account.delete_one({'_id': OWNER_ID})
            login_attempts.attempts.clear()
            with ThreadPoolExecutor(max_workers=2) as pool:
                competing = list(pool.map(lambda index: http.post('/v1/auth/setup', json={**signup, 'username': f'owner{index}'}), range(2)))
            assert sorted(item.status_code for item in competing) == [201, 409]
            assert db.account.count_documents({}) == 1
            assert list(db.accounts.find({})) == before
            checks += 3
            print(f'PASS: {checks} API/Mongo probes, concurrent session cap, revocation, privacy and preserved bank accounts.')
    finally:
        app.state.db = old_db
        login_attempts.attempts.clear()
        client.drop_database(name)
        client.close()


if __name__ == '__main__': main()
