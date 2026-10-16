// A resumed editor keeps the version its local fields were based on.
export function sameReviewSource(original: any, current: any): boolean {
  if (!original || !current || original.provisional_id !== current.provisional_id) return false;
  if ((original.revision || 1) !== (current.revision || 1)) return false;
  if (original.source_token && current.source_token) return original.source_token === current.source_token;
  return original.status === current.status && original.status_revisao === current.status_revisao;
}
export function batchDraftSource(batch: any): string {
  return JSON.stringify([batch.batch_id, batch.sha256, batch.complete, batch.rows]);
}
type Db = {runAsync: (sql: string, ...params: any[]) => Promise<unknown>};
export async function preserveConflictingDraft(db: Db, scope: string, id: string, state: any): Promise<void> {
  const updatedAt = new Date().toISOString();
  await db.runAsync('INSERT OR IGNORE INTO preserved_drafts(draft_id,scope,item_id,state,updated_at) VALUES(?,?,?,?,?)',
    `${scope}:${id}:${updatedAt}`, scope, id, JSON.stringify(state), updatedAt);
}
