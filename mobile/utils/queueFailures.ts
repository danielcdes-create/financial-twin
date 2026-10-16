import type {QueueRow} from './syncQueue';

type Db = {runAsync: (sql: string, ...params: any[]) => Promise<unknown>};
// A retry callback may have outlived the row shown to its owner. Clear only the
// exact operation it offered, preserving replacement/rerouted retry metadata.
export async function clearQueueFailure(db: Db, row: QueueRow): Promise<void> {
  await db.runAsync('DELETE FROM queue_state WHERE event_id=? AND EXISTS (SELECT 1 FROM event_queue WHERE event_id=? AND kind=? AND local_uri IS ? AND payload=? AND filename IS ? AND mime_type IS ? AND id=?)',
    row.event_id, row.event_id, row.kind, row.local_uri, row.payload, row.filename, row.mime_type, row.id);
}

// The failed transport belongs to this exact row. A concurrent discard/reroute
// must not quarantine its replacement or reintroduce a retired row's state.
export async function persistQueueFailure(db: Db, row: QueueRow, error: string,
  attempts: number, nextAttemptAt: string, quarantine: boolean): Promise<void> {
  await db.runAsync('INSERT INTO queue_state(event_id,attempt_count,next_attempt_at,last_error,quarantined) SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM event_queue WHERE event_id=? AND kind=? AND local_uri IS ? AND payload=? AND filename IS ? AND mime_type IS ? AND id=?) ON CONFLICT(event_id) DO UPDATE SET attempt_count=excluded.attempt_count,next_attempt_at=excluded.next_attempt_at,last_error=excluded.last_error,quarantined=excluded.quarantined',
    row.event_id, attempts, nextAttemptAt, error, quarantine ? 1 : 0,
    row.event_id, row.kind, row.local_uri, row.payload, row.filename, row.mime_type, row.id);
}
