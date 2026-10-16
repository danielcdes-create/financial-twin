const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const React=require('react'),{act,create}=require('react-test-renderer'),{DatabaseSync}=require('node:sqlite');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),storage=new Map();let quota=false;
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,
 {module:m,exports:m.exports,console,Date,Intl,Error,Promise,Map,WeakMap,Set,navigator:{locks:{request:async(_key,work)=>work()}},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>{if(quota)throw Error('quota');storage.set(key,value);}},require:name=>{
  if(name==='react')return React;if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:file});cache.set(file,m.exports);return m.exports;}
const {manualCapturePayload,emptyManualCapture}=load('utils/manualCapture.ts');
const {useManualDraft,flushManualDraft,deleteManualDraft}=load('utils/manualDraft.ts');
const draft={...emptyManualCapture(),merchant:"McDonald's",amount:'32,90',date:'09/10/2026',account:'santander',payment:'pix'};
for(const [input,value] of [['32,90',32.9],['32.90',32.9],['1.234,56',1234.56],['1.234',1234],['0,01',.01]])assert.equal(manualCapturePayload({...draft,amount:input},x=>x).amount,value);
for(const input of ['0','-32,90','1e3','0,001','32,999','1,2,3','1.23.4','Infinity','100000000,01'])assert.throws(()=>manualCapturePayload({...draft,amount:input},x=>x));
assert.throws(()=>manualCapturePayload({...draft,date:'31/02/2026'},x=>x));
const payload=manualCapturePayload(draft,x=>x);assert.equal(payload.account_profile_id,'santander');assert.equal(payload.payment_method,'pix');assert.equal(payload.event_timestamp,'2026-10-09T00:00:00-03:00');
assert.equal(manualCapturePayload({...draft,kind:'income'},x=>x).direction,'income');
const transfer=manualCapturePayload({...draft,kind:'own_transfer',origin:'itau',destination:'santander'},x=>x);assert.equal(transfer.from_account_key,'itau');assert.equal(transfer.account_profile_id,undefined);assert.equal(transfer.payment_method,undefined);
const parcelado=manualCapturePayload({...draft,installments:'4'},x=>x);assert.equal(parcelado.installment_count,4);assert.equal(parcelado.amount,32.9);
assert.equal(manualCapturePayload({...draft,installments:'1'},x=>x).installment_count,undefined);
assert.throws(()=>manualCapturePayload({...draft,installments:'49'},x=>x));
const web=load('utils/queueDb.web.tsx').webQueueDb;
const sqlite=new DatabaseSync(':memory:');sqlite.exec('CREATE TABLE batch_drafts(batch_id TEXT PRIMARY KEY,state TEXT,updated_at TEXT)');
const sqlDb={getAllAsync:async(sql,...args)=>sqlite.prepare(sql).all(...args),runAsync:async(sql,...args)=>sqlite.prepare(sql).run(...args)};
async function test(db,label){let hook,renderer;const errors=[];function Probe(){hook=useManualDraft(db,message=>errors.push(message));return null;}
 const mount=async()=>act(async()=>{renderer=create(React.createElement(Probe));await new Promise(resolve=>setTimeout(resolve,10));});
 const unmount=async()=>act(async()=>renderer.unmount());
 await mount();assert.equal(hook.ready,true);
 await act(async()=>{for(const [key,value] of Object.entries(draft))hook.change(key,value);await flushManualDraft(db);});
 await unmount();await mount();assert.equal(hook.draft.merchant,"McDonald's");assert.equal(hook.draft.account,'santander');
 let sealed;await act(async()=>{sealed=await hook.prepare({event_id:'same-event',parsed_payload:payload});});
 await unmount();await mount();assert.equal((await hook.prepare({event_id:'other'})).event_id,'same-event','Restart reuses sealed command after uncertain local insert/cleanup');
 const previous=(await db.getAllAsync('SELECT * FROM batch_drafts WHERE batch_id=?','manual-capture'))[0];
 await act(async()=>{hook.change('amount','33,90');await flushManualDraft(db);});
 await assert.rejects(deleteManualDraft(db,previous.state),/mudou/);
 assert.equal(hook.draft.amount,'33,90');
 await act(async()=>hook.clear());assert.equal(hook.draft.merchant,'');assert.equal((await db.getAllAsync('SELECT * FROM batch_drafts')).length,0);
 await unmount();await mount();assert.equal(hook.draft.amount,'');await unmount();console.log('PASS durable manual draft, sealed identity, conditional deletion and restart:',label);
}
(async()=>{await test(web,'web');await test(sqlDb,'SQLite');
 let hook,r;const errors=[];function Probe(){hook=useManualDraft(web,x=>errors.push(x));return null;}
 await act(async()=>{r=create(React.createElement(Probe));await new Promise(resolve=>setTimeout(resolve,10));});quota=true;
 await act(async()=>{hook.change('merchant','Preserved input');await flushManualDraft(web).catch(()=>{});});assert.equal(hook.draft.merchant,'Preserved input');assert.ok(errors.length);
 await assert.rejects(hook.prepare({event_id:'never-enqueued'}));quota=false;await act(async()=>r.unmount());sqlite.close();console.log('PASS validation and storage failure preserve input and prevent enqueue');
})().catch(error=>{console.error(error);process.exitCode=1;});
