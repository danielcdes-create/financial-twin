import { requireNativeModule } from 'expo-modules-core';
export type CapturedEvent = { event_id:string; source_type:'android_notification'; source_provider:string; captured_at:string; raw_payload:Record<string,unknown>; parsed_payload?:Record<string,unknown> };
export type InboxSnapshot = {version:2; events:CapturedEvent[]; state:'ready'|'needs_recovery'|'unavailable'; recovery_count:number; error:string};
type NativeCapture = { isEnabled():boolean; openSettings():void; getQueuedEvents():string; getInboxSnapshot?():string; exportRecovery?():string; markSynced(eventId:string):void };
let native:NativeCapture|undefined;
try { native=requireNativeModule<NativeCapture>('NotificationCapture'); } catch {}
export function validateCapturedEvent(value: unknown): value is CapturedEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const event=value as CapturedEvent;
  const stamp=typeof event.captured_at==='string'?Date.parse(event.captured_at):NaN;
  return typeof event.event_id==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(event.event_id) && event.source_type==='android_notification' &&
    typeof event.source_provider==='string' && !!event.source_provider.trim() && Number.isFinite(stamp) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(event.captured_at) && new Date(stamp).toISOString().slice(0,19)===event.captured_at.slice(0,19) &&
    !!event.raw_payload && typeof event.raw_payload==='object' && !Array.isArray(event.raw_payload) &&
    (event.parsed_payload===undefined || (!!event.parsed_payload && typeof event.parsed_payload==='object' && !Array.isArray(event.parsed_payload)));
}
function getInboxSnapshot():InboxSnapshot {
  if(!native)return{version:2,events:[],state:'ready',recovery_count:0,error:''};
  try {
    const value=JSON.parse(native.getInboxSnapshot?native.getInboxSnapshot():JSON.stringify({version:2,events:JSON.parse(native.getQueuedEvents()),state:'ready',recovery_count:0,error:''}));
    if(value?.version!==2||!Array.isArray(value.events)||!['ready','needs_recovery','unavailable'].includes(value.state))throw new Error('Formato da fila nativa inválido.');
    const events=value.events.filter(validateCapturedEvent), invalid=events.length!==value.events.length;
    return{version:2,events,state:invalid?'needs_recovery':value.state,recovery_count:Number(value.recovery_count)||0,error:invalid?'Há notificações inválidas preservadas na origem. Recupere a fila local.':String(value.error||'')};
  }catch{return{version:2,events:[],state:'unavailable',recovery_count:0,error:'Não foi possível ler a fila de notificações. Preserve os dados locais e tente recuperar.'};}
}
export default {
  available:!!native,
  isEnabled:()=>native?.isEnabled()??false,
  openSettings:()=>native?.openSettings(),
  getInboxSnapshot,
  getQueuedEvents:():CapturedEvent[]=>{const snapshot=getInboxSnapshot();if(snapshot.state==='unavailable')throw new Error(snapshot.error);return snapshot.events;},
  exportRecovery:():string=>{if(!native?.exportRecovery)throw new Error('Atualize o Android para exportar a recuperação.');return native.exportRecovery();},
  markSynced:(id:string)=>native?.markSynced(id),
};
