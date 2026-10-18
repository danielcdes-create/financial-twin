import type {CapturedEvent, InboxSnapshot} from '../modules/notification-capture';
import type {QueueRow} from './syncQueue';

type Db = {getAllAsync:<T>(sql:string,...params:any[])=>Promise<T[]>;runAsync:(sql:string,...params:any[])=>Promise<unknown>};
export async function importNativeInbox(db:Db, snapshot:InboxSnapshot, ensureSession:()=>void):Promise<void> {
  ensureSession();
  if(snapshot.state==='unavailable')throw new Error(snapshot.error);
  for(const event of snapshot.events){
    ensureSession();
    const payload=JSON.stringify(event);
    await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,created_at) VALUES(?,?,?,?)',event.event_id,'native_event',payload,event.captured_at);
    const stored=(await db.getAllAsync<QueueRow>('SELECT * FROM event_queue WHERE event_id=? ORDER BY id',event.event_id)).find(row=>row.event_id===event.event_id);
    ensureSession();
    if(!stored||stored.kind!=='native_event'||stored.payload!==payload)throw new Error('A notificação permanece na origem: a identidade da fila local não foi confirmada.');
  }
}
export function nativePendingCount(active:QueueRow[],quarantined:QueueRow[],events:CapturedEvent[]):number {
  const quarantinedIds=new Set(quarantined.map(row=>row.event_id));
  return new Set([...active.map(row=>row.event_id),...events.filter(event=>!quarantinedIds.has(event.event_id)).map(event=>event.event_id)]).size;
}
