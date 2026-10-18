import {deleteVoiceDraft, flushVoiceDraft} from './voiceDraft';
import {deleteManualDraft, flushManualDraft, MANUAL_DRAFT_KEY} from './manualDraft';

type Db = {getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>; runAsync: (sql: string, ...params: any[]) => Promise<unknown>};
type Kind = 'review' | 'batch' | 'preserved' | 'voice';
export type LocalDraft = {kind: Kind; id: string; title: string; description: string; updatedAt: string; row: Record<string, any>; pendingCreation: boolean};
const writes = new WeakMap<object, Map<string, Set<Promise<unknown>>>>();
const writerKey = (kind: Kind, id: string) => `${kind}:${id}`;

// Keep a handle after an editor unmounts so deletion waits for its last autosave.
export function trackDraftWrite(db: object, kind: Kind, id: string, task: Promise<unknown>): void {
  let pending = writes.get(db);
  if (!pending) {pending = new Map(); writes.set(db, pending);}
  const key = writerKey(kind, id);
  let tasks = pending.get(key);
  if (!tasks) {tasks = new Set(); pending.set(key, tasks);}
  tasks.add(task);
  const finished = () => {tasks.delete(task); if (!tasks.size && pending.get(key) === tasks) pending.delete(key);};
  void task.then(finished, finished);
}
export async function flushDraftWrites(db: object, kind: Kind, id: string): Promise<void> {
  const pending = writes.get(db), key = writerKey(kind, id);
  while (pending?.get(key)?.size) await Promise.allSettled([...pending.get(key)!]);
}
const parsed = (value: unknown): any => {try {return JSON.parse(String(value));} catch {return null;}};
const textValue = (...values: unknown[]): string => values.find(value => typeof value === 'string' && value.trim()) as string || '';
const sameRow = (left: Record<string, any>, right: Record<string, any>, fields: string[]) => fields.every(field => left[field] === right[field]);
const tables: Record<Kind, {table: string; id: string; fields: string[]}> = {
  review: {table: 'review_drafts', id: 'provisional_id', fields: ['item', 'draft', 'updated_at']},
  batch: {table: 'batch_drafts', id: 'batch_id', fields: ['state', 'updated_at']},
  preserved: {table: 'preserved_drafts', id: 'draft_id', fields: ['state', 'updated_at']},
  voice: {table: 'voice_drafts', id: 'draft_key', fields: ['state', 'updated_at']},
};

export async function listLocalDrafts(db: Db): Promise<LocalDraft[]> {
  const groups = await Promise.all((Object.keys(tables) as Kind[]).map(async kind => {
    const rows = await db.getAllAsync<Record<string, any>>(`SELECT * FROM ${tables[kind].table}`);
    return rows.map(row => {
      const state = parsed(kind === 'review' ? row.draft : row.state), item = (kind === 'review' ? parsed(row.item) : null) || state?.item || {};
      const name = textValue(state?.draft?.merchant, state?.draft?.vendedor, state?.vendedor, state?.name, state?.filename, item.vendedor, item.merchant_name, item.merchant_raw, item.descricao_original, item.texto_original, item.raw_text, state?.text);
      const pendingCreation = kind === 'batch' && row.batch_id === 'commitment-create' && !state?.creation_receipt;
      let title = kind === 'review' ? 'Revisão de captura' : kind === 'batch' ? 'Revisão de lote' : kind === 'voice' ? 'Captura por voz' : 'Cópia preservada';
      if (kind === 'preserved') title = row.scope === 'voice' ? 'Voz preservada' : row.scope === 'batch' ? 'Lote preservado' : 'Revisão preservada';
      if (kind === 'batch' && row.batch_id === MANUAL_DRAFT_KEY) title = 'Captura por digitação';
      if (kind === 'batch' && row.batch_id === 'commitment-create') title = pendingCreation ? 'Cadastro aguardando confirmação' : 'Cadastro confirmado';
      return {kind, id: String(row[tables[kind].id]), title, description: name.slice(0, 160) || (Array.isArray(state?.rows) ? `${state.rows.length} linha(s) para revisar` : 'Edições salvas neste aparelho'), updatedAt: String(row.updated_at), row, pendingCreation};
    });
  }));
  return groups.flat().sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

export async function deleteLocalDraft(db: Db, draft: LocalDraft): Promise<void> {
  if (draft.pendingCreation) throw new Error('Este cadastro já foi enviado. Confira a tentativa pendente em Compromissos antes de iniciar outro cadastro.');
  await flushDraftWrites(db, draft.kind, draft.id);
  if (draft.kind === 'batch' && draft.id === MANUAL_DRAFT_KEY) await flushManualDraft(db).catch(() => undefined);
  if (draft.kind === 'voice') await flushVoiceDraft(db, draft.id).catch(() => undefined);
  const {table, id, fields} = tables[draft.kind];
  const rows = await db.getAllAsync<Record<string, any>>(`SELECT * FROM ${table} WHERE ${id}=?`, draft.id);
  if (!rows.length) return;
  if (!sameRow(rows[0], draft.row, fields)) throw new Error('O rascunho mudou. Atualize a lista e confira antes de excluir.');
  if (draft.kind === 'batch' && draft.id === MANUAL_DRAFT_KEY) {await deleteManualDraft(db, draft.row.state); return;}
  if (draft.kind === 'voice') {await deleteVoiceDraft(db, draft.id, draft.row.state); return;}
  await db.runAsync(`DELETE FROM ${table} WHERE ${id}=?${fields.map(field => ` AND ${field}=?`).join('')}`, draft.id, ...fields.map(field => draft.row[field]));
  const remaining = await db.getAllAsync<Record<string, any>>(`SELECT * FROM ${table} WHERE ${id}=?`, draft.id);
  if (remaining.length) throw new Error('O rascunho mudou ou não pôde ser excluído. Atualize a lista para conferir.');
}
