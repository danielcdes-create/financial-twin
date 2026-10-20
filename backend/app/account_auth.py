"""Private account lifecycle. No public signup into the owner's financial history."""
import hmac
import re
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError
from app.auth import (BIOMETRIC_SESSION_SECONDS, OWNER_ID, auth_database, auth_settings, get_settings, hash_password,
                      login_attempts, owner, public_account, renew_session_token, require_session, session_response,
                      verify_password)

router = APIRouter(prefix='/v1/auth', tags=['account'])


class AccountInput(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator('username')
    @classmethod
    def username_valid(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,63}', value):
            raise ValueError('Use 3 a 64 letras sem acento, números, ponto, hífen ou sublinhado.')
        return value

    @field_validator('display_name')
    @classmethod
    def name_valid(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Informe um nome válido.')
        return value


class SetupInput(AccountInput):
    password: str = Field(min_length=12, max_length=256)
    setup_token: str = Field(min_length=16, max_length=256)
    device_label: str = Field(default='Meu aparelho', min_length=1, max_length=60)


class LoginInput(BaseModel):
    username: str = Field(default='', max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_label: str = Field(default='Meu aparelho', min_length=1, max_length=60)


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
    device_label: str = Field(default='Meu aparelho', min_length=1, max_length=60)


def throttle(request):
    login_attempts.check(request.client.host if request.client else 'unknown')
    login_attempts.check('private-owner')


@router.get('/status')
def status(request: Request):
    settings = get_settings()
    configured = len(settings.jwt_secret) >= 32
    configured_owner = owner(auth_database(request)) is not None
    return {'auth_required': True, 'configured': configured, 'setup_required': not configured_owner,
            'setup_available': configured and len(settings.account_setup_token) >= 32,
            'legacy_available': configured and bool(settings.app_password) and not configured_owner}


@router.post('/setup', status_code=201)
def setup(body: SetupInput, request: Request):
    throttle(request)
    settings = auth_settings()
    if (len(settings.account_setup_token) < 32 or
            not hmac.compare_digest(body.setup_token.encode(), settings.account_setup_token.encode())):
        raise HTTPException(401, 'Código de configuração inválido.')
    account = {'_id': OWNER_ID, 'kind': 'app_identity', 'schema_version': 1, 'username': body.username,
               'display_name': body.display_name, 'password_hash': hash_password(body.password),
               'auth_version': 1, 'sessions': [], 'created_at': int(time.time())}
    db = auth_database(request)
    try:
        db.account.insert_one(account)
    except DuplicateKeyError:
        raise HTTPException(409, 'A conta já foi configurada. Use Entrar.') from None
    login_attempts.reset('private-owner')
    return session_response(db, account, body.device_label)


@router.post('/session')
def login(body: LoginInput, request: Request):
    throttle(request)
    settings = auth_settings()
    db = auth_database(request)
    account = owner(db)
    if not account and settings.app_password:
        # Existing configured server password proves ownership. Migrate once, then ignore it.
        if not hmac.compare_digest(body.password.encode(), settings.app_password.encode()):
            hash_password(body.password)
            raise HTTPException(401, 'Usuário ou senha incorretos.')
        account = {'_id': OWNER_ID, 'kind': 'app_identity', 'schema_version': 1,
                   'username': 'proprietario', 'display_name': 'Minha conta',
                   'password_hash': hash_password(body.password), 'auth_version': 1,
                   'sessions': [], 'created_at': int(time.time())}
        try:
            db.account.insert_one(account)
        except DuplicateKeyError:
            raise HTTPException(409, 'A conta foi configurada. Entre com as novas credenciais.') from None
    else:
        password_ok = verify_password(body.password, account['password_hash']) if account else False
        if not account:
            hash_password(body.password)
        if not account or body.username.strip().lower() != account['username'] or not password_ok:
            raise HTTPException(401, 'Usuário ou senha incorretos.')
    login_attempts.reset('private-owner')
    return session_response(db, account, body.device_label)


@router.get('/account')
def account_info(request: Request, response: Response, session=Depends(require_session)):
    jti = session.get('jti', '')
    if jti:
        try:
            renewed = renew_session_token(
                auth_database(request),
                session['account'],
                jti,
                BIOMETRIC_SESSION_SECONDS,
                persist_async=True,
            )
            if renewed:
                response.headers['X-Session-Token'] = renewed['access_token']
                response.headers['X-Session-Expires-In'] = str(renewed['expires_in'])
        except Exception:
            pass
    return public_account(session['account'])


@router.post('/biometric')
def biometric_refresh(request: Request, session=Depends(require_session)):
    jti = session.get('jti', '')
    renewed = renew_session_token(auth_database(request), session['account'], jti, BIOMETRIC_SESSION_SECONDS)
    if renewed:
        return renewed
    return session_response(auth_database(request), session['account'], 'Este aparelho', BIOMETRIC_SESSION_SECONDS)


@router.patch('/account')
def account_update(body: AccountInput, request: Request, session=Depends(require_session)):
    account = session['account']
    version = account['auth_version'] + 1
    before = public_account(account)
    after = {'username': body.username, 'display_name': body.display_name}
    db = auth_database(request)
    result = db.account.update_one({'_id': OWNER_ID, 'auth_version': account['auth_version']},
                                  {'$set': {'username': body.username, 'display_name': body.display_name, 'auth_version': version}})
    if result.matched_count != 1:
        raise HTTPException(409, 'A conta mudou. Entre novamente.')
    db.audit_log.insert_one({'event': 'account_profile_updated', 'actor': 'owner', 'before': before, 'after': after,
                             'created_at': datetime.now(timezone.utc)})
    refreshed = {**account, 'auth_version': version, 'username': body.username, 'display_name': body.display_name}
    return session_response(db, refreshed, 'Este aparelho')


@router.post('/password')
def password_change(body: PasswordInput, request: Request, session=Depends(require_session)):
    throttle(request)
    account = session['account']
    if not verify_password(body.current_password, account['password_hash']):
        raise HTTPException(403, 'Senha atual incorreta.')
    if body.current_password == body.new_password:
        raise HTTPException(422, 'Escolha uma senha diferente da atual.')
    version = account['auth_version'] + 1
    encoded = hash_password(body.new_password)
    db = auth_database(request)
    result = db.account.update_one({'_id': OWNER_ID, 'auth_version': account['auth_version']},
                                  {'$set': {'password_hash': encoded, 'auth_version': version, 'sessions': []}})
    if result.matched_count != 1:
        raise HTTPException(409, 'A senha já foi alterada. Entre novamente.')
    login_attempts.reset('private-owner')
    return session_response(db, {**account, 'auth_version': version, 'password_hash': encoded}, body.device_label)


@router.get('/sessions')
def sessions(session=Depends(require_session)):
    now = int(time.time())
    return {'items': [{**item, 'current': item['id'] == session['session_id']}
                      for item in session['account'].get('sessions', []) if item['expires_at'] > now]}


@router.delete('/sessions/{session_id}')
def revoke(session_id: str, request: Request, session=Depends(require_session)):
    if not re.fullmatch('[a-f0-9]{64}', session_id):
        raise HTTPException(422, 'Sessão inválida.')
    auth_database(request).account.update_one({'_id': OWNER_ID}, {'$pull': {'sessions': {'id': session_id}}})
    return {'revoked': True}


@router.post('/logout')
def logout(request: Request, session=Depends(require_session)):
    return revoke(session['session_id'], request, session)


@router.post('/logout-all')
def logout_all(request: Request, session=Depends(require_session)):
    auth_database(request).account.update_one({'_id': OWNER_ID}, {'$inc': {'auth_version': 1}, '$set': {'sessions': []}})
    return {'revoked': True}
