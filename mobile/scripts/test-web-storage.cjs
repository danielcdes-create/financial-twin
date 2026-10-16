// Exercise the real adapter with isolated storage; never touches browser data.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
const state=new Map();let quotaFailure=false;
const sandbox={exports:{},require,console,Map,JSON,Math,Number,String,Boolean,Error,navigator:{locks:{request:async(_name,fn)=>fn()}},localStorage:{getItem:key=>state.get(key)||null,setItem:(key,value)=>{if(quotaFailure)throw new Error('Quota exceeded');state.set(key,value)}}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('utils/queueDb.web.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText,sandbox);
const db=sandbox.exports.webQueueDb;
(async()=>{
 const insert='INSERT OR IGNORE INTO event_queue (event_id,kind,payload,created_at) VALUES (?,?,?,?)';
 for(let i=0;i<241;i++)await db.runAsync(insert,`event-${i}`,'text','{}','2026-09-29');
 await db.runAsync(insert,'event-0','text','{}','2026-09-29');
 const active='SELECT e.*, s.attempt_count, s.next_attempt_at, s.last_error FROM event_queue e LEFT JOIN queue_state s ON s.event_id=e.event_id WHERE COALESCE(s.quarantined,0)=0 ORDER BY e.id';
 assert.equal((await db.getAllAsync(active)).length,241);
 await db.runAsync('INSERT INTO queue_state','event-0',3,null,'Failure',1);
 assert.equal((await db.getAllAsync(active)).length,240);
 assert.equal((await db.getAllAsync(active.replace('COALESCE(s.quarantined,0)=0','s.quarantined=1'))).length,1);
 await db.runAsync('INSERT INTO review_drafts','capture','{}','{"vendedor":"Preservar"}','2026-09-29');
 quotaFailure=true;await assert.rejects(db.runAsync('INSERT INTO review_drafts','capture','{}','changed','2026-09-29'));quotaFailure=false;
 assert.equal((await db.getAllAsync('SELECT * FROM review_drafts'))[0].draft,'{"vendedor":"Preservar"}');
 state.set('financial-twin-event-queue','corrupt');await assert.rejects(db.getAllAsync(active));assert.equal(state.get('financial-twin-event-queue'),'corrupt');
 console.log('Web queue: expanded SELECT, 241 rows, idempotence, quarantine, quota failure, corrupt preservation — passed');
})().catch(error=>{console.error(error);process.exitCode=1});
