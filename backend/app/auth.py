"""Authentication for one private ledger owner; financial accounts are unrelated."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from threading import Lock
from fastapi import Header, HTTPException, Request
from app.config import get_settings

OWNER_ID = 'financial-twin-owner'
SESSION_SECONDS = 12 * 60 * 60
BIOMETRIC_SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_SESSIONS = 10
PASSWORD_ITERATIONS = 600_000


class LoginAttempts:
    """Bounded per-process protection; never trust forwarded IP headers."""
    def __init__(self):
        self.lock = Lock()
        self.attempts = {}

    def check(self, identity, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if v and v[-1] > now - 60}
            if identity not in self.attempts and len(self.attempts) >= 1024:
                raise HTTPException(429, 'Muitas tentativas. Aguarde um minuto.')
            queue = self.attempts.setdefault(identity, deque())
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= 10:
                raise HTTPException(429, 'Muitas tentativas. Aguarde um minuto.', headers={'Retry-After': '60'})
            queue.append(now)

    def reset(self, identity):
        with self.lock:
            self.attempts.pop(identity, None)


login_attempts = LoginAttempts()


def auth_database(request: Request):
    db = getattr(request.app.state, 'db', None)
    if db is None:
        raise HTTPException(503, 'Acesso indisponível. Tente novamente quando o servidor voltar.')
    return db


def auth_settings():
    settings = get_settings()
    if len(settings.jwt_secret) < 32:
        raise HTTPException(503, 'Configure a segurança de acesso no servidor.')
    return settings


def owner(db):
    return db.account.find_one({'_id': OWNER_ID, 'kind': 'app_identity'})


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or int(iterations) != PASSWORD_ITERATIONS:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def session_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


def create_session_token(identity: str, version: int, session_id: str, now: int | None = None,
                         ttl: int = SESSION_SECONDS) -> str:
    settings = auth_settings()
    now = int(time.time()) if now is None else now
    head = _b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64(json.dumps({'sub': identity, 'iat': now, 'exp': now + ttl,
                              'ver': version, 'jti': session_id}, separators=(',', ':')).encode())
    message = f'{head}.{payload}'
    signature = _b64(hmac.new(settings.jwt_secret.encode(), message.encode(), hashlib.sha256).digest())
    return f'{message}.{signature}'


def session_response(db, account, label: str, ttl: int | None = None):
    now = int(time.time())
    duration = ttl if ttl is not None else (SESSION_SECONDS if label == 'Navegador' else BIOMETRIC_SESSION_SECONDS)
    identity = secrets.token_urlsafe(32)
    item = {'id': session_hash(identity), 'label': label, 'created_at': now, 'last_seen_at': now, 'expires_at': now + duration}
    # Filter stale sessions, append atomically, cap independently of concurrent logins.
    result = db.account.update_one({'_id': OWNER_ID, 'kind': 'app_identity', 'auth_version': account['auth_version'],
                                    'password_hash': account['password_hash']}, [
        {'$set': {'sessions': {'$slice': [{'$concatArrays': [
            {'$filter': {'input': {'$ifNull': ['$sessions', []]}, 'as': 'session',
                         'cond': {'$gt': ['$$session.expires_at', now]}}}, {'$literal': [item]}]}, -MAX_SESSIONS]}}}
    ])
    if result.matched_count != 1:
        raise HTTPException(401, 'A conta mudou. Entre novamente.')
    return {'access_token': create_session_token(OWNER_ID, account['auth_version'], identity, now, duration),
            'token_type': 'bearer', 'expires_in': duration, 'account': public_account(account)}


def renew_session_token(db, account, jti: str, ttl: int = BIOMETRIC_SESSION_SECONDS):
    if not jti or len(jti) != 43:
        return None
    now = int(time.time())
    identity = session_hash(jti)
    updated = False
    for s_item in account.get('sessions', []):
        if s_item.get('id') == identity and int(s_item.get('expires_at') or 0) > now:
            s_item['last_seen_at'] = now
            s_item['expires_at'] = max(int(s_item.get('expires_at') or 0), now + ttl)
            updated = True
            break
    if not updated:
        return None
    try:
        db.account.update_one({'_id': OWNER_ID, 'auth_version': account['auth_version']},
                              {'$set': {'sessions': account['sessions']}})
    except Exception:
        pass
    return {'access_token': create_session_token(OWNER_ID, account['auth_version'], jti, now, ttl),
            'token_type': 'bearer', 'expires_in': ttl, 'account': public_account(account)}


def public_account(account):
    return {'username': account['username'], 'display_name': account['display_name']}


def require_session(request: Request, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer ') or len(authorization) > 2048:
        raise HTTPException(401, 'Entre na sua conta para continuar.')
    settings = auth_settings()
    try:
        head, payload, signature = authorization[7:].split('.')
        expected = _b64(hmac.new(settings.jwt_secret.encode(), f'{head}.{payload}'.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        header = json.loads(base64.urlsafe_b64decode(head + '=' * (-len(head) % 4)))
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        now = int(time.time())
        if (header != {'alg': 'HS256', 'typ': 'JWT'} or not isinstance(claims, dict) or claims.get('sub') != OWNER_ID
                or type(claims.get('exp')) is not int or claims['exp'] <= now
                or type(claims.get('iat')) is not int or claims['iat'] > now + 30
                or type(claims.get('ver')) is not int or not isinstance(claims.get('jti'), str)
                or len(claims['jti']) != 43):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise HTTPException(401, 'Sessão inválida ou expirada. Entre novamente.') from None
    account = owner(auth_database(request))
    identity = session_hash(claims['jti'])
    if (not account or account.get('auth_version') != claims['ver']
            or not any(item['id'] == identity and item['expires_at'] > now for item in account.get('sessions', []))):
        raise HTTPException(401, 'Sessão encerrada. Entre novamente.')
    for s_item in account.get('sessions', []):
        if s_item.get('id') == identity:
            if now - int(s_item.get('last_seen_at') or 0) >= 60:
                s_item['last_seen_at'] = now
                try:
                    auth_database(request).account.update_one({'_id': OWNER_ID}, {'$set': {'sessions': account['sessions']}})
                except Exception:
                    pass
            break
    return {'account': account, 'session_id': identity, 'jti': claims['jti']}
