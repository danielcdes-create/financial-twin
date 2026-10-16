const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
const modules=new Map(), storage=new Map();let quota=false;
function load(file){
 if(modules.has(file))return modules.get(file);
 const module={exports:{}}; modules.set(file,module.exports);
 const sandbox={module,exports:module.exports,require:name=>name==='react'?{createContext:()=>({})}:name==='./captureDate'?load('utils/captureDate.ts'):name==='./moneyInput'?load('utils/moneyInput.ts'):require(name),
 console,Map,JSON,Math,Number,String,Boolean,Error,Date,Promise,navigator:{locks:{request:async(_key,fn)=>fn()}},
 localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>{if(quota)throw Error('quota');storage.set(key,value)}}};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText,sandbox);
 return module.exports;
}
(async()=>{
 const batch=load('utils/statementBatch.ts');
 assert.equal(batch.signedBatchAmount('-R$ 1.873,14'),'-1873.14');
 for(const value of ['','12,345','1e3','NaN','0','-100000001'])assert.equal(batch.signedBatchAmount(value),null);
 assert.equal(batch.batchDate('29/02/2026'),null);assert.equal(batch.batchDate('30/09/2026'),'2026-09-30');
 const source=[{row_id:1,date:'30/09/2026',amount:'-12,59',description:'Padaria',kind:'expense',bank_ref:null,selected:false,confirmed_distinct:false}];
 const body=batch.batchBody('profile:itau',source,true);
 assert.equal(body.rows[0].amount,'-12.59');assert.equal(body.rows[0].selected,false);assert.equal(source[0].amount,'-12,59');
 const unknown=[{...source[0],currency:null}],foreign=[{...source[0],currency:'USD'}];
 assert.equal(batch.batchBody('profile:itau',unknown,false).acknowledge_brl,false);
 assert.equal(batch.batchBody('profile:itau',unknown,false,true).acknowledge_brl,true);
 assert.equal(batch.batchBody('profile:itau',unknown,false,true).rows[0].currency,null,'Acknowledgement preserves unknown source currency');
 assert.equal(batch.batchBody('profile:itau',foreign,false,true).rows[0].currency,'USD','Acknowledgement cannot erase foreign source evidence');
 const db=load('utils/queueDb.web.tsx').webQueueDb;
 await db.runAsync('INSERT INTO review_drafts','old-review','{}','preserved','2026-10-01');
 const save='INSERT INTO batch_drafts(batch_id,state,updated_at) VALUES(?,?,?)';
 await db.runAsync(save,'batch-1',JSON.stringify(body),'2026-10-01');
 assert.equal((await db.getAllAsync('SELECT * FROM batch_drafts WHERE batch_id=?','batch-1'))[0].state,JSON.stringify(body));
 assert.equal((await db.getAllAsync('SELECT * FROM review_drafts'))[0].draft,'preserved');
 quota=true;await assert.rejects(db.runAsync(save,'batch-1','lost','2026-10-01'));quota=false;
 assert.equal((await db.getAllAsync('SELECT * FROM batch_drafts'))[0].state,JSON.stringify(body));
 await db.runAsync('DELETE FROM batch_drafts WHERE batch_id=?','batch-1');
 assert.equal((await db.getAllAsync('SELECT * FROM review_drafts')).length,1);
 storage.set('financial-twin-batch-drafts','corrupt');await assert.rejects(db.getAllAsync('SELECT * FROM batch_drafts'));assert.equal(storage.get('financial-twin-batch-drafts'),'corrupt');
 const sync=load('utils/syncQueue.ts');let removed=0,attempts=0;
 const row={id:1,event_id:'batch-1',kind:'batch',payload:'{}',local_uri:'file:///persisted.png',filename:'print.png',mime_type:'image/png'};
 const run=sync.createQueueSync({prepare:async()=>true,list:async()=>removed?[]:[row],send:async()=>++attempts===1?{persisted:true,event_id:'batch-1'}:{persisted:true,draft_persisted:true,event_id:'batch-1'},remove:async()=>{removed++},recordFailure:async()=>{},done:()=>{},changed:()=>{}});
 await run();assert.equal(removed,0,'An ACK without a retained draft must keep the queued file');
 await run();assert.equal(removed,1);
 assert.throws(()=>sync.requirePersistedAck(row,{persisted:true,draft_persisted:true,event_id:'another-batch'}));
 console.log('Batch capture: signed cents, civil dates, retained edits, isolated drafts, quota/corruption, draft ACK and retry — passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
