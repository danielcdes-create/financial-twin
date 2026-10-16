"""Explicit test-only server, without production startup or recovery workers."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from contextlib import asynccontextmanager
from pymongo import MongoClient
from app.main import app,configured_mongodb_uri
from app.config import get_settings
import uvicorn
if __name__=='__main__':
    name=sys.argv[1]
    if not name.startswith('financial_twin_test_'):raise SystemExit('Explicit test database required')
    @asynccontextmanager
    async def lifespan(instance):
        with MongoClient(configured_mongodb_uri(get_settings()),tz_aware=True) as client:
            instance.state.db=client[name];instance.state.service=None;instance.state.mongo_client=client
            yield
    app.router.lifespan_context=lifespan
    for middleware in app.user_middleware:
        if middleware.cls.__name__=='CORSMiddleware':middleware.kwargs['allow_origins'].append('http://127.0.0.1:19007')
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]))
