"""Fetch only windows consumed by dashboard cards, preserving global freshness."""
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
import re
from pymongo.database import Database
from app.atomic import with_batch_size
from app.financial_time import day_start, today, local_date
from app.economic_period import economic_instant
from app.reference_cache import dashboard_snapshot_cache, unwrap_database

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
    'merchant_name': 1,
    'data_evento': 1,
    'competencia': 1,
    'recorrencia': 1,
    'transfer': 1,
    'classification.migration_reason': 1,
    'linked_transaction_id': 1,
    'account_id': 1,
    'account_profile_id': 1,
    'moeda': 1,
    'currency': 1,
    'status': 1,
    'provisional_id': 1,
}

_COMPETENCE_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


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


def _row_matches_intervals(row, date_ranges, comp_ranges, normalized_datetime):
    dt = row.get('data_evento')
    if isinstance(dt, datetime):
        ndt = normalized_datetime(dt)
        if any(start_dt <= ndt < end_dt for start_dt, end_dt in date_ranges):
            return True
    comp = row.get('competencia')
    if isinstance(comp, str) and any(start_c <= comp <= end_c for start_c, end_c in comp_ranges):
        return True
    return False


def _load_raw_ledger_snapshot(db):
    all_txs = list(with_batch_size(db.transactions.find({}, PROJECTION), 2000))
    all_captures = list(with_batch_size(db.provisional_transactions.find({'status': 'confirmed'}, PROJECTION), 2000))
    return all_txs, all_captures


def snapshot_for(db, windows):
    from app.main import period_start, shift_months, normalized_datetime

    with SpanTimer("dashboard.snapshot_for", windows=len(windows)):
        # Load transactions and confirmed captures in a single batched query per collection
        # and reuse the 30s process-local snapshot cache when reading from a MongoDB Database.
        raw_db = unwrap_database(db)
        if isinstance(raw_db, Database):
            all_txs, all_captures = dashboard_snapshot_cache.get(
                raw_db,
                lambda: _load_raw_ledger_snapshot(raw_db),
            )
        else:
            all_txs, all_captures = _load_raw_ledger_snapshot(db)

        latest_dt = max(
            (row['data_evento'] for row in all_txs if isinstance(row.get('data_evento'), datetime)),
            key=normalized_datetime,
            default=None,
        )
        captured_dt = max(
            (row['data_evento'] for row in all_captures if isinstance(row.get('data_evento'), datetime)),
            key=normalized_datetime,
            default=None,
        )
        timestamps = [normalized_datetime(dt) for dt in (latest_dt, captured_dt) if dt is not None]

        economic_timestamps = []
        for rows in (all_txs, all_captures):
            best_comp_row = max(
                (
                    row for row in rows
                    if isinstance(row.get('competencia'), str)
                    and '0001-01' <= row['competencia'] <= '9999-12'
                    and _COMPETENCE_RE.match(row['competencia'])
                ),
                key=lambda r: r['competencia'],
                default=None,
            )
            if best_comp_row is not None:
                inst = economic_instant(best_comp_row)
                if inst is not None:
                    economic_timestamps.append(inst)

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

        merged_dates = _merge_intervals(date_intervals)
        merged_comps = _merge_intervals(competence_intervals)
        txs = [row for row in all_txs if _row_matches_intervals(row, merged_dates, merged_comps, normalized_datetime)]
        captures = [row for row in all_captures if _row_matches_intervals(row, merged_dates, merged_comps, normalized_datetime)]
        return (
            txs,
            captures,
            {
                'legacy': latest_dt,
                'captured': captured_dt,
                'anchor': anchor,
                'economic_anchor': max(economic_timestamps, default=None),
                'all_txs': all_txs,
                'all_captures': all_captures,
            },
        )