import React, { createContext, useContext, useMemo } from 'react';

type QueueItem={id:number;event_id:string;kind:string;payload:string;local_uri:string|null;filename:string|null;mime_type:string|null;created_at:string};
type QueueState={event_id:string;attempt_count:number;next_attempt_at:string|null;last_error:string;quarantined:number};
type ReviewDraft={provisional_id:string;item:string;draft:string;updated_at:string};
type QueueDb={getAllAsync:<T>(sql:string,...params:any[])=>Promise<T[]>;runAsync:(sql:string,...params:any[])=>Promise<void>;execAsync:(sql:string)=>Promise<void>};
const KEY='financial-twin-event-queue';
const STATE_KEY='financial-twin-queue-state';
const DRAFT_KEY='financial-twin-review-drafts';
const BATCH_KEY='financial-twin-batch-drafts';
const SEQUENCE_KEY='financial-twin-queue-sequence';
const ACK_KEY='financial-twin-queue-acknowledgements';
const PRESERVED_KEY='financial-twin-preserved-drafts';
const COMMAND_KEY='financial-twin-review-commands';
const VOICE_KEY='financial-twin-voice-drafts';
const MAX_CONFIRMED_RECEIPTS=500;
const Context=createContext<QueueDb|null>(null);
function read():QueueItem[]{try{return JSON.parse(localStorage.getItem(KEY)||'[]')}catch{throw new Error('Dados locais não puderam ser lidos. Preserve o armazenamento do navegador para recuperação.')}}
function write(rows:QueueItem[]){localStorage.setItem(KEY,JSON.stringify(rows))}
function readState():QueueState[]{try{return JSON.parse(localStorage.getItem(STATE_KEY)||'[]')}catch{throw new Error('Dados locais não puderam ser lidos. Preserve o armazenamento do navegador para recuperação.')}}
function writeState(rows:QueueState[]){localStorage.setItem(STATE_KEY,JSON.stringify(rows))}
function readDrafts():ReviewDraft[]{try{return JSON.parse(localStorage.getItem(DRAFT_KEY)||'[]')}catch{throw new Error('Dados locais não puderam ser lidos. Preserve o armazenamento do navegador para recuperação.')}}
function writeDrafts(rows:ReviewDraft[]){localStorage.setItem(DRAFT_KEY,JSON.stringify(rows))}
type BatchDraft={batch_id:string;state:string;updated_at:string};
function readBatches():BatchDraft[]{try{return JSON.parse(localStorage.getItem(BATCH_KEY)||'[]')}catch{throw new Error('A revisão do lote não pôde ser lida. Preserve os dados do navegador para recuperação.')}}
type QueueAck={event_id:string;identity:string;result:string;confirmed_at:string};
function readAcks():QueueAck[]{try{return JSON.parse(localStorage.getItem(ACK_KEY)||'[]')}catch{throw new Error('As confirmações locais não puderam ser lidas. Preserve os dados para recuperação.')}}
type PreservedDraft={draft_id:string;scope:string;item_id:string;state:string;updated_at:string};
function readPreserved():PreservedDraft[]{try{return JSON.parse(localStorage.getItem(PRESERVED_KEY)||'[]')}catch{throw new Error('Os rascunhos preservados não puderam ser lidos. Mantenha os dados para recuperação.')}}
type ReviewCommand={command_key:string;path:string;body:string;result:string|null;updated_at:string};
function readCommands():ReviewCommand[]{try{return JSON.parse(localStorage.getItem(COMMAND_KEY)||'[]')}catch{throw new Error('As tentativas de revisão não puderam ser lidas. Preserve os dados para recuperação.')}}
type VoiceDraftRow={draft_key:string;state:string;updated_at:string};
function readVoice():VoiceDraftRow[]{try{return JSON.parse(localStorage.getItem(VOICE_KEY)||'[]')}catch{throw new Error('O rascunho de voz não pôde ser lido. Preserve os dados para recuperação.')}}
export const webQueueDb:QueueDb={
 async getAllAsync<T>(sql:string,...params:any[]){
  if(sql.startsWith('SELECT e.*')&&sql.includes('FROM event_queue')){
   const states=new Map(readState().map(row=>[row.event_id,row]));
   const quarantined=sql.includes('s.quarantined=1');
   return read().filter(row=>Boolean(states.get(row.event_id)?.quarantined)===quarantined).sort((a,b)=>a.id-b.id).map(row=>({...row,attempt_count:states.get(row.event_id)?.attempt_count||0,next_attempt_at:states.get(row.event_id)?.next_attempt_at||null,last_error:states.get(row.event_id)?.last_error||null})) as T[];
  }
  if(sql.startsWith('SELECT * FROM event_queue')){const rows=read().sort((a,b)=>a.id-b.id);return (sql.includes('WHERE event_id=?')?rows.filter(row=>row.event_id===String(params[0])):rows) as T[];}
  if(sql.startsWith('SELECT * FROM review_drafts')){const rows=params.length?readDrafts().filter(row=>row.provisional_id===String(params[0])):readDrafts();return rows.sort((a,b)=>b.updated_at.localeCompare(a.updated_at)).slice(0,sql.includes('LIMIT 1')?1:undefined) as T[]}
  if(sql.startsWith('SELECT * FROM batch_drafts'))return readBatches().filter(row=>!params.length||row.batch_id===String(params[0])) as T[];
  if(sql.startsWith('SELECT * FROM queue_acknowledgements'))return readAcks().filter(row=>!params.length||row.event_id===String(params[0])) as T[];
  if(sql.startsWith('SELECT * FROM queue_state'))return readState().filter(row=>!params.length||row.event_id===String(params[0])) as T[];
  if(sql.startsWith('SELECT * FROM preserved_drafts'))return readPreserved().filter(row=>!params.length||(sql.includes('WHERE draft_id=?')?row.draft_id:row.item_id)===String(params[0])) as T[];
  if(sql.startsWith('SELECT * FROM review_commands'))return readCommands().filter(row=>(!sql.includes('WHERE result IS NULL')||row.result===null)&&(!params.length||row.command_key===String(params[0]))) as T[];
  if(sql.startsWith('SELECT * FROM voice_drafts'))return readVoice().filter(row=>!params.length||row.draft_key===String(params[0])) as T[];
  throw new Error('Consulta local não suportada')
 },
 async runAsync(sql:string,...params:any[]){
  const run=async()=>{
  const matchesDraft=(row:Record<string,any>,idField:string,fields:string[])=>{
    if(row[idField]!==String(params[0]))return false;
    const guarded=fields.filter(field=>sql.includes(` AND ${field}=?`));
    return guarded.every((field,index)=>row[field]===params[index+1]);
  };
  if(sql.startsWith('DELETE FROM voice_drafts WHERE draft_key=?')){localStorage.setItem(VOICE_KEY,JSON.stringify(readVoice().filter(row=>!matchesDraft(row,'draft_key',['state','updated_at']))));return}
  if(sql.startsWith('DELETE FROM preserved_drafts WHERE draft_id=?')){localStorage.setItem(PRESERVED_KEY,JSON.stringify(readPreserved().filter(row=>!matchesDraft(row,'draft_id',['state','updated_at']))));return}
  if(sql.startsWith('DELETE FROM review_drafts WHERE provisional_id=?')){writeDrafts(readDrafts().filter(row=>!matchesDraft(row,'provisional_id',['item','draft','updated_at'])));return}
  if(sql.startsWith('DELETE FROM batch_drafts WHERE batch_id=?')){localStorage.setItem(BATCH_KEY,JSON.stringify(readBatches().filter(row=>!matchesDraft(row,'batch_id',['state','updated_at']))));return}
  const rows=read();
  if(sql.startsWith('INSERT INTO voice_drafts')){const drafts=readVoice().filter(row=>row.draft_key!==String(params[0]));drafts.push({draft_key:String(params[0]),state:String(params[1]),updated_at:String(params[2])});localStorage.setItem(VOICE_KEY,JSON.stringify(drafts));return}
  if(sql.startsWith('INSERT INTO review_commands')){const commands=readCommands().filter(row=>row.command_key!==String(params[0]));commands.push({command_key:String(params[0]),path:String(params[1]),body:String(params[2]),result:params[3]==null?null:String(params[3]),updated_at:String(params[4])});const pendingCmds=commands.filter(row=>row.result===null);const completedCmds=commands.filter(row=>row.result!==null).slice(-MAX_CONFIRMED_RECEIPTS);localStorage.setItem(COMMAND_KEY,JSON.stringify([...pendingCmds,...completedCmds]));return}
  if(sql.startsWith('INSERT OR IGNORE INTO preserved_drafts')){const drafts=readPreserved();if(drafts.some(row=>row.draft_id===String(params[0])))return;drafts.push({draft_id:String(params[0]),scope:String(params[1]),item_id:String(params[2]),state:String(params[3]),updated_at:String(params[4])});localStorage.setItem(PRESERVED_KEY,JSON.stringify(drafts));return}
  if(sql.startsWith('INSERT OR IGNORE INTO queue_acknowledgements')){const receipts=readAcks();if(receipts.some(row=>row.event_id===String(params[0])))return;receipts.push({event_id:String(params[0]),identity:String(params[1]),result:String(params[2]),confirmed_at:String(params[3])});localStorage.setItem(ACK_KEY,JSON.stringify(receipts.slice(-MAX_CONFIRMED_RECEIPTS)));return}
  if(sql.startsWith('INSERT OR IGNORE INTO event_queue')){const eventId=String(params[0]);if(rows.some(row=>row.event_id===eventId))return;const hasFile=sql.includes('local_uri,filename,mime_type');const id=Math.max(Number(localStorage.getItem(SEQUENCE_KEY)||0),0,...rows.map(row=>row.id))+1;if(!Number.isSafeInteger(id))throw new Error('Identidade da fila inválida. Preserve os dados para recuperação.');localStorage.setItem(SEQUENCE_KEY,String(id));rows.push({id,event_id:eventId,kind:String(params[1]),payload:String(params[2]),local_uri:hasFile?String(params[3]):null,filename:hasFile?String(params[4]):null,mime_type:hasFile?String(params[5]):null,created_at:String(params[hasFile?6:3])});write(rows);return}
  if(sql.startsWith('INSERT INTO queue_state')){
   if(sql.includes('WHERE EXISTS (SELECT 1 FROM event_queue')&&!rows.some(row=>row.event_id===String(params[5])&&row.kind===params[6]&&row.local_uri===params[7]&&row.payload===params[8]&&row.filename===params[9]&&row.mime_type===params[10]&&row.id===params[11]))return;
   const eventId=String(params[0]);writeState([...readState().filter(row=>row.event_id!==eventId),{event_id:eventId,attempt_count:Number(params[1]),next_attempt_at:params[2]==null?null:String(params[2]),last_error:String(params[3]),quarantined:Number(params[4])}]);return
  }
  if(sql === "UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document'"){write(rows.map(row=>row.event_id===String(params[0])&&row.kind==='document'?{...row,kind:'batch'}:row));return}
  if(sql === "UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document' AND local_uri IS ? AND payload=? AND filename IS ? AND mime_type IS ? AND id=?"){
   write(rows.map(row=>row.event_id===String(params[0])&&row.kind==='document'&&row.local_uri===params[1]&&row.payload===params[2]&&row.filename===params[3]&&row.mime_type===params[4]&&row.id===params[5]?{...row,kind:'batch'}:row));return
  }
  if(sql.startsWith('DELETE FROM queue_state')){
   if(sql.includes('AND NOT EXISTS (SELECT 1 FROM event_queue WHERE event_id=?)')&&rows.some(row=>row.event_id===String(params[1])))return;
   if(sql.includes('AND EXISTS (SELECT 1 FROM event_queue WHERE event_id=? AND kind=?')&&!rows.some(row=>row.event_id===String(params[1])&&row.kind===params[2]&&row.local_uri===params[3]&&row.payload===params[4]&&row.filename===params[5]&&row.mime_type===params[6]&&row.id===params[7]))return;
   if(sql.includes('AND attempt_count=? AND next_attempt_at IS ? AND last_error=? AND quarantined=?')){
    writeState(readState().filter(row=>!(row.event_id===String(params[0])&&row.attempt_count===params[1]&&row.next_attempt_at===params[2]&&row.last_error===params[3]&&row.quarantined===params[4])));return;
   }
   writeState(readState().filter(row=>row.event_id!==String(params[0])));return
  }
  if(sql.startsWith('DELETE FROM event_queue')){const byEvent=sql.includes('WHERE event_id=?');const guarded=/^DELETE FROM event_queue WHERE event_id=\? AND kind=\? AND local_uri(?:=| IS )\? AND payload=\? AND filename IS \? AND mime_type IS \? AND id=\?$/.test(sql);const matches=(row:QueueItem)=>guarded?row.event_id===String(params[0])&&row.kind===params[1]&&row.local_uri===params[2]&&row.payload===params[3]&&row.filename===params[4]&&row.mime_type===params[5]&&row.id===params[6]:byEvent?row.event_id===String(params[0]):row.id===Number(params[0])&&row.event_id===String(params[1]);const removed=rows.find(matches);write(rows.filter(row=>!matches(row)));if(removed)writeState(readState().filter(row=>row.event_id!==removed.event_id));return}
  if(sql.startsWith('INSERT INTO review_drafts')){const drafts=readDrafts().filter(row=>row.provisional_id!==String(params[0]));drafts.push({provisional_id:String(params[0]),item:String(params[1]),draft:String(params[2]),updated_at:String(params[3])});writeDrafts(drafts);return}
  if(sql.startsWith('INSERT INTO batch_drafts')){const batches=readBatches().filter(row=>row.batch_id!==String(params[0]));batches.push({batch_id:String(params[0]),state:String(params[1]),updated_at:String(params[2])});localStorage.setItem(BATCH_KEY,JSON.stringify(batches));return}
  throw new Error('Operação local não suportada')
  };
  if(!navigator.locks)throw new Error('Use um navegador com suporte a bloqueio de armazenamento local para salvar capturas com segurança. Os dados existentes foram preservados.');
  await navigator.locks.request('financial-twin-local-write',run);
 },
 async execAsync(){},
};
export function SQLiteProvider({children}:{children:React.ReactNode;databaseName:string;onInit?:(db:QueueDb)=>Promise<void>}){const value=useMemo(()=>webQueueDb,[]);return <Context.Provider value={value}>{children}</Context.Provider>}
export function useSQLiteContext(){const value=useContext(Context);if(!value)throw new Error('Fila local indisponível');return value}
