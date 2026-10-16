import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.supervision import SupervisorOptions, probe_liveness, single_instance, supervise


class SupervisorTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.05)
        self.fail('Supervisor did not reach the expected state.')

    def test_singleton_rejects_duplicate_and_releases_after_exit(self):
        with single_instance('isolated-supervisor-test') as first:
            self.assertTrue(first)
            with single_instance('isolated-supervisor-test') as second:
                self.assertFalse(second)
        with single_instance('isolated-supervisor-test') as third:
            self.assertTrue(third)

    def test_liveness_does_not_require_a_database(self):
        client = TestClient(app)
        self.assertEqual(client.get('/live').json(), {'status': 'alive'})

    def test_cold_start_grace_does_not_delay_recovery_after_ready(self):
        options = SupervisorOptions('127.0.0.1', 8000, Path('.'), ('unused-worker',))
        self.assertEqual(options.startup_grace, 300)
        self.assertEqual((options.interval, options.timeout, options.failure_limit), (5, 3, 3))
        worker = Mock(pid=1234)
        worker.poll.return_value = None
        stop = Mock()
        stopped = False
        stop.is_set.side_effect = lambda: stopped
        stop.wait.return_value = False
        records = []

        def record(value):
            nonlocal stopped
            records.append(value)
            if value['event'] == 'worker_restart':
                stopped = True

        def finish_worker(_):
            worker.poll.return_value = 0

        with patch('app.supervision.single_instance', return_value=nullcontext(True)), \
             patch('app.supervision.subprocess.Popen', return_value=worker), \
             patch('app.supervision.probe_liveness', side_effect=[False] * 6 + [True] + [False] * 3), \
             patch('app.supervision.time.monotonic', side_effect=[0, 0, 61, 125, 180, 240, 290]), \
             patch('app.supervision.stop_worker', side_effect=finish_worker) as finish:
            supervise(options, stop, record)
        ready_at = next(index for index, value in enumerate(records) if value['event'] == 'worker_ready')
        self.assertFalse(any(value['event'] == 'liveness_failed' for value in records[:ready_at]))
        self.assertEqual([value['consecutive'] for value in records if value['event'] == 'liveness_failed'], [1, 2, 3])
        finish.assert_called_once_with(worker)

    def test_recovers_exited_and_unresponsive_owned_worker_but_not_mongo_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            control = folder/'control'
            options = SupervisorOptions('127.0.0.1', port, folder,
                        (sys.executable, str(Path(__file__).with_name('supervisor_worker_fixture.py')), str(port), str(control)),
                        interval=.1, timeout=.2, failure_limit=3, startup_grace=3, restart_delay=.1)
            records = []
            stop = threading.Event()
            watcher = threading.Thread(target=supervise, args=(options, stop, records.append))
            watcher.start()
            try:
                self.wait_for(lambda: any(r['event'] == 'worker_ready' for r in records))
                first = next(r['pid'] for r in records if r['event'] == 'worker_started')
                # /health returns 503 throughout, while /live stays responsive.
                time.sleep(.5)
                self.assertEqual(sum(r['event'] == 'worker_started' for r in records), 1)
                control.write_text('exit')
                self.wait_for(lambda: sum(r['event'] == 'worker_ready' for r in records) >= 2)
                second = [r['pid'] for r in records if r['event'] == 'worker_started'][-1]
                self.assertNotEqual(first, second)
                # Stall the actual HTTP worker while its process stays alive.
                control.write_text('hang')
                self.wait_for(lambda: sum(r['event'] == 'worker_started' for r in records) >= 3)
                self.wait_for(lambda: sum(r['event'] == 'worker_ready' for r in records) >= 3)
                self.assertTrue(any(r['event'] == 'worker_restart' for r in records))
                self.assertTrue(probe_liveness('127.0.0.1', port, 1))
            finally:
                stop.set()
                watcher.join(timeout=12)
                self.assertFalse(watcher.is_alive())


if __name__ == '__main__':
    unittest.main()
