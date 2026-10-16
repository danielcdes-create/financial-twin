"""Own one HTTP worker and recover crashes or a stalled HTTP event loop."""
import ctypes
import hashlib
import json
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SupervisorOptions:
    host: str
    port: int
    cwd: Path
    command: tuple[str, ...]
    interval: float = 5
    timeout: float = 3
    failure_limit: int = 3
    # Bootstrap checks MongoDB indexes before HTTP starts listening. A cold
    # startup under an Android build can exceed a minute; do not reset its
    # progress. Once /live succeeds, the normal failure limit applies at once.
    startup_grace: float = 300
    restart_delay: float = 2


@contextmanager
def single_instance(identity: str):
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, True, 'Local\\FinancialTwinBackend-' + digest)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        owned = ctypes.get_last_error() != 183
        try:
            yield owned
        finally:
            if owned:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl
        import tempfile
        with open(Path(tempfile.gettempdir()) / ('financial-twin-' + digest + '.lock'), 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                owned = True
            except BlockingIOError:
                owned = False
            try:
                yield owned
            finally:
                if owned:
                    fcntl.flock(lock, fcntl.LOCK_UN)


def probe_liveness(host: str, port: int, timeout: float) -> bool:
    target = '127.0.0.1' if host in {'0.0.0.0', 'localhost'} else '[::1]' if host == '::' else host
    # MongoDB failure must not kill a responsive HTTP server.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{target}:{port}/live', timeout=timeout) as response:
            payload = json.load(response)
            return response.status == 200 and isinstance(payload, dict) and payload.get('status') == 'alive'
    except (OSError, ValueError):
        return False


def stop_worker(worker):
    if worker.poll() is None:
        if sys.platform == 'win32':
            # Windows venv Python can be a launcher with a real Python child.
            # Limit termination to this still-running Popen process tree.
            subprocess.run(['taskkill.exe', '/PID', str(worker.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            worker.terminate()
    worker.wait(timeout=10)


def supervise(options: SupervisorOptions, stop: threading.Event | None = None, emit=None):
    stop = stop or threading.Event()
    emit = emit or (lambda record: print(json.dumps(record), flush=True))
    worker = None
    identity = f'{options.cwd.resolve()}:{options.host}:{options.port}'
    with single_instance(identity) as owned:
        if not owned:
            emit({'event': 'supervisor_already_running', 'port': options.port})
            return
        try:
            while not stop.is_set():
                if worker is None or worker.poll() is not None:
                    if worker is not None:
                        emit({'event': 'worker_exited', 'pid': worker.pid, 'code': worker.returncode})
                        if stop.wait(options.restart_delay):
                            break
                    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    worker = subprocess.Popen(list(options.command), cwd=options.cwd, creationflags=flags,
                                              stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)
                    started = time.monotonic()
                    failures = 0
                    ready = False
                    emit({'event': 'worker_started', 'pid': worker.pid, 'port': options.port})
                if probe_liveness(options.host, options.port, options.timeout):
                    if not ready:
                        emit({'event': 'worker_ready', 'pid': worker.pid})
                    ready = True
                    failures = 0
                elif ready or time.monotonic() - started >= options.startup_grace:
                    failures += 1
                    emit({'event': 'liveness_failed', 'pid': worker.pid, 'consecutive': failures})
                    if failures >= options.failure_limit:
                        emit({'event': 'worker_restart', 'pid': worker.pid, 'reason': 'http_unresponsive'})
                        # Stop only our Popen child, never another listener.
                        stop_worker(worker)
                        continue
                if stop.wait(options.interval):
                    break
        finally:
            if worker is not None and worker.poll() is None:
                stop_worker(worker)
            emit({'event': 'supervisor_stopped', 'port': options.port})


def run_local_server():
    from app.config import get_settings
    settings = get_settings()
    stop = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: stop.set())
    project = Path(__file__).resolve().parents[2]
    supervise(SupervisorOptions(settings.api_host, settings.api_port, project,
                               (sys.executable, '-u', str(project/'backend/run_server.py'))), stop)
