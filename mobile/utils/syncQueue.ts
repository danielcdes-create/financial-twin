export type QueueRow = { id: number; event_id: string; kind: string; payload: string; local_uri: string | null; filename: string | null; mime_type: string | null; attempt_count?: number; next_attempt_at?: string | null; last_error?: string | null };
export function sameQueueRow(current: QueueRow | undefined, expected: QueueRow): boolean {
  return !!current && current.id === expected.id && current.event_id === expected.event_id &&
    current.kind === expected.kind && current.payload === expected.payload && current.local_uri === expected.local_uri &&
    current.filename === expected.filename && current.mime_type === expected.mime_type;
}
export function requirePersistedAck(row: QueueRow, result: any) {
  if (row.kind === 'batch' && result?.draft_persisted !== true) throw new Error('O lote ainda não foi salvo para revisão. O arquivo continua na fila.');
  if (row.kind === 'document' && result?.invalid_row_numbers?.length) throw Object.assign(new Error(`Importação com ${result.invalid_row_numbers.length} linha(s) inválida(s). O arquivo foi separado para revisão.`), { status: 422 });
  if (result?.persisted !== true) throw new Error('O servidor ainda não confirmou a persistência. O item continua na fila.');
  if (result.event_id !== row.event_id) throw new Error('Confirmação de outro evento. O item continua na fila.');
  if (row.kind === 'document' && row.filename?.startsWith('voice-') && JSON.parse(row.payload).voice_command_version === 1 &&
      (result.processing?.persisted_event_id ?? result.persisted_event_id) !== row.event_id)
    throw new Error('A confirmação não corresponde à declaração de voz enviada. O item permanece preservado.');
}
export function permanentQueueError(error: unknown): boolean {
  if ((error as {retryable?: boolean})?.retryable === true) return false;
  const status = (error as { status?: number })?.status;
  return status === 400 || status === 409 || status === 410 || status === 413 || status === 415 || status === 422;
}
export function nextQueueAttempt(attempts: number, now = Date.now(), random = Math.random): string {
  const base = Math.min(300_000, 5_000 * 2 ** Math.min(attempts - 1, 6));
  return new Date(now + Math.round(base * (0.75 + random() * 0.5))).toISOString();
}
export function createQueueSync<Row extends QueueRow>(io: {
  prepare: () => Promise<boolean>;
  list: () => Promise<Row[]>;
  send: (row: Row) => Promise<any>;
  remove: (row: Row) => Promise<void>;
  getAcknowledgement?: (row: Row) => Promise<any | null>;
  acknowledge?: (row: Row, result: any) => Promise<void>;
  recordFailure: (row: Row, error: string, attempts: number, nextAttemptAt: string, quarantine: boolean) => Promise<void>;
  done: (row: Row, result: any) => void | Promise<void>;
  changed: (count: number, error: string) => void;
}) {
  let pending: Promise<void> | null = null;
  let rerun = false;
  // Keep a valid receipt even if writing it locally fails. Restart recovery uses
  // the durable receipt; if unavailable, the same event ID is sent again safely.
  const acknowledged = new Map<string, {identity: string; result: any}>();
  return function sync(): Promise<void> {
    if (pending) { rerun = true; return pending; }
    pending = (async () => {
      let failure = '';
      let suspended = false;
      try {
        do {
          rerun = false;
          if (!await io.prepare()) break;
          for (const selected of await io.list()) {
            // Another consumer can discard, quarantine or reroute a later row
            // while an earlier upload is pending. Never send that old snapshot.
            const row = (await io.list()).find(item => item.event_id === selected.event_id);
            if (!sameQueueRow(row, selected)) continue;
            const currentRow = row!;
            let confirmed = false;
            let checking = false;
            try {
              const identity = JSON.stringify([currentRow.kind, currentRow.payload, currentRow.local_uri, currentRow.filename, currentRow.mime_type]);
              const previous = acknowledged.get(currentRow.event_id);
              if (previous && previous.identity !== identity) throw Object.assign(new Error('O conteúdo da operação mudou após a confirmação. Preserve o item para revisão.'), {status: 409});
              let result = previous?.result ?? await io.getAcknowledgement?.(currentRow);
              if (!result && currentRow.next_attempt_at && currentRow.next_attempt_at > new Date().toISOString()) { failure ||= 'Aguardando a próxima tentativa da fila.'; continue; }
              checking = true;
              const beforeSend = (await io.list()).find(item => item.event_id === currentRow.event_id);
              checking = false;
              if (!sameQueueRow(beforeSend, currentRow)) continue;
              result ??= await io.send(currentRow);
              requirePersistedAck(currentRow, result);
              confirmed = true;
              acknowledged.set(currentRow.event_id, {identity, result});
              await io.acknowledge?.(currentRow, result);
              await io.done(currentRow, result);
              await io.remove(currentRow);
              acknowledged.delete(currentRow.event_id);
            } catch (error) {
              // A failed list is a queue/storage failure, not this row's
              // transport attempt. Preserve its attempts and quarantine state.
              if (checking) throw error;
              const message = error instanceof Error ? error.message : 'Falha ao sincronizar item';
              if ((error as {name?: string})?.name === 'AbortError') {
                // Suspension is not a transport failure. Keep the row/receipt,
                // stop this cycle and wait for the explicit foreground sync.
                suspended = true; failure = ''; break;
              }
              if ((error as { status?: number })?.status === 401) { failure = message; suspended = true; break; }
              if (confirmed) {
                failure = 'Operação confirmada no servidor. A finalização local será tentada novamente: ' + message;
                continue;
              }
              const attempts = (currentRow.attempt_count || 0) + 1;
              const quarantine = permanentQueueError(error);
              await io.recordFailure(currentRow, message, attempts, nextQueueAttempt(attempts), quarantine);
              failure = quarantine ? 'Há item(ns) separados para revisão na fila local.' : message;
            }
          }
        } while (rerun && !suspended);
      } catch (error) { failure = (error as {name?: string})?.name === 'AbortError' ? '' : error instanceof Error ? error.message : 'Falha ao sincronizar a fila'; }
      try { io.changed((await io.list()).length, failure); }
      catch (error) { io.changed(-1, (error as {name?: string})?.name === 'AbortError' ? '' : failure || 'Não foi possível consultar a fila local.'); }
    })().finally(() => { pending = null; });
    return pending;
  };
}
