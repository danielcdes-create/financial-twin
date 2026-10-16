import {isPdfCapture} from './pdfCpf';
import type {QueueRow} from './syncQueue';
import {appendQueuedFile} from './uploadFile';
import {persistQueueAcknowledgement} from './queueAcknowledgements';

function isStatementFile(row: QueueRow): boolean {
  const tabular=/\.(csv|xlsx)$/i.test(row.filename||'');
  const image=['image/png','image/jpeg'].includes(row.mime_type||'')||/\.(png|jpe?g)$/i.test(row.filename||'');
  return (row.kind === 'document' || row.kind === 'batch') && (isPdfCapture(row)||image||tabular);
}

export function canReviewAsStatement(row: QueueRow): boolean {
  return row.kind === 'document' && isStatementFile(row) &&
    /only BRL is supported|moeda|fatura|extrato|linha\(s\) inválida/i.test(row.last_error || '');
}

export function queueFailureLabel(row: QueueRow): string {
  return row.last_error === 'only BRL is supported'
    ? 'Não foi possível reconhecer a moeda deste arquivo. Se for fatura ou extrato, confira as linhas pelo fluxo abaixo.'
    : row.last_error || 'Não foi possível processar este item.';
}

type QueueDb = {
  getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>;
  runAsync: (sql: string, ...params: any[]) => Promise<unknown>;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sameQueuedFile = (current: QueueRow | undefined, expected: QueueRow) =>
  current?.id === expected.id && current.event_id === expected.event_id &&
  current.kind === expected.kind && current.local_uri === expected.local_uri &&
  current.payload === expected.payload && current.filename === expected.filename &&
  current.mime_type === expected.mime_type;

// A previous receipt attempt may remain after the same bytes were imported through
// a different statement batch. Only the server's persisted hash match can clear it.
// This request never extracts a document, imports lines or sends the stored CPF.
export async function reconcileImportedFiles(
  db: QueueDb,
  rows: QueueRow[],
  upload: (path: string, data: FormData) => Promise<any>,
  assertSession: () => void,
): Promise<string[]> {
  const removed: string[] = [];
  let sessionChanged = false;
  const checkSession = () => {
    try { assertSession(); }
    catch (error) { sessionChanged = true; throw error; }
  };
  const checked = async <T,>(work: () => Promise<T>): Promise<T> => {
    checkSession();
    let result: T;
    try { result = await work(); }
    catch (error) { checkSession(); throw error; }
    checkSession();
    return result;
  };
  const list = () => db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id');
  for (const row of rows) {
    // The reviewed batch route accepts PDF, images and CSV/XLSX. The same exact
    // committed-byte proof must also retire their previous receipt attempts.
    if (!row.local_uri || !isStatementFile(row)) continue;
    try {
      if (!sameQueuedFile((await checked(list)).find(item => item.event_id === row.event_id), row)) continue;
      const data = new FormData();
      data.append('event_id', row.event_id);
      await checked(() => appendQueuedFile(data, row));
      const ack = await checked(() => upload('/v1/capture-batches/file-status', data));
      if (ack?.imported !== true || ack.event_id !== row.event_id ||
          typeof ack.sha256 !== 'string' || !/^[0-9a-f]{64}$/i.test(ack.sha256) ||
          typeof ack.batch_id !== 'string' || !UUID.test(ack.batch_id)) continue;
      if (!sameQueuedFile((await checked(list)).find(item => item.event_id === row.event_id), row)) continue;
      // imported=true means the server verified these exact bytes against a
      // committed batch with a persisted result. Retain that proof and the URI
      // before retiring the local attempt; never remove the original bytes.
      await checked(() => persistQueueAcknowledgement(db, row, {
        persisted: true, event_id: row.event_id, imported: true,
        sha256: ack.sha256, batch_id: ack.batch_id,
        ...(row.kind === 'batch' ? {draft_persisted: true} : {}),
      }));
      await checked(() => db.runAsync(
        'DELETE FROM event_queue WHERE event_id=? AND kind=? AND local_uri=? AND payload=? AND filename IS ? AND mime_type IS ? AND id=?',
        row.event_id, row.kind, row.local_uri, row.payload, row.filename, row.mime_type, row.id,
      ));
      if ((await checked(list)).some(item => item.event_id === row.event_id)) continue;
      removed.push(row.event_id);
      await checked(() => db.runAsync('DELETE FROM queue_state WHERE event_id=? AND NOT EXISTS (SELECT 1 FROM event_queue WHERE event_id=?)', row.event_id, row.event_id));
    } catch (error) {
      if (sessionChanged || (error as {name?: string})?.name === 'AbortError' || (error as {status?: number})?.status === 401) throw error;
      // Offline, missing bytes, an old server or local storage failure must leave
      // ordinary queue processing available and allow another check later.
    }
  }
  return removed;
}

// Only an explicit user action changes the route. Keep the same bytes and identity;
// the statement route prepares a draft, never posts a receipt total.
export async function requeueAsStatement(db: QueueDb, row: QueueRow): Promise<void> {
  if (!canReviewAsStatement(row) || !row.local_uri) throw new Error('Este arquivo não está disponível para revisão como extrato.');
  const previousState = (await db.getAllAsync<{event_id: string; attempt_count: number; next_attempt_at: string | null; last_error: string; quarantined: number}>(
    'SELECT * FROM queue_state WHERE event_id=?', row.event_id))[0];
  const original=(await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id')).find(item=>item.event_id===row.event_id);
  if(!sameQueuedFile(original,row))throw new Error('A fila mudou. O arquivo foi preservado; atualize a tela antes de tentar novamente.');
  await db.runAsync("UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document' AND local_uri IS ? AND payload=? AND filename IS ? AND mime_type IS ? AND id=?",
    row.event_id,row.local_uri,row.payload,row.filename,row.mime_type,row.id);
  const persisted = (await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id')).find(item => item.event_id === row.event_id);
  if (!sameQueuedFile(persisted,{...row,kind:'batch'}))
    throw new Error('A fila mudou. Atualize a tela antes de tentar novamente.');
  // Reset only the failure attached to the previous route. A new batch failure
  // written by another consumer after the route update must remain visible.
  if (previousState) await db.runAsync('DELETE FROM queue_state WHERE event_id=? AND attempt_count=? AND next_attempt_at IS ? AND last_error=? AND quarantined=?',
    row.event_id, previousState.attempt_count, previousState.next_attempt_at, previousState.last_error, previousState.quarantined);
}
