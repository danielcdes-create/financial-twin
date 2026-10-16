"""Single HTTP worker; start run_dev.py for supervised operation."""
import sys
import json
import pymongo
import uvicorn
from app.config import get_settings

if __name__ == '__main__':
    settings = get_settings()
    print(json.dumps({'runtime':'financial-twin-backend','pymongo_version':pymongo.version,'pymongo_module':pymongo.__file__}),flush=True)
    uvicorn.run('app.main:app', host=settings.api_host, port=settings.api_port,
                reload=False, loop='app.runtime:windows_server_loop' if sys.platform == 'win32' else 'auto')
