"""Isolated HTTP fixture; never imports app config, finance data, or MongoDB."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

port = int(sys.argv[1])
control = Path(sys.argv[2])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        mode = control.read_text() if control.exists() else ''
        if mode == 'exit':
            control.unlink()
            os._exit(9)
        if mode == 'hang':
            control.unlink()
            while True:
                time.sleep(10)
        if self.path == '/health':
            self.send_response(503)
            payload = {'mongodb': 'unavailable'}
        else:
            self.send_response(200)
            payload = {'status': 'alive'}
        data = json.dumps(payload).encode()
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = HTTPServer(('127.0.0.1', port), Handler)
if (control.parent/'stall-worker').exists():
    while True:
        time.sleep(10)
server.serve_forever()
