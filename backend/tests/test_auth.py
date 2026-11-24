import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.auth import (BIOMETRIC_SESSION_SECONDS, MAX_SESSIONS, OWNER_ID, create_session_token, renew_session_token, require_session,
                      session_hash, session_response, hash_password, verify_password, LoginAttempts)
from app.main import app


class _StatefulAccountCollection:
    def __init__(self, get_doc):
        self._get_doc = get_doc

    def find_one(self, query):
        doc = self._get_doc()
        if not doc:
            return None
        for k, v in (query or {}).items():
            if k == 'sessions.id':
                if not any(s.get('id') == v for s in doc.get('sessions', [])):
                    return None
            elif doc.get(k) != v:
                return None
        return doc

    def update_one(self, query, update):
        doc = self.find_one(query)
        if doc is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        if isinstance(update, list):
            # Aggregation pipeline used by session_response: filter expired + append + slice(-MAX_SESSIONS)
            stage = update[0].get('$set', {}).get('sessions', {})
            slice_args = stage.get('$slice', [])
            concat_args = slice_args[0].get('$concatArrays', [])
            filter_spec = concat_args[0].get('$filter', {})
            cutoff_now = filter_spec.get('cond', {}).get('$gt', [None, 0])[1]
            appended = list(concat_args[1].get('$literal', []))
            cap = abs(int(slice_args[1]))
            kept = [s for s in doc.get('sessions', []) if int(s.get('expires_at') or 0) > cutoff_now]
            doc['sessions'] = (kept + appended)[-cap:]
            return SimpleNamespace(matched_count=1, modified_count=1)
        sets = update.get('$set', {})
        target_sid = (query or {}).get('sessions.id')
        for k, v in sets.items():
            if k.startswith('sessions.$.'):
                sub = k.split('sessions.$.', 1)[1]
                for s in doc.get('sessions', []):
                    if s.get('id') == target_sid:
                        s[sub] = v
                        break
            else:
                doc[k] = v
        if '$inc' in update:
            for k, v in update['$inc'].items():
                doc[k] = doc.get(k, 0) + v
        if '$pull' in update and 'sessions' in update['$pull']:
            cond = update['$pull']['sessions']
            doc['sessions'] = [s for s in doc.get('sessions', []) if s.get('id') != cond.get('id')]
        return SimpleNamespace(matched_count=1, modified_count=1)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(jwt_secret='x' * 48, environment='development', app_password='', account_setup_token='z' * 48)
        self.settings_patch = patch('app.auth.get_settings', return_value=self.settings)
        self.settings_patch.start()
        self.identity = secrets.token_urlsafe(32)
        self.account = {'_id': OWNER_ID, 'kind': 'app_identity', 'auth_version': 2,
                        'username': 'daniel', 'display_name': 'Daniel', 'password_hash': 'hash',
                        'sessions': [{'id': session_hash(self.identity), 'expires_at': int(time.time()) + 100}]}
        self.db = SimpleNamespace(account=_StatefulAccountCollection(lambda: self.account))
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=self.db)))
        self.token = create_session_token(OWNER_ID, 2, self.identity)

    def tearDown(self):
        self.settings_patch.stop()

    def check(self, token):
        return require_session(self.request, 'Bearer ' + token)

    def test_signed_session_and_tampering(self):
        self.assertEqual(self.check(self.token)['session_id'], session_hash(self.identity))
        with self.assertRaises(HTTPException): self.check(self.token + 'x')

    def test_development_is_never_anonymous(self):
        with self.assertRaises(HTTPException) as failure: require_session(self.request, None)
        self.assertEqual(failure.exception.status_code, 401)

    def test_expired_boundary_is_rejected(self):
        with patch('app.auth.time.time', return_value=int(time.time()) + 43200):
            with self.assertRaises(HTTPException): self.check(self.token)

    def test_biometric_device_session_and_sliding_renewal(self):
        # Seed an already-expired stale session to verify session_response prunes it
        self.account['sessions'].append({'id': 'stale-expired', 'expires_at': int(time.time()) - 10})
        mobile_resp = session_response(self.db, self.account, 'Android')
        self.assertEqual(mobile_resp['expires_in'], BIOMETRIC_SESSION_SECONDS)
        self.assertFalse(any(s['id'] == 'stale-expired' for s in self.account['sessions']))
        self.assertEqual(len(self.account['sessions']), 2)
        web_resp = session_response(self.db, self.account, 'Navegador')
        self.assertEqual(web_resp['expires_in'], 43200)
        self.assertEqual(len(self.account['sessions']), 3)
        self.assertIsNotNone(self.check(mobile_resp['access_token']))
        self.assertIsNotNone(self.check(web_resp['access_token']))

        # Sliding renewal updates existing session in-place without duplicating
        renewed = renew_session_token(self.db, self.account, self.identity)
        self.assertIsNotNone(renewed)
        self.assertEqual(renewed['expires_in'], BIOMETRIC_SESSION_SECONDS)
        self.assertEqual(len(self.account['sessions']), 3)
        with patch('app.auth.time.time', return_value=int(time.time()) + 86400):
            self.assertEqual(self.check(renewed['access_token'])['session_id'], session_hash(self.identity))
            with self.assertRaises(HTTPException):
                self.check(web_resp['access_token'])

        # MAX_SESSIONS cap is strictly enforced across repeated logins
        for idx in range(MAX_SESSIONS + 3):
            session_response(self.db, self.account, f'Device-{idx}')
        self.assertEqual(len(self.account['sessions']), MAX_SESSIONS)

        # Stale auth_version snapshot is rejected with 401 when account version changes
        stale_snapshot = {**self.account, 'auth_version': self.account['auth_version'] - 1}
        with self.assertRaises(HTTPException) as stale_err:
            session_response(self.db, stale_snapshot, 'Android')
        self.assertEqual(stale_err.exception.status_code, 401)

    def test_revoke_and_password_version_invalidate(self):
        self.account['sessions'] = []
        with self.assertRaises(HTTPException): self.check(self.token)
        self.account['sessions'] = [{'id': session_hash(self.identity), 'expires_at': int(time.time()) + 100}]
        self.account['auth_version'] += 1
        with self.assertRaises(HTTPException): self.check(self.token)

    def test_foreign_owner_rejected(self):
        with self.assertRaises(HTTPException): self.check(create_session_token('stranger', 2, self.identity))

    def test_signed_malformed_claims_never_crash(self):
        def b64(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
        for claims in [[], None, {'sub': OWNER_ID, 'exp': True}, {'sub': OWNER_ID, 'exp': 'tomorrow'}]:
            message = b64({'alg': 'HS256', 'typ': 'JWT'}) + '.' + b64(claims)
            signature = base64.urlsafe_b64encode(hmac.new(self.settings.jwt_secret.encode(), message.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
            with self.assertRaises(HTTPException): self.check(message + '.' + signature)

    def test_old_shared_tokens_are_rejected(self):
        head = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=').decode()
        payload = base64.urlsafe_b64encode(json.dumps({'sub': 'owner', 'exp': int(time.time()) + 100}).encode()).rstrip(b'=').decode()
        message = head + '.' + payload
        sig = base64.urlsafe_b64encode(hmac.new(self.settings.jwt_secret.encode(), message.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
        with self.assertRaises(HTTPException): self.check(message + '.' + sig)

    def test_missing_signing_key_fails_closed(self):
        self.settings.jwt_secret = ''
        with self.assertRaises(HTTPException) as failure: self.check(self.token)
        self.assertEqual(failure.exception.status_code, 503)

    def test_password_hash_is_salted_and_unicode_supported(self):
        password = 'Uma senha longa áç秘密!'
        a, b = hash_password(password), hash_password(password)
        self.assertNotEqual(a, b)
        self.assertNotIn(password, a)
        self.assertTrue(verify_password(password, a))
        self.assertFalse(verify_password(password + 'x', a))
        self.assertFalse(verify_password(password, 'invalid'))

    def test_auth_validation_never_echoes_secrets(self):
        client = TestClient(app)
        for path, body in [('/v1/auth/setup', {'username': 'ab', 'display_name': 'Test', 'password': 'secret', 'setup_token': 'secret-token'}),
                           ('/v1/auth/session', {'password': '秘密' * 300})]:
            response = client.post(path, json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(body['password'], response.text)
            self.assertNotIn('input', response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_sensitive_routes_require_login(self):
        client = TestClient(app)
        for path in ['/v1/accounts', '/v1/dashboard', '/v1/reviews', '/v1/transactions', '/v1/auth/account', '/v1/auth/sessions']:
            self.assertEqual(client.get(path).status_code, 401, path)

    def test_profile_update_bumps_version_and_writes_audit(self):
        from app.account_auth import AccountInput, account_update
        stored = []
        class Collection:
            def update_one(self, query, update, pipeline=None):
                stored.append(('update', query, update))
                return SimpleNamespace(matched_count=1)
            def insert_one(self, document):
                stored.append(('audit', document))
        db = SimpleNamespace(account=Collection(), audit_log=Collection())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
        session = {'account': {'_id': OWNER_ID, 'kind': 'app_identity', 'auth_version': 2,
                               'username': 'proprietario', 'display_name': 'Antes', 'password_hash': 'hash'},
                   'session_id': 'current'}
        with patch('app.account_auth.session_response', return_value={'access_token': 'renewed', 'account': {'username': 'novo', 'display_name': 'Depois'}}) as renewed:
            result = account_update(AccountInput(username='novo', display_name='Depois'), request, session)
        self.assertEqual(result['access_token'], 'renewed')
        renewed.assert_called_once()
        self.assertEqual(renewed.call_args.args[1]['auth_version'], 3)
        self.assertEqual(stored[0][2]['$set']['auth_version'], 3)
        self.assertEqual(stored[1][1]['event'], 'account_profile_updated')
        self.assertEqual(stored[1][1]['before']['display_name'], 'Antes')
        self.assertNotIn('password', stored[1][1]['before'])
        self.assertNotIn('password', stored[1][1]['after'])

    def test_brute_force_window_and_expiry(self):
        limiter = LoginAttempts()
        for _ in range(10): limiter.check('owner', now=1)
        with self.assertRaises(HTTPException) as failure: limiter.check('owner', now=2)
        self.assertEqual(failure.exception.status_code, 429)
        limiter.check('owner', now=62)


if __name__ == '__main__': unittest.main()
