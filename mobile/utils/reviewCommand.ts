type Db = {
  getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>;
  runAsync: (sql: string, ...params: any[]) => Promise<unknown>;
};
type Command = {command_key: string; path: string; body: string; result: string | null; updated_at: string};
export const newOperationId = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
  const value = Math.random() * 16 | 0;
  return (c === 'x' ? value : (value & 3) | 8).toString(16);
});
const commandKey = (id: string) => `provisional:${id}`;
const flights = new Map<string, Promise<any>>();
export async function pendingReviewCommand(db: Db, id: string): Promise<Command | null> {
  const rows = await db.getAllAsync<Command>('SELECT * FROM review_commands WHERE command_key=?', commandKey(id));
  return rows[0] && !rows[0].result ? rows[0] : null;
}
export async function pendingReviewCommands(db: Db): Promise<Command[]> {
  const rows = await db.getAllAsync<Command>('SELECT * FROM review_commands');
  return rows.filter(row => row.command_key.startsWith('provisional:') && !row.result);
}
export async function reviewDraftWasCompleted(db: Db, draft: {provisional_id: string; item: any; updated_at: string}): Promise<boolean> {
  const rows = await db.getAllAsync<Command>('SELECT * FROM review_commands WHERE command_key=?', commandKey(draft.provisional_id));
  const command = rows[0];
  return !!command?.result && JSON.parse(command.result).persisted === true &&
    JSON.parse(command.body).expected_revision === (draft.item.revision || 1) && draft.updated_at <= command.updated_at;
}
export function runReviewCommand(db: Db, api: (path: string, init?: RequestInit) => Promise<any>, id: string,
  path?: string, values?: any): Promise<any> {
  const key=commandKey(id), previous=flights.get(key)||Promise.resolve();
  const work=()=>performReviewCommand(db,api,id,path,values);
  const task=previous.catch(()=>undefined).then(()=>typeof navigator!=='undefined'&&navigator.locks
    ? navigator.locks.request('financial-twin-review-command:'+key,work) : work());
  flights.set(key,task);
  void task.finally(()=>{if(flights.get(key)===task)flights.delete(key)}).catch(()=>undefined);
  return task;
}
async function performReviewCommand(db: Db, api: (path: string, init?: RequestInit) => Promise<any>, id: string,
  path?: string, values?: any): Promise<any> {
  const rows = await db.getAllAsync<Command>('SELECT * FROM review_commands WHERE command_key=?', commandKey(id));
  let command = rows[0];
  const previous = command && JSON.parse(command.body);
  const withoutId = (body: any) => {const {request_id: _requestId, ...content} = body; return content;};
  if (values && command && !command.result && (command.path !== path || JSON.stringify(withoutId(previous)) !== JSON.stringify(values)))
    throw new Error('Existe uma tentativa anterior sem confirmação. Use Concluir tentativa anterior antes de salvar novas alterações. Seus campos foram preservados.');
  if (values && (!command || command.result)) {
    command = {command_key: commandKey(id), path: path!, body: JSON.stringify({...values, request_id: newOperationId()}), result: null, updated_at: new Date().toISOString()};
    await db.runAsync('INSERT INTO review_commands(command_key,path,body,result,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(command_key) DO UPDATE SET path=excluded.path,body=excluded.body,result=excluded.result,updated_at=excluded.updated_at',
      command.command_key, command.path, command.body, null, command.updated_at);
  }
  if (!command) throw new Error('Nenhuma tentativa anterior encontrada.');
  if (command.result) return JSON.parse(command.result);
  let response: any;
  try {response = await api(command.path, {method: /\/(decision|reparse)$/.test(command.path) ? 'POST' : 'PATCH', body: command.body});}
  catch (error) {
    const failure = error as {status?: number; message?: string; retryable?: boolean};
    // Pending transaction conflicts can still commit. Retain the exact command
    // until ACK; a definitive precondition rejection instead permits correction.
    if (!failure.retryable && [400,404,409,410,422,428].includes(failure.status || 0)) {
      try {await db.runAsync('INSERT INTO review_commands(command_key,path,body,result,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(command_key) DO UPDATE SET path=excluded.path,body=excluded.body,result=excluded.result,updated_at=excluded.updated_at',
        command.command_key, command.path, command.body, JSON.stringify({rejected:true,status:failure.status,message:failure.message}), new Date().toISOString());} catch {/* Preserve the original rejection and pending command. */}
    }
    throw error;
  }
  if (response?.persisted !== true || response.provisional_id !== id || response.request_id !== JSON.parse(command.body).request_id)
    throw new Error('O servidor não confirmou esta operação. A tentativa e o rascunho continuam salvos.');
  // A receipt-write failure cannot change the already persisted remote outcome.
  try {await db.runAsync('INSERT INTO review_commands(command_key,path,body,result,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(command_key) DO UPDATE SET path=excluded.path,body=excluded.body,result=excluded.result,updated_at=excluded.updated_at',
    command.command_key, command.path, command.body, JSON.stringify(response), new Date().toISOString());}
  catch {response.local_finalization_pending = true;}
  return response;
}
