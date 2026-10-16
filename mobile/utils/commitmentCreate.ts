export const CREATION_DRAFT = 'commitment-create';
type DraftDb = {getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>; runAsync: (sql: string, ...params: any[]) => Promise<unknown>};
export const creationId = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
  const r = Math.random() * 16 | 0; return (c === 'x' ? r : r & 3 | 8).toString(16);
});
export async function pendingCreation(db: DraftDb): Promise<Record<string, any> | null> {
  const rows = await db.getAllAsync<{state: string}>('SELECT * FROM batch_drafts WHERE batch_id=?', CREATION_DRAFT);
  const state=rows[0]?JSON.parse(rows[0].state):null;
  return state?.creation_receipt ? null : state;
}
export async function retainCreation(db: DraftDb, body: Record<string, any>) {
  await db.runAsync('INSERT INTO batch_drafts(batch_id,state,updated_at) VALUES(?,?,?) ON CONFLICT(batch_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at', CREATION_DRAFT, JSON.stringify(body), new Date().toISOString());
}
export async function forgetCreation(db: DraftDb) {
  await db.runAsync('DELETE FROM batch_drafts WHERE batch_id=?', CREATION_DRAFT);
}
export async function acknowledgeCreation(db:DraftDb,body:Record<string,any>,response:any){
  if(response?.persisted!==true||response.creation_id!==body.creation_id||!response.commitment_id)throw new Error('O servidor não confirmou a identidade do cadastro. Sua tentativa foi preservada.');
  try{await retainCreation(db,{...body,creation_receipt:response})}catch{return false;}
  try{await forgetCreation(db)}catch{/* The persisted receipt already prevents another creation after restart. */}
  return true;
}
