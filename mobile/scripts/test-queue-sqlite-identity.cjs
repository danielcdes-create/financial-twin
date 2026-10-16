// Actual SQLite engine, production queue helpers, synthetic in-memory rows only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const {DatabaseSync}=require('node:sqlite');
const cache=new Map();
const mocks={'./pdfCpf':{isPdfCapture:row=>/\.pdf$/i.test(row.filename||'')},'./uploadFile':{appendQueuedFile:async()=>{throw Error('No transport in native SQLite test');}}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const module={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
    {module,exports:module.exports,console,Error,Date,Math,require:name=>mocks[name]||(name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts'):require(name))});
  cache.set(file,module.exports);return module.exports;
}
const {createQueueSync}=load('utils/syncQueue.ts');
const {persistQueueFailure,clearQueueFailure}=load('utils/queueFailures.ts');
const {finalizeQueueRow}=load('utils/queueFinalization.ts');
const {persistQueueAcknowledgement,readQueueAcknowledgement}=load('utils/queueAcknowledgements.ts');
const {requeueAsStatement}=load('utils/queueRecovery.ts');
const sqlite=new DatabaseSync(':memory:');
sqlite.exec('CREATE TABLE event_queue(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,kind TEXT,payload TEXT,local_uri TEXT,filename TEXT,mime_type TEXT);CREATE TABLE queue_state(event_id TEXT PRIMARY KEY,attempt_count INTEGER,next_attempt_at TEXT,last_error TEXT,quarantined INTEGER);CREATE TABLE queue_acknowledgements(event_id TEXT PRIMARY KEY,identity TEXT,result TEXT,confirmed_at TEXT);');
const db={getAllAsync:async(sql,...args)=>sqlite.prepare(sql).all(...args),runAsync:async(sql,...args)=>sqlite.prepare(sql).run(...args)};
const active='SELECT e.*,s.attempt_count,s.next_attempt_at,s.last_error FROM event_queue e LEFT JOIN queue_state s ON s.event_id=e.event_id WHERE COALESCE(s.quarantined,0)=0 ORDER BY e.id';
const seed=async(eventId,payload='{}')=>{await db.runAsync('INSERT INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type) VALUES(?,?,?,NULL,NULL,NULL)',eventId,'event',payload);return(await db.getAllAsync('SELECT * FROM event_queue WHERE event_id=?',eventId))[0];};
const state=async id=>(await db.getAllAsync('SELECT * FROM queue_state WHERE event_id=?',id))[0];
(async()=>{
  const original=await seed('synthetic-guard');
  await persistQueueFailure(db,original,'genuine failure',1,'2026-10-04',false);
  assert.equal((await state(original.event_id)).attempt_count,1,'Matching null-URI row records a genuine failure');
  await db.runAsync("UPDATE event_queue SET kind='batch' WHERE event_id=?",original.event_id);
  await persistQueueFailure(db,original,'obsolete purpose',2,'2026-10-04',true);
  assert.equal((await state(original.event_id)).quarantined,0,'Old purpose cannot quarantine the new route');
  await clearQueueFailure(db,original);
  assert.equal((await state(original.event_id)).attempt_count,1,'Old retry cannot clear the new route state');
  await db.runAsync('DELETE FROM event_queue WHERE event_id=?',original.event_id);
  const replacement=await seed(original.event_id,'{"replacement":true}');
  assert.notEqual(replacement.id,original.id);
  await persistQueueFailure(db,original,'obsolete identity',3,'2026-10-04',true);
  assert.equal((await state(original.event_id)).attempt_count,1);
  await clearQueueFailure(db,original);assert.equal((await state(original.event_id)).attempt_count,1,'Old retry cannot clear replacement state');
  await clearQueueFailure(db,replacement);assert.equal(await state(original.event_id),undefined,'Matching retry clears only its own state');
  await assert.rejects(finalizeQueueRow(db,original,()=>{}),/mudou/);
  await finalizeQueueRow(db,replacement,()=>{});
  assert.equal(await state(original.event_id),undefined);

  const cleanup=await seed('synthetic-cleanup');let replaced=false;
  await finalizeQueueRow({...db,getAllAsync:async(sql,...args)=>{
    const snapshot=await db.getAllAsync(sql,...args);
    if(sql.startsWith('SELECT * FROM event_queue')&&!snapshot.length&&!replaced){replaced=true;const next=await seed(cleanup.event_id,'{"new":true}');await persistQueueFailure(db,next,'Replacement state',4,'2026-10-05',true);}
    return snapshot;
  }},cleanup,()=>{});
  assert.equal((await state(cleanup.event_id)).attempt_count,4,'Conditional cleanup preserves replacement metadata');
  await db.runAsync('DELETE FROM event_queue WHERE event_id=?',cleanup.event_id);
  await db.runAsync('DELETE FROM queue_state WHERE event_id=?',cleanup.event_id);

  await db.runAsync('INSERT INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type) VALUES(?,?,?,?,?,?)','synthetic-reroute','document','{}','file:///synthetic','synthetic.pdf','application/pdf');
  const document=(await db.getAllAsync('SELECT * FROM event_queue WHERE event_id=?','synthetic-reroute'))[0];
  await persistQueueFailure(db,document,'only BRL is supported',1,'2026-10-04',true);
  await requeueAsStatement({...db,runAsync:async(sql,...args)=>{
    await db.runAsync(sql,...args);
    if(sql.startsWith("UPDATE event_queue SET kind='batch'")){const batch=(await db.getAllAsync('SELECT * FROM event_queue WHERE event_id=?',document.event_id))[0];await persistQueueFailure(db,batch,'New batch failure',2,'2026-10-05',true);}
  }},{...document,last_error:'only BRL is supported'});
  assert.equal((await state(document.event_id)).attempt_count,2,'Reroute cleanup preserves failure from the new route');
  await db.runAsync('DELETE FROM event_queue WHERE event_id=?',document.event_id);
  await db.runAsync('DELETE FROM queue_state WHERE event_id=?',document.event_id);

  const receipt=await seed('synthetic-receipt');let sends=0,localFailure=true;
  const options={prepare:async()=>true,list:()=>db.getAllAsync(active),send:async row=>{sends++;return{persisted:true,event_id:row.event_id};},
    getAcknowledgement:row=>readQueueAcknowledgement(db,row),acknowledge:(row,result)=>persistQueueAcknowledgement(db,row,result),
    remove:row=>{if(localFailure)throw Error('Synthetic cleanup failure');return finalizeQueueRow(db,row,()=>{});},
    recordFailure:(...args)=>persistQueueFailure(db,...args),done(){},changed(){}};
  await createQueueSync(options)();assert.equal(sends,1);assert.ok(await readQueueAcknowledgement(db,receipt));assert.equal(await state(receipt.event_id),undefined);
  localFailure=false;await createQueueSync(options)();assert.equal(sends,1,'New driver recovers a persisted SQLite receipt without sending');
  assert.equal((await db.getAllAsync(active)).length,0);

  const lost=await seed('synthetic-response-loss'),server=new Map();let attempts=0,writes=0;
  const lostOptions={...options,send:async row=>{attempts++;if(!server.has(row.event_id)){server.set(row.event_id,{payload:row.payload,result:{persisted:true,event_id:row.event_id}});writes++;}assert.equal(server.get(row.event_id).payload,row.payload);if(attempts===1)throw Error('Response lost after commit');return server.get(row.event_id).result;}};
  await createQueueSync(lostOptions)();assert.equal((await state(lost.event_id)).attempt_count,1);assert.equal((await db.getAllAsync(active))[0].event_id,lost.event_id);
  await db.runAsync('DELETE FROM queue_state WHERE event_id=?',lost.event_id); // Explicit retry in synthetic storage.
  await createQueueSync(lostOptions)();assert.equal(attempts,2);assert.equal(writes,1);assert.equal((await db.getAllAsync(active)).length,0);
  console.log('Actual SQLite '+sqlite.prepare('SELECT sqlite_version() AS version').get().version+': full identity/null URI, stale state, replacement/reroute cleanup, ACK restart and lost-response idempotency passed.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>sqlite.close());
