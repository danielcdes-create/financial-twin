// Production local draft store and manager; isolated web storage and real SQLite.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const React=require('react'),{act,create}=require('react-test-renderer'),{DatabaseSync}=require('node:sqlite');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),state=new Map(),dialogs=[];let quotaFailure=false,dbForComponent;
const native={StyleSheet:{create:x=>x},...Object.fromEntries(['Text','View','Pressable','TextInput','ActivityIndicator'].map(name=>[name,name]))};
const mocks={'react':React,'react-native':native,'./durableFiles':{persistQueuedFile:async uri=>uri},'../utils/appAlert':{Alert:{alert:(...args)=>dialogs.push(args)}},'../utils/queueDb':{useSQLiteContext:()=>dbForComponent}};
const localStorage={getItem:key=>state.get(key)||null,setItem:(key,value)=>{if(quotaFailure)throw Error('Synthetic quota failure');state.set(key,value)}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
    {module:m,exports:m.exports,console,Error,Date,Intl,Math,Promise,WeakMap,Map,Symbol,setTimeout,clearTimeout,localStorage,navigator:{locks:{request:async(_key,work)=>work()}},require:name=>{
      if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');
    }},{filename:file});cache.set(file,m.exports);return m.exports;
}
const utility=load('utils/localDrafts.ts'),web=load('utils/queueDb.web.tsx').webQueueDb,Manager=load('components/DraftManager.tsx').default;
const deferred=()=>{let resolve;const promise=new Promise(yes=>{resolve=yes});return{promise,resolve}};
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,5))});
const schema='CREATE TABLE review_drafts(provisional_id TEXT PRIMARY KEY,item TEXT,draft TEXT,updated_at TEXT);CREATE TABLE batch_drafts(batch_id TEXT PRIMARY KEY,state TEXT,updated_at TEXT);CREATE TABLE preserved_drafts(draft_id TEXT PRIMARY KEY,scope TEXT,item_id TEXT,state TEXT,updated_at TEXT);CREATE TABLE voice_drafts(draft_key TEXT PRIMARY KEY,state TEXT,updated_at TEXT);';
const seed=async db=>{
  await db.runAsync('INSERT INTO review_drafts(provisional_id,item,draft,updated_at) VALUES(?,?,?,?)','capture','{"vendedor":"Fonte"}','{"vendedor":"Minha edição"}','2026-10-06');
  await db.runAsync('INSERT INTO batch_drafts(batch_id,state,updated_at) VALUES(?,?,?)','batch','{"rows":[{}]}','2026-10-06');
  await db.runAsync('INSERT OR IGNORE INTO preserved_drafts(draft_id,scope,item_id,state,updated_at) VALUES(?,?,?,?,?)','copy-a','review','capture','{"vendedor":"Antigo"}','2026-10-05');
  await db.runAsync('INSERT OR IGNORE INTO preserved_drafts(draft_id,scope,item_id,state,updated_at) VALUES(?,?,?,?,?)','copy-b','review','capture','{"vendedor":"Outra cópia"}','2026-10-05');
  await db.runAsync('INSERT INTO voice_drafts(draft_key,state,updated_at) VALUES(?,?,?)','capture','{"clip":null,"text":"Voz","hasTranscript":true,"appliedContext":null}','2026-10-06');
};
async function storeChecks(db,label){
  await seed(db);
  const all=await utility.listLocalDrafts(db);assert.equal(all.length,5);
  for(const kind of ['review','batch','preserved','voice']){
    const draft=all.find(row=>row.kind===kind);await utility.deleteLocalDraft(db,draft);assert.ok(!(await utility.listLocalDrafts(db)).some(row=>row.kind===kind&&row.id===draft.id),label+' removes '+kind);
  }
  assert.equal((await utility.listLocalDrafts(db))[0].id,'copy-b',label+' exact preserved identity retains another copy of the same item');
  await db.runAsync('INSERT INTO batch_drafts(batch_id,state,updated_at) VALUES(?,?,?)','commitment-create','{"creation_id":"pending"}','2026-10-06');
  const pending=(await utility.listLocalDrafts(db)).find(row=>row.pendingCreation);assert.ok(pending);await assert.rejects(utility.deleteLocalDraft(db,pending),/já foi enviado/);
  await db.runAsync('INSERT INTO review_drafts(provisional_id,item,draft,updated_at) VALUES(?,?,?,?)','capture','{}','{"vendedor":"Antes"}','2026-10-06');
  const original=(await utility.listLocalDrafts(db)).find(row=>row.kind==='review');
  await db.runAsync('DELETE FROM review_drafts WHERE provisional_id=?','capture');
  await db.runAsync('INSERT INTO review_drafts(provisional_id,item,draft,updated_at) VALUES(?,?,?,?)','capture','{}','{"vendedor":"Depois"}','2026-10-07');
  await assert.rejects(utility.deleteLocalDraft(db,original),/mudou/);assert.equal((await db.getAllAsync('SELECT * FROM review_drafts WHERE provisional_id=?','capture'))[0].draft,'{"vendedor":"Depois"}');
  const latest=(await utility.listLocalDrafts(db)).find(row=>row.kind==='review'),write=deferred();utility.trackDraftWrite(db,'review','capture',write.promise);
  const newerMount=Promise.resolve();utility.trackDraftWrite(db,'review','capture',newerMount);await newerMount;
  let done=false;const removal=utility.deleteLocalDraft(db,latest).then(()=>{done=true});await new Promise(resolve=>setTimeout(resolve,5));assert.equal(done,false);write.resolve();await removal;assert.equal(done,true);
  console.log('PASS',label,'all kinds, exact identity, pending creation guard, stale snapshot, autosaves across separate mounts');
}
(async()=>{
  await storeChecks(web,'web');
  state.clear();const sqlite=new DatabaseSync(':memory:');sqlite.exec(schema);
  const nativeDb={getAllAsync:async(sql,...params)=>sqlite.prepare(sql).all(...params),runAsync:async(sql,...params)=>sqlite.prepare(sql).run(...params)};
  await storeChecks(nativeDb,'SQLite');sqlite.close();
  // A replacement written between the read and DELETE is protected by SQL itself.
  state.clear();await seed(web);const snapshot=(await utility.listLocalDrafts(web)).find(row=>row.kind==='review');
  await web.runAsync('INSERT INTO review_drafts','capture','{}','{"vendedor":"Nova"}','2026-10-07');
  await web.runAsync('DELETE FROM review_drafts WHERE provisional_id=? AND item=? AND draft=? AND updated_at=?',snapshot.id,snapshot.row.item,snapshot.row.draft,snapshot.row.updated_at);
  assert.equal((await web.getAllAsync('SELECT * FROM review_drafts'))[0].draft,'{"vendedor":"Nova"}');
  dbForComponent=web;let renderer;await act(async()=>{renderer=create(React.createElement(Manager,{onBack(){},onPendingCreation(){}}))});await settle();
  const button=label=>renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(text=>text.props.children===label));
  const press=label=>act(async()=>{const node=button(label);assert.ok(node,label);return node.props.onPress()});
  await press('Excluir rascunho: Revisão de captura');assert.equal((await utility.listLocalDrafts(web)).length,5,'opening confirmation does not delete');
  const confirm=dialogs.at(-1)[2].find(option=>option.style==='destructive');quotaFailure=true;await act(async()=>confirm.onPress());quotaFailure=false;await settle();
  assert.equal((await utility.listLocalDrafts(web)).length,5,'failed delete retains saved content');assert.ok(button('Excluir rascunho: Revisão de captura'));
  await press('Excluir rascunho: Revisão de captura');const stale=dialogs.at(-1)[2].find(option=>option.style==='destructive').onPress;
  await act(async()=>renderer.unmount());await stale();assert.equal((await utility.listLocalDrafts(web)).length,5,'retired dialog callback cannot delete');
  await act(async()=>{renderer=create(React.createElement(Manager,{onBack(){},onPendingCreation(){}}))});await settle();await press('Excluir rascunho: Revisão de captura');await act(async()=>dialogs.at(-1)[2].find(option=>option.style==='destructive').onPress());await settle();
  assert.equal((await utility.listLocalDrafts(web)).length,4);assert.equal(button('Excluir rascunho: Revisão de captura'),undefined);await act(async()=>renderer.unmount());
  console.log('PASS manager confirmation, failure recovery, retired callback, persisted deletion');
})().catch(error=>{console.error(error);process.exitCode=1});
