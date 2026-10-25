"""Telemetry keeps timings and drops search text, ids and amounts."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.dashboard_window import _merge_intervals
from app.telemetry import MongoPerformanceListener, RequestTrace, SpanTimer, TelemetryAggregator, _current_trace, public_route, safe_client_metadata


class TelemetryPrivacyTests(unittest.TestCase):
    def test_route_label_drops_query_and_ids(self):
        self.assertEqual(
            public_route('/v1/transactions/507f1f77bcf86cd799439011?q=mercado&valor=10'),
            '/v1/transactions/{id}',
        )

    def test_client_metadata_keeps_timings_only(self):
        meta = safe_client_metadata({
            'query': 'mercado 10,00',
            'commitment_id': '507f1f77bcf86cd799439011',
            'error': 'saldo divergente',
            'payload_bytes': 12,
            'kind': 'assinatura',
            'cached': True,
        })
        self.assertEqual(meta, {'payload_bytes': 12, 'kind': 'assinatura', 'cached': True})

    def test_span_drops_financial_labels(self):
        trace = RequestTrace(trace_id='t', method='GET', path='/v1/dashboard/overview')
        token = _current_trace.set(trace)
        try:
            with SpanTimer('dashboard.compute_period', period='month', query='pix', valor='10.00'):
                pass
        finally:
            _current_trace.reset(token)
        span = trace.custom_spans[0]
        self.assertEqual(span['period'], 'month')
        self.assertNotIn('query', span)
        self.assertNotIn('valor', span)

    def test_competence_intervals_merge_without_changing_coverage(self):
        self.assertEqual(
            _merge_intervals([('2026-01', '2026-03'), ('2026-03', '2026-06')]),
            [('2026-01', '2026-06')],
        )
        self.assertEqual(
            _merge_intervals([('2026-01', '2026-02'), ('2026-04', '2026-05')]),
            [('2026-01', '2026-02'), ('2026-04', '2026-05')],
        )

    def test_mongo_listener_tracks_getmore_collection_and_connection_id(self):
        listener = MongoPerformanceListener()
        trace = RequestTrace(trace_id='t2', method='GET', path='/v1/commitment-projection')
        token = _current_trace.set(trace)
        try:
            listener.started(SimpleNamespace(
                command_name='getMore',
                command={'getMore': 160975888853370636, 'collection': 'obligations'},
                request_id=42,
                connection_id=('atlas-shard-0', 27017),
            ))
            listener.succeeded(SimpleNamespace(
                command_name='getMore',
                request_id=42,
                connection_id=('atlas-shard-0', 27017),
                duration_micros=531000,
                reply={'cursor': {'nextBatch': [{}, {}]}},
            ))
        finally:
            _current_trace.reset(token)
        self.assertEqual(len(trace.db_commands), 1)
        self.assertEqual(trace.db_commands[0].collection, 'obligations')
        self.assertEqual(trace.db_commands[0].docs_returned, 2)



    def test_mongo_listener_extracts_cursor_ns_and_does_not_eager_pop(self):
        listener = MongoPerformanceListener()
        listener.started(SimpleNamespace(
            command_name='find',
            command={'find': 'accounts'},
            connection_id=None,
            request_id=99,
        ))
        listener.started(SimpleNamespace(
            command_name='find',
            command={'find': 'wrong_collection'},
            connection_id=('127.0.0.1', 27017),
            request_id=99,
        ))
        trace = RequestTrace(trace_id='t2', method='GET', path='/v1/dashboard/overview')
        token = _current_trace.set(trace)
        try:
            listener.succeeded(SimpleNamespace(
                command_name='find',
                connection_id=('127.0.0.1', 27017),
                request_id=99,
                duration_micros=156000,
                reply={'cursor': {'firstBatch': [{'_id': 1}], 'ns': 'financial_twin.transactions'}},
            ))
        finally:
            _current_trace.reset(token)
        self.assertEqual(len(trace.db_commands), 1)
        self.assertEqual(trace.db_commands[0].collection, 'transactions')
        self.assertEqual(listener._collections_by_req_id.get((None, 99)), 'accounts')

    def test_mongo_listener_tracks_ping_and_aggregator_rotates_and_hydrates_spans(self):
        import tempfile
        listener = MongoPerformanceListener()
        trace = RequestTrace(trace_id='t-ping', method='GET', path='/health')
        token = _current_trace.set(trace)
        try:
            listener.started(SimpleNamespace(
                command_name='ping',
                command={'ping': 1},
                connection_id=('127.0.0.1', 27017),
                request_id=77,
            ))
            listener.succeeded(SimpleNamespace(
                command_name='ping',
                connection_id=('127.0.0.1', 27017),
                request_id=77,
                duration_micros=120000,
                reply={'ok': 1.0},
            ))
            with SpanTimer('month_close.financial_context', period='2026-10', cached=True, months=12, purchase=False):
                pass
        finally:
            _current_trace.reset(token)
        self.assertEqual(len(trace.db_commands), 1)
        self.assertEqual(trace.db_commands[0].collection, 'admin')
        self.assertEqual(trace.db_commands[0].command_name, 'ping')
        self.assertEqual(trace.n_plus_one_alerts(), [])
        self.assertEqual(trace.custom_spans[0]['period'], '2026-10')
        self.assertEqual(trace.custom_spans[0]['cached'], True)
        self.assertEqual(trace.custom_spans[0]['months'], 12)
        self.assertEqual(trace.custom_spans[0]['purchase'], False)

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / 'perf_telemetry.jsonl'
            agg = TelemetryAggregator(max_samples_per_key=10, log_path=log_file, max_log_bytes=450, backup_count=2)
            for idx in range(6):
                agg.record_backend({
                    'timestamp': '2026-10-25T00:00:00Z',
                    'trace_id': f'tr-{idx}',
                    'method': 'GET',
                    'route': '/v1/dashboard/overview',
                    'status_code': 200,
                    'total_ms': 100.0 + idx * 10,
                    'db_ms': 40.0,
                    'app_ms': 60.0 + idx * 10,
                    'db_calls': 2,
                    'n_plus_one': [],
                    'spans': [{'span': 'dashboard.snapshot_for', 'duration_ms': 15.0 + idx, 'status': 'ok'}],
                })
            agg.record_client_batch([
                {'category': 'data_fetch', 'name': 'GET /v1/reviews', 'duration_ms': 0.0, 'status': 'cache_hit'},
                {'category': 'data_fetch', 'name': 'GET /v1/reviews', 'duration_ms': 210.0, 'status': 'ok'},
                {'category': 'data_fetch', 'name': 'GET /v1/reviews', 'duration_ms': 50.0, 'status': 'aborted'},
            ])
            report = agg.build_report()
            self.assertIn('dashboard.snapshot_for', report['backend_spans_by_p95'])
            client_rev = report['mobile_events_by_p95']['data_fetch:GET /v1/reviews']
            self.assertEqual(client_rev['error_or_abort_count'], 1)
            self.assertEqual(client_rev['status_counts'], {'cache_hit': 1, 'ok': 1, 'aborted': 1})
            self.assertTrue(log_file.with_name('perf_telemetry.jsonl.1').exists())

            # Verify boot hydration from existing log file
            reloaded = TelemetryAggregator(max_samples_per_key=10, log_path=log_file, max_log_bytes=10_000)
            reloaded_report = reloaded.build_report()
            self.assertIn('data_fetch:GET /v1/reviews', reloaded_report['mobile_events_by_p95'])


if __name__ == '__main__':
    unittest.main()
