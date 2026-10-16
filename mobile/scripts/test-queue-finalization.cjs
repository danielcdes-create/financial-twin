// Real storage and finalization. All rows/files are synthetic.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const storage=new Map(),cache=new Map();
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true,target:ts.ScriptTarget.ES2022}}).outputText,
{module:m,exports:m.exports,console,Date,localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},navigator:{locks:{request:async(_key,run)=>run()}},require:name=>name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts'):require(name)});cache.set(file,m.exports);return m.exports;}
const db=load('utils/queueDb.web.tsx').webQueueDb;
const {finalizeQueueRow}=load('utils/queueFinalization.ts');
const {persistQueueAcknowledgement,readQueueAcknowledgement}=load('utils/queueAcknowledgements.ts');
const list=()=>db.getAllAsync('SELECT * FROM event_queue ORDER BY id');
let active=true;
const ensure=()=>{if(!active)throw Object.assign(new Error('Session changed'),{status:401});};
const seed=async(id,uri=null)=>{if(uri)await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)',id,'document','{}',uri,'synthetic.txt','text/plain','2026-10-03');else await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,created_at) VALUES(?,?,?,?)',id,'native_event','{}','2026-10-03');return (await list()).find(row=>row.event_id===id);};
(async()=>{
const a=await seed('null-uri');await persistQueueAcknowledgement(db,a,{persisted:true,event_id:a.event_id});
await finalizeQueueRow(db,a,ensure);assert.equal((await list()).length,0,'SQL IS removes null URI native event');
assert.equal((await readQueueAcknowledgement(db,a)).event_id,a.event_id,'Receipt survives queue deletion');
const b=await seed('shared-a','file:///synthetic/shared'),c=await seed('shared-b','file:///synthetic/shared');
await persistQueueAcknowledgement(db,b,{persisted:true,event_id:b.event_id});
await finalizeQueueRow(db,b,ensure);
await assert.rejects(()=>finalizeQueueRow(db,{...c,payload:'stale'},ensure),/mudou/);
assert.equal((await list()).length,1,'Stale payload cannot remove current row');
active=false;await assert.rejects(()=>finalizeQueueRow(db,c,ensure),/Session changed/);active=true;
await persistQueueAcknowledgement(db,c,{persisted:true,event_id:c.event_id});
const realRun=db.runAsync.bind(db);let inserted=false;
const racingDb={getAllAsync:db.getAllAsync.bind(db),runAsync:async(sql,...args)=>{const result=await realRun(sql,...args);if(sql.includes('DELETE FROM queue_state')&&!inserted){inserted=true;await seed('new-reference',c.local_uri);}return result;}};
await finalizeQueueRow(racingDb,c,ensure);
assert.equal((await list())[0].event_id,'new-reference','New shared reference survives finalization');
await db.runAsync('DELETE FROM event_queue WHERE event_id=?','new-reference');
assert.equal((await list()).length,0,'Financial acknowledgement is independent from housekeeping');
cache.delete(path.resolve('utils/queueAcknowledgements.ts'));
const restarted=load('utils/queueAcknowledgements.ts');
const receipt=await restarted.readQueueAcknowledgement(db,c);
assert.equal(receipt.event_id,c.event_id,'Restart retains receipt without resubmission');
const archived=(await db.getAllAsync('SELECT * FROM queue_acknowledgements WHERE event_id=?',c.event_id))[0];
assert.equal(JSON.parse(archived.identity)[2],c.local_uri,'Original private bytes stay linked to durable receipt');
assert.doesNotMatch(fs.readFileSync('utils/queueFinalization.ts','utf8'),/removeFile|\.delete\(/,'Finalization cannot delete original bytes in a filesystem race');
await assert.rejects(()=>readQueueAcknowledgement(db,{...c,payload:'changed'}),/outro conteúdo/);
console.log('Queue finalization: null URI, matching identity, durable ACK, concurrent shared reference, session and restart receipt passed; original bytes preserved.');
})().catch(error=>{console.error(error);process.exitCode=1;});
