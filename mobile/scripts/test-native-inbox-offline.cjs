const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript'),{DatabaseSync}=require('node:sqlite');
function load(file,mocks={}){const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{module:m,exports:m.exports,Error,Date,JSON,Set,require:name=>{if(mocks[name])return mocks[name];throw Error(name);}});return m.exports;}
const {importNativeInbox,nativePendingCount}=load('utils/nativeInbox.ts');
const event={event_id:'11111111-1111-4111-8111-111111111111',source_type:'android_notification',source_provider:'com.nu.production',captured_at:'2026-10-09T10:00:00.000Z',raw_payload:{title:'Compra aprovada'},parsed_payload:{amount:8,currency:'BRL',merchant_raw:'Café'}};
const snapshot={version:2,events:[event],state:'ready',recovery_count:0,error:''};
const sqlite=new DatabaseSync(':memory:');sqlite.exec('CREATE TABLE event_queue(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,kind TEXT,payload TEXT,created_at TEXT,local_uri TEXT,filename TEXT,mime_type TEXT);CREATE TABLE queue_acknowledgements(event_id TEXT PRIMARY KEY,identity TEXT,result TEXT,confirmed_at TEXT);');
let blocked=false,failed=false;
const db={getAllAsync:async(sql,...args)=>sqlite.prepare(sql).all(...args),runAsync:async(sql,...args)=>{if(failed)throw Error('SQLite quota failure');return sqlite.prepare(sql).run(...args);}};
const all=()=>db.getAllAsync('SELECT * FROM event_queue ORDER BY id');
(async()=>{
 // Offline ingestion is local and requires no transport at all.
 assert.equal(nativePendingCount([],[],snapshot.events),1);await importNativeInbox(db,snapshot,()=>{assert.equal(blocked,false);});assert.equal((await all()).length,1);
 assert.equal(nativePendingCount(await all(),[],snapshot.events),1,'Native+SQLite same event is counted once');
 await importNativeInbox(db,snapshot,()=>{});assert.equal((await all()).length,1,'Reconnection/replay uses same identity');
 assert.equal(nativePendingCount([],await all(),snapshot.events),0,'Quarantined notifications are not counted twice as sendable');
 blocked=true;await assert.rejects(importNativeInbox(db,snapshot,()=>{if(blocked)throw Object.assign(Error('locked'),{name:'AbortError'});}),/locked/);blocked=false;
 failed=true;await assert.rejects(importNativeInbox(db,snapshot,()=>{}),/quota/);failed=false;assert.equal(snapshot.events.length,1,'SQLite failure preserves original inbox');
 await assert.rejects(importNativeInbox(db,{...snapshot,state:'unavailable',error:'native failure'},()=>{}),/native failure/);
 let raw='[',readFailure=false;
 const bridge=load('modules/notification-capture/index.ts',{'expo-modules-core':{requireNativeModule:()=>({isEnabled:()=>true,getQueuedEvents:()=>{if(readFailure)throw Error('native read failed');return raw;}})}}).default;
 assert.equal(bridge.getInboxSnapshot().state,'unavailable');assert.throws(()=>bridge.getQueuedEvents(),/fila/);
 raw='{}';assert.equal(bridge.getInboxSnapshot().state,'unavailable');raw=JSON.stringify([event,{event_id:'broken'}]);assert.equal(bridge.getInboxSnapshot().state,'needs_recovery');assert.equal(bridge.getQueuedEvents().length,1);
 raw='[]';readFailure=true;assert.equal(bridge.getInboxSnapshot().state,'unavailable');readFailure=false;assert.equal(bridge.getInboxSnapshot().state,'ready');assert.equal(bridge.getQueuedEvents().length,0);
 const syncModule=load('utils/syncQueue.ts');
 const receipts=load('utils/queueAcknowledgements.ts',{'./syncQueue':syncModule});
 const row=(await all())[0];await receipts.persistQueueAcknowledgement(db,row,{persisted:true,event_id:event.event_id});
 await db.runAsync('DELETE FROM event_queue WHERE event_id=?',event.event_id);await importNativeInbox(db,snapshot,()=>{});
 let sends=0,marks=0;
 await syncModule.createQueueSync({prepare:async()=>true,list:all,getAcknowledgement:row=>receipts.readQueueAcknowledgement(db,row),acknowledge:(row,result)=>receipts.persistQueueAcknowledgement(db,row,result),send:async()=>{sends++;throw Error('No second financial transport after ACK');},done:()=>{marks++;},remove:row=>db.runAsync('DELETE FROM event_queue WHERE event_id=?',row.event_id),recordFailure:async()=>{throw Error('No failed attempt expected');},changed(){}})();
 assert.equal(sends,0);assert.equal(marks,1);assert.equal((await all()).length,0,'Native replay with durable ACK only finalizes local source');
 console.log('PASS real SQLite offline import, union counts, identity replay, quota/session preservation and strict native bridge failure/recovery states');
})().catch(error=>{console.error(error.stack);process.exitCode=1;}).finally(()=>sqlite.close());
