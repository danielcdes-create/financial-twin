const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
function load(file,extra={}){const box={exports:{},require,console,Map,Set,JSON,Math,Number,String,Boolean,Error,Promise,Date,Intl,setTimeout,clearTimeout,...extra};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText,box);return box.exports;}
(async()=>{
 const state=new Map(),db=load('utils/queueDb.web.tsx',{navigator:{locks:{request:async(_name,fn)=>fn()}},localStorage:{getItem:key=>state.get(key)||null,setItem:(key,value)=>state.set(key,value)}}).webQueueDb;
 const {createQueueSync}=load('utils/syncQueue.ts');
 const insert='INSERT OR IGNORE INTO event_queue (event_id,kind,payload,created_at) VALUES (?,?,?,?)',list=()=>db.getAllAsync('SELECT e.* FROM event_queue');
 await db.runAsync(insert,'A','event','{}','2026-10-02');
 let release,listed;const held=new Promise(r=>release=r),ready=new Promise(r=>listed=r);const sent=[];
 const common={prepare:async()=>true,list,remove:row=>db.runAsync('DELETE FROM event_queue WHERE event_id=?',row.event_id),recordFailure:async()=>{},done:()=>{},changed:()=>{}};
 const tab2=createQueueSync({...common,send:async row=>{sent.push(row.event_id);listed();await held;return {persisted:true,event_id:row.event_id}}});
 const late=tab2();await ready;
 await createQueueSync({...common,send:async row=>({persisted:true,event_id:row.event_id})})();
 assert.equal((await list()).length,0);
 await db.runAsync(insert,'B','event','{}','2026-10-02');const b=(await list())[0];assert.ok(b.id>1);
 release();await late;assert.equal((await list()).length,1);assert.equal((await list())[0].event_id,'B');
 // A wrong ACK and an obsolete id-only delete cannot remove the current event.
 await createQueueSync({...common,send:async()=>({persisted:true,event_id:'another-event'})})();assert.equal((await list())[0].event_id,'B');
 await db.runAsync('DELETE FROM event_queue WHERE id=?',b.id);assert.equal((await list())[0].event_id,'B');
 const {loadCursorItems}=load('utils/loadCursorItems.ts');
 const rows=Array.from({length:51},(_,batch_id)=>({batch_id})),calls=[];
 const all=await loadCursorItems(async path=>{calls.push(path);return path.includes('cursor=')?{items:rows.slice(50),has_more:false,total:51}:{items:rows.slice(0,50),has_more:true,next_cursor:'continuation',total:51}},'/v1/capture-batches?revision=1');
 assert.equal(all.length,51);assert.ok(calls[1].includes('&cursor=continuation'));
 await assert.rejects(loadCursorItems(async()=>({items:[],has_more:true,next_cursor:'repeat'}),'/drafts'));
 const {loadCommitmentLists}=load('utils/commitmentLists.ts');
 const plans=Array.from({length:201},(_,id)=>({id}));
 const loaded=await loadCommitmentLists(async path=>path.startsWith('/v1/installments')?(path.includes('offset=')?{items:plans.slice(200),has_more:false}:{items:plans.slice(0,200),has_more:true,next_offset:200,total:201}):{items:[]});
 assert.equal(loaded.lists.installments.length,201);assert.equal(loaded.errors.length,0);
 const {reviewDecisionBody,reviewHasDifferences}=load('utils/reviewDecision.ts');
 assert.throws(()=>reviewDecisionBody({},'confirm_match'));
 const review={review_token:'review-observed',source_token:'source-observed',candidate_token:'candidate-observed',source:{valor:-10,data_evento:'2026-09-20T03:00:00Z'},candidate:{valor:-100,data_evento:'2026-09-20T03:00:00Z'}};
 assert.equal(reviewHasDifferences(review),true);const body=reviewDecisionBody(review,'confirm_match',true);assert.equal(body.candidate_token,'candidate-observed');assert.equal(body.acknowledge_difference,true);
 console.log('C01/C08/C09/C04: two-tab ACK conserves B; ACK identity enforced; obsolete deletes rejected; 51 drafts and 201 plans reachable; review tokens and acknowledgement retained.');
})().catch(error=>{console.error(error);process.exitCode=1});
