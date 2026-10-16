// Production queue/journal/file helpers, isolated storage and transports.
// Set QUEUE_TRANSITIONS_ORIGINAL to the snapshot root to prove regressions.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const storage=new Map(),cache=new Map(),files=new Map(),failures=[];
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const abort=()=>Object.assign(Error('Synthetic suspension'),{name:'AbortError'});
const row=id=>({id,event_id:`synthetic-${id}`,kind:'event',payload:'{}',local_uri:null,filename:null,mime_type:null});
const ack=item=>({persisted:true,event_id:item.event_id});
const mocks={
  './uploadFile':{appendQueuedFile:async()=>{}},
  './sessionStore':{getItemAsync:async()=>{throw Error('Reconciliation cannot read stored CPF');}},
};
const indexedDB={open:()=>{
  const request={result:{transaction:()=>{
    const transaction={error:null,objectStore:()=>({
      add:(blob,key)=>write(blob,key,false),put:(blob,key)=>write(blob,key,true),
      get:key=>{const operation={result:files.get(key)};queueMicrotask(()=>transaction.oncomplete());return operation;},
    })};
    const write=(blob,key,replace)=>{const operation={};queueMicrotask(()=>{
      if(!replace&&files.has(key)){transaction.error=Object.assign(Error('Duplicate file identity'),{name:'ConstraintError'});transaction.onabort();}
      else {files.set(key,blob);operation.result=key;transaction.oncomplete();}
    });return operation;};
    return transaction;
  }}};
  queueMicrotask(()=>request.onsuccess());return request;
}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const module={exports:{}};
  const original=process.env.QUEUE_TRANSITIONS_ORIGINAL&&path.join(process.env.QUEUE_TRANSITIONS_ORIGINAL,path.relative(process.cwd(),file));
  const selected=original&&fs.existsSync(original)?original:file;
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(selected,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
    {module,exports:module.exports,console,Error,Date,Math,Promise,Blob,FormData,queueMicrotask,indexedDB,
      fetch:async uri=>({ok:true,blob:async()=>new Blob([uri])}),
      navigator:{locks:{request:async(_key,work)=>work()}},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
      require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:selected});
  cache.set(file,module.exports);return module.exports;
}
const {createQueueSync}=load('utils/syncQueue.ts');
const {reconcileImportedFiles,requeueAsStatement}=load('utils/queueRecovery.ts');
const {runReviewCommand,pendingReviewCommand}=load('utils/reviewCommand.ts');
const {persistQueuedFile,queuedBlob}=load('utils/durableFiles.ts');
const db=load('utils/queueDb.web.tsx').webQueueDb;
const {persistQueueFailure,clearQueueFailure}=load('utils/queueFailures.ts');
const {finalizeQueueRow}=load('utils/queueFinalization.ts');
const options=rows=>({prepare:async()=>true,list:async()=>rows.map(item=>({...item})),send:async item=>ack(item),remove:async item=>{const index=rows.findIndex(current=>current.id===item.id);if(index>=0)rows.splice(index,1);},recordFailure:async()=>{},done(){},changed(){}});
async function check(name,work){storage.clear();try{await work();console.log('PASS',name);}catch(error){failures.push(name);console.error('FAIL',name,error.stack);}}
(async()=>{
  await check('Suspension after an earlier failure clears the retry signal without erasing persisted backoff',async()=>{
    const rows=[row(1),row(2)],recorded=[];let changed;
    await createQueueSync({...options(rows),send:async item=>{if(item.id===1)throw Error('Synthetic offline');throw abort();},
      recordFailure:async(item,_message,attempts,next)=>recorded.push({id:item.id,attempts,next}),changed:(count,error)=>changed={count,error}})();
    assert.equal(recorded.length,1);assert.equal(recorded[0].id,1);assert.equal(recorded[0].attempts,1);
    assert.equal(rows.length,2);assert.equal(changed.count,2);assert.equal(changed.error,'','Abort must not feed the App retry scheduler');
  });
  await check('An unauthorized response stops duplicate reruns of the same queue cycle',async()=>{
    const rows=[row(3)],request=deferred();let sends=0;
    const sync=createQueueSync({...options(rows),send:async()=>{sends++;if(sends===1)return request.promise;throw Object.assign(Error('Unauthorized'),{status:401});}});
    const task=sync();await new Promise(resolve=>setImmediate(resolve));const duplicate=sync();request.reject(Object.assign(Error('Unauthorized'),{status:401}));
    await task;await duplicate;assert.equal(sends,1);assert.equal(rows.length,1);
  });
  await check('A queue snapshot cannot send a following row removed by another consumer',async()=>{
    const rows=[row(4),row(5)],sent=[];
    await createQueueSync({...options(rows),list:async()=>rows.map(item=>({...item})),send:async item=>{sent.push(item.id);if(item.id===4)rows.splice(1,1);return ack(item);}})();
    assert.deepEqual(sent,[4]);
  });
  await check('A changed route is preserved without sending its obsolete document snapshot',async()=>{
    const rows=[row(6),{...row(7),kind:'document',local_uri:'idb:original',filename:'synthetic.pdf',mime_type:'application/pdf'}],sent=[];
    await createQueueSync({...options(rows),list:async()=>rows.map(item=>({...item})),send:async item=>{sent.push(item.kind);if(item.id===6)rows[1].kind='batch';return ack(item);}})();
    assert.deepEqual(sent,['event']);assert.equal(rows[0].kind,'batch');
  });
  await check('Imported-file reconciliation propagates suspension and does not inspect the next file',async()=>{
    const rows=[{...row(8),kind:'document',local_uri:'idb:a',filename:'a.pdf'},{...row(9),kind:'document',local_uri:'idb:b',filename:'b.pdf'}];let uploads=0;
    await assert.rejects(reconcileImportedFiles({getAllAsync:async()=>rows,runAsync:async()=>{throw Error('Must not mutate');}},rows,async()=>{uploads++;throw abort();},()=>{}),error=>error.name==='AbortError');
    assert.equal(uploads,1);assert.equal(rows.length,2);
  });
  await check('Retryable 409 keeps the exact review command pending until persisted ACK',async()=>{
    let sends=0,original,replayed;
    const api=async(_path,init)=>{sends++;const body=JSON.parse(init.body);if(sends===1){original=body;throw Object.assign(Error('Operation still pending'),{status:409,retryable:true});}replayed=body;return{persisted:true,provisional_id:'retryable',request_id:body.request_id};};
    await assert.rejects(runReviewCommand(db,api,'retryable','/v1/provisional/retryable',{expected_revision:1,vendedor:'Synthetic'}),/pending/);
    assert.ok(await pendingReviewCommand(db,'retryable'),'Pending conflict must not retire the command');
    await assert.rejects(runReviewCommand(db,api,'retryable','/v1/provisional/retryable',{expected_revision:2,vendedor:'Changed'}),/tentativa anterior/);assert.equal(sends,1);
    await runReviewCommand(db,api,'retryable');assert.deepEqual(replayed,original);assert.equal(await pendingReviewCommand(db,'retryable'),null);
  });
  await check('A definitive 428 lets the user correct the review precondition and creates a new identity',async()=>{
    let first;
    await assert.rejects(runReviewCommand(db,async(_path,init)=>{first=JSON.parse(init.body);throw Object.assign(Error('Reopen to get revision'),{status:428});},'precondition','/v1/provisional/precondition',{vendedor:'Synthetic'}),/revision/);
    assert.equal(await pendingReviewCommand(db,'precondition'),null,'The rejected precondition cannot remain an unrecoverable pending write');
    await runReviewCommand(db,async(_path,init)=>{const body=JSON.parse(init.body);assert.notEqual(body.request_id,first.request_id);return{persisted:true,provisional_id:'precondition',request_id:body.request_id};},'precondition','/v1/provisional/precondition',{expected_revision:2,vendedor:'Synthetic'});
  });
  await check('Web durable files reject identity reuse while preserving the original bytes',async()=>{
    const uri=await persistQueuedFile('original bytes','immutable-file');assert.equal(await(await queuedBlob(uri)).text(),'original bytes');
    await assert.rejects(persistQueuedFile('replacement bytes','immutable-file'));
    assert.equal(await(await queuedBlob(uri)).text(),'original bytes','A pending capture/receipt must retain its immutable original');
  });
  await check('A stale transport failure cannot quarantine a rerouted or replaced queue row',async()=>{
    const insert='INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)';
    await db.runAsync(insert,'stale-failure','document','{}','idb:original','synthetic.pdf','application/pdf','2026-10-04');
    const original=(await db.getAllAsync('SELECT * FROM event_queue'))[0];
    await db.runAsync("UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document'",original.event_id);
    await persistQueueFailure(db,original,'Synthetic invalid document',1,'2026-10-04',true);
    const active='SELECT e.*, s.attempt_count FROM event_queue e LEFT JOIN queue_state s ON s.event_id=e.event_id WHERE COALESCE(s.quarantined,0)=0 ORDER BY e.id';
    assert.equal((await db.getAllAsync(active)).length,1,'Old purpose cannot quarantine the reviewed batch');
    assert.equal((await db.getAllAsync(active))[0].attempt_count,0);
    const rerouted=(await db.getAllAsync('SELECT * FROM event_queue'))[0];
    await persistQueueFailure(db,rerouted,'Synthetic batch failure',2,'2026-10-04',false);
    assert.equal((await db.getAllAsync(active))[0].attempt_count,2,'Matching row retains genuine retry metadata');
    await db.runAsync('DELETE FROM event_queue WHERE event_id=?',original.event_id);
    await db.runAsync(insert,original.event_id,'document','{}','idb:replacement','synthetic.pdf','application/pdf','2026-10-04');
    await persistQueueFailure(db,original,'Old failure',3,'2026-10-04',true);
    assert.equal((await db.getAllAsync(active)).length,1);assert.equal((await db.getAllAsync(active))[0].attempt_count,0);
  });
  await check('Finalization cannot erase a replacement retry state inserted after its empty snapshot',async()=>{
    const insert='INSERT OR IGNORE INTO event_queue(event_id,kind,payload,created_at) VALUES(?,?,?,?)';
    await db.runAsync(insert,'cleanup-race','event','{}','2026-10-04');
    const original=(await db.getAllAsync('SELECT * FROM event_queue'))[0];let added=false;
    const racingDb={runAsync:db.runAsync.bind(db),getAllAsync:async(...args)=>{
      const snapshot=await db.getAllAsync(...args);
      if(!snapshot.length&&!added){added=true;await db.runAsync(insert,original.event_id,'event','{"replacement":true}','2026-10-04');await db.runAsync('INSERT INTO queue_state',original.event_id,4,'2026-10-05','Replacement is quarantined',1);}
      return snapshot;
    }};
    await finalizeQueueRow(racingDb,original,()=>{});
    const quarantined='SELECT e.*, s.attempt_count FROM event_queue e JOIN queue_state s ON s.event_id=e.event_id WHERE s.quarantined=1 ORDER BY e.id';
    assert.equal((await db.getAllAsync(quarantined)).length,1);assert.equal((await db.getAllAsync(quarantined))[0].attempt_count,4);
  });
  await check('A failed preflight list cannot become a row retry or quarantined transport failure',async()=>{
    const rows=[row(10)];let lists=0,sends=0,recorded=0;
    await createQueueSync({...options(rows),list:async()=>{lists++;if(lists===3)throw Error('Synthetic local read failed');return rows;},send:async item=>{sends++;return ack(item);},recordFailure:async()=>{recorded++;}})();
    assert.equal(sends,0);assert.equal(recorded,0);assert.equal(rows.length,1);
  });
  await check('A session change after ACK stops the cycle and later finalizes the receipt without transport',async()=>{
    const rows=[row(11),row(12)];let sends=0,acknowledged=0,changed=true;
    const sync=createQueueSync({...options(rows),send:async item=>{sends++;return ack(item);},acknowledge:async()=>{acknowledged++;},done:()=>{if(changed)throw Object.assign(Error('Confirmed; session changed'),{status:401});}});
    await sync();assert.equal(sends,1);assert.equal(acknowledged,1);assert.equal(rows.length,2);
    changed=false;await sync();assert.equal(sends,2,'Receipt is finalized locally; only the following event is sent');assert.equal(rows.length,0);
  });
  await check('Exact committed-byte reconciliation also recovers images and tabular statement captures',async()=>{
    const insert='INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)';
    for(const [index,filename,mime] of [[1,'statement.png','image/png'],[2,'statement.jpg','image/jpeg'],[3,'statement.csv','text/csv'],[4,'statement.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']]){
      const eventId=`imported-${index}`;await db.runAsync(insert,eventId,'document','{}',`idb:${eventId}`,filename,mime,'2026-10-04');
      await db.runAsync('INSERT INTO queue_state',eventId,1,null,'only BRL is supported',1);
    }
    const rows=await db.getAllAsync('SELECT * FROM event_queue');let uploaded=0;
    const recovered=await reconcileImportedFiles(db,rows,async()=>{const current=rows[uploaded++];return{imported:true,event_id:current.event_id,sha256:'ab'.repeat(32),batch_id:'33333333-3333-4333-8333-333333333333'};},()=>{});
    assert.equal(recovered.length,4);assert.equal(uploaded,4);assert.equal((await db.getAllAsync('SELECT * FROM event_queue')).length,0);
  });
  await check('Rerouting clears its old failure without erasing a concurrent new batch failure',async()=>{
    await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)','reroute-state','document','{}','idb:original','statement.pdf','application/pdf','2026-10-04');
    await db.runAsync('INSERT INTO queue_state','reroute-state',1,null,'only BRL is supported',1);
    const original={...(await db.getAllAsync('SELECT * FROM event_queue'))[0],last_error:'only BRL is supported'};
    const racingDb={getAllAsync:db.getAllAsync.bind(db),runAsync:async(sql,...args)=>{
      await db.runAsync(sql,...args);
      if(sql.startsWith('UPDATE event_queue SET kind=')){const batch=(await db.getAllAsync('SELECT * FROM event_queue'))[0];await persistQueueFailure(db,batch,'Synthetic batch parsing failed',2,'2026-10-05',true);}
    }};
    await requeueAsStatement(racingDb,original);
    const quarantined=await db.getAllAsync('SELECT e.*, s.attempt_count FROM event_queue e JOIN queue_state s ON s.event_id=e.event_id WHERE s.quarantined=1 ORDER BY e.id');
    assert.equal(quarantined.length,1);assert.equal(quarantined[0].kind,'batch');assert.equal(quarantined[0].attempt_count,2);
  });
  await check('A stale retry callback cannot clear a replacement or rerouted failure',async()=>{
    const insert='INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)';
    await db.runAsync(insert,'retry-cas','document','{"old":true}','idb:old','statement.pdf','application/pdf','2026-10-04');
    const old=(await db.getAllAsync('SELECT * FROM event_queue'))[0];
    await db.runAsync('INSERT INTO queue_state',old.event_id,1,null,'Old failure',1);
    await clearQueueFailure(db,old);assert.equal((await db.getAllAsync('SELECT * FROM queue_state')).length,0,'Matching retry clears its own failure');
    await db.runAsync('INSERT INTO queue_state',old.event_id,2,null,'Changed-route failure',1);
    await db.runAsync("UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document'",old.event_id);
    await clearQueueFailure(db,old);assert.equal((await db.getAllAsync('SELECT * FROM queue_state'))[0].attempt_count,2,'Old purpose cannot clear batch metadata');
    await db.runAsync('DELETE FROM event_queue WHERE event_id=?',old.event_id);
    await db.runAsync(insert,old.event_id,'batch','{"new":true}','idb:new','statement.pdf','application/pdf','2026-10-04');
    const replacement=(await db.getAllAsync('SELECT * FROM event_queue'))[0];assert.notEqual(replacement.id,old.id);
    await db.runAsync('INSERT INTO queue_state',old.event_id,4,null,'Replacement failure',1);
    await clearQueueFailure(db,old);assert.equal((await db.getAllAsync('SELECT * FROM queue_state'))[0].attempt_count,4);
    await clearQueueFailure(db,replacement);assert.equal((await db.getAllAsync('SELECT * FROM queue_state')).length,0,'Current replacement can be explicitly retried');
  });
  if(failures.length)process.exitCode=1;
  console.log(`${15-failures.length}/15 queue adversarial transitions passed; only synthetic memory/storage/transport used.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
