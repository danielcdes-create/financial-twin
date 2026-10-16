import {requirePersistedAck, type QueueRow} from './syncQueue';

type Db = {
  getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>;
  runAsync: (sql: string, ...params: any[]) => Promise<unknown>;
};
const identity = (row: QueueRow) => JSON.stringify([row.kind, row.payload, row.local_uri, row.filename, row.mime_type]);

export async function readQueueAcknowledgement(db: Db, row: QueueRow): Promise<any | null> {
  const receipts = await db.getAllAsync<{identity: string; result: string}>('SELECT * FROM queue_acknowledgements WHERE event_id=?', row.event_id);
  if (!receipts[0]) return null;
  if (receipts[0].identity !== identity(row)) throw Object.assign(new Error('A operação já foi confirmada com outro conteúdo. Preserve o item para revisão.'), {status: 409});
  const result = JSON.parse(receipts[0].result);
  requirePersistedAck(row, result);
  return result;
}

export async function persistQueueAcknowledgement(db: Db, row: QueueRow, result: any): Promise<void> {
  requirePersistedAck(row, result);
  const previous = await readQueueAcknowledgement(db, row);
  if (previous) return;
  await db.runAsync('INSERT OR IGNORE INTO queue_acknowledgements(event_id,identity,result,confirmed_at) VALUES(?,?,?,?)',
    row.event_id, identity(row), JSON.stringify(result), new Date().toISOString());
  await readQueueAcknowledgement(db, row);
}
