"""Ephemeral login preview on loopback, with synthetic credentials only."""
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).parents[1]))
import uvicorn
from pymongo import MongoClient
from app.main import app, configured_mongodb_uri
from app.config import get_settings
import app.auth as auth
import app.account_auth as account_auth

settings = get_settings()
preview = settings.model_copy(update={'jwt_secret': 'isolated-ui-signing-' * 3, 'account_setup_token': 'isolated-ui-setup-' * 3, 'app_password': ''})
auth.get_settings = lambda: preview
account_auth.get_settings = lambda: preview
name = 'financial_twin_test_account_' + uuid4().hex[:8]

@asynccontextmanager
async def isolated_lifespan(application):
    client = MongoClient(configured_mongodb_uri(settings), serverSelectionTimeoutMS=10000)
    assert name.startswith('financial_twin_test_account_')
    application.state.db = client[name]
    application.state.mongo_client = client
    # Seed via API during a separately labelled integration probe, never a real identity.
    account = {'_id': auth.OWNER_ID, 'kind': 'app_identity', 'schema_version': 1,
               'username': 'preview', 'display_name': 'Conta de teste',
               'password_hash': auth.hash_password('Synthetic-preview-2026!'), 'auth_version': 1,
               'sessions': [], 'created_at': 0}
    application.state.db.account.insert_one(account)
    try: yield
    finally:
        client.drop_database(name)
        client.close()
app.router.lifespan_context = isolated_lifespan
# Add only the synthetic local preview origin, not a production permission.
for middleware in app.user_middleware:
    if middleware.cls.__name__ == 'CORSMiddleware': middleware.kwargs['allow_origins'].append('http://127.0.0.1:19007')
uvicorn.run(app, host='127.0.0.1', port=18009, access_log=False)
