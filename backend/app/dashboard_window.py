"""Fetch only windows consumed by dashboard cards, preserving global freshness."""
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
from app.financial_time import day_start, today, local_date
from app.economic_period import economic_instant

try:
    from app.telemetry import SpanTimer
except Exception:
    def SpanTimer(*_args, **_kwargs):
        return nullcontext()

PROJECTION = {
    'valor': 1,
    'fluxo': 1,
    'tipo': 1,
    'natureza': 1,
    'categoria': 1,
    'categoria_sugerida': 1,
    'subcategoria': 1,
    'descricao_original': 1,
    'estabelecimento_original': 1,
    'merchant_raw': 1,
    'data_evento': 1,
    'competencia': 1,
    'recorrencia': 1,
    'transfer': 1,
    'classification.migration_reason': 1,
    'linked_transaction_id': 1,
}


def _merge_intervals(intervals):
    """Collapse overlapping or adjacent [start, end] ranges into minimal disjoint intervals."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda pair: pair[0])
    merged = [ordered[0]]
    for current_start, current_end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if current_start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, current_end))
        else:
            merged.append((current_start, current_end))
    return merged


def snapshot_for(db, windows):
    from app.main import period_start, shift_months, normalized_datetime

    with SpanTimer("dashboard.snapshot_for", windows=len(windows)):
        latest = db.transactions.find_one(
            {'data_evento': {'$type': 'date'}},
            {'data_evento': 1},
            sort=[('data_evento', -1)],
        )
        captured = db.provisional_transactions.find_one(
            {'status': 'confirmed', 'data_evento': {'$type': 'date'}},
            {'data_evento': 1},
            sort=[('data_evento', -1)],
        )
        timestamps = [normalized_datetime(row['data_evento']) for row in (latest, captured) if row]

        # Bounded prefix range + regex enables reverse index scan on `competencia` instead of COLLSCAN.
        competence_query = {
            'competencia': {
                '$gte': '0001-01',
                '$lte': '9999-12',
                '$regex': r'^\d{4}-(0[1-9]|1[0-2])$',
            }
        }
        economic_latest = db.transactions.find_one(
            competence_query,
            {'competencia': 1},
            sort=[('competencia', -1)],
        )
        economic_capture = db.provisional_transactions.find_one(
            {'status': 'confirmed', **competence_query},
            {'competencia': 1},
            sort=[('competencia', -1)],
        )
        economic_timestamps = [
            economic_instant(row)
            for row in (economic_latest, economic_capture)
            if row and economic_instant(row)
        ]
        timestamps.extend(economic_timestamps)
        anchor = max(timestamps, default=datetime.now(timezone.utc))

        date_intervals = []
        competence_intervals = []
        for period, start, end in windows:
            beginning = period_start(anchor, period, start)
            ending = day_start(end + timedelta(days=1)) if end else anchor + timedelta(microseconds=1)
            if period != 'all':
                if start or end:
                    beginning = beginning - (ending - beginning)
                else:
                    beginning = shift_months(
                        beginning,
                        -12 if period == 'year' else -{'month': 1, '3m': 3, '6m': 6, '12m': 12}.get(period, 1),
                    )
            date_intervals.append((beginning, ending))
            comp_start = '0001-01' if beginning.year == 1 else local_date(beginning).strftime('%Y-%m')
            comp_end = local_date(ending - timedelta(microseconds=1)).strftime('%Y-%m')
            competence_intervals.append((comp_start, comp_end))

        ranges = [
            {'data_evento': {'$gte': start_dt, '$lt': end_dt}}
            for start_dt, end_dt in _merge_intervals(date_intervals)
        ] + [
            {'competencia': {'$gte': start_comp, '$lte': end_comp}}
            for start_comp, end_comp in _merge_intervals(competence_intervals)
        ]
        query = {'$or': ranges}
        txs = list(db.transactions.find(query, PROJECTION))
        captures = list(db.provisional_transactions.find({'status': 'confirmed', **query}, PROJECTION))
        return (
            txs,
            captures,
            {
                'legacy': latest.get('data_evento') if latest else None,
                'captured': captured.get('data_evento') if captured else None,
                'anchor': anchor,
                'economic_anchor': max(economic_timestamps, default=None),
            },
        )