"""Telemetry keeps timings and drops search text, ids and amounts."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.dashboard_window import _merge_intervals
from app.telemetry import MongoPerformanceListener, RequestTrace, SpanTimer, _current_trace, public_route, safe_client_metadata


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


if __name__ == '__main__':
    unittest.main()
