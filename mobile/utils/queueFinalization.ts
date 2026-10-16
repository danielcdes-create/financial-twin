import type { QueueRow } from './syncQueue';

type Db = {getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>;
  runAsync: (sql: string, ...params: any[]) => Promise<unknown>};
const identity = (row: QueueRow) => JSON.stringify([row.id, row.event_id, row.kind,
  row.local_uri, row.payload, row.filename, row.mime_type]);
export async function finalizeQueueRow(db: Db, row: QueueRow, ensureSession: () => void) {
  ensureSession();
  const rows = await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id');
  ensureSession();
  const current = rows.find(item => item.event_id === row.event_id);
  if (current && identity(current) !== identity(row))
    throw new Error('O item local mudou após a confirmação. Foi preservado para conferência.');
  if (current) await db.runAsync('DELETE FROM event_queue WHERE event_id=? AND kind=? AND local_uri IS ? AND payload=? AND filename IS ? AND mime_type IS ? AND id=?',
    row.event_id, row.kind, row.local_uri, row.payload, row.filename, row.mime_type, row.id);
  ensureSession();
  const remaining = await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id');
  if (remaining.some(item => item.event_id === row.event_id))
    throw new Error('O item local mudou durante a finalização. Foi preservado.');
  // A replacement may appear after the snapshot above. Retire only orphaned
  // metadata in the same SQL statement, preserving a new row's retry state.
  await db.runAsync('DELETE FROM queue_state WHERE event_id=? AND NOT EXISTS (SELECT 1 FROM event_queue WHERE event_id=?)', row.event_id, row.event_id);
  ensureSession();
  // The durable acknowledgement retains the original private URI. Keep its
  // bytes: a database snapshot cannot exclude a new reference while an async
  // filesystem deletion is in progress. Confirmation only retires the queue.
}
