// Real review components, interaction gate and Activity dialogs; synthetic HTTP/storage.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),reviews=new Map(),batches=new Map(),preserved=new Map(),requests=[],events=[];
let renderer,holdWrite=null,failDelete=false;
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return{promise,resolve};};
const db={getAllAsync:async(sql,id)=>{
  const table=sql.includes('review_commands')?new Map():sql.includes('review_drafts')?reviews:sql.includes('batch_drafts')?batches:preserved;
  return id?(table.has(id)?[table.get(id)]:[]):[...table.values()].sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at))).slice(0,sql.includes('LIMIT 1')?1:undefined);
},runAsync:async(sql,...args)=>{
  const table=sql.includes('review_drafts')?reviews:sql.includes('batch_drafts')?batches:preserved;
  if(sql.startsWith('DELETE')){events.push('delete:'+args[0]);if(failDelete)throw Error('Synthetic deletion failure');table.delete(args[0]);return;}
  if(holdWrite){const pending=holdWrite;holdWrite=null;events.push('write-held');await pending.promise;}
  events.push('write:'+args[0]);
  if(table===reviews){const [provisional_id,item,draft,updated_at]=args;table.set(provisional_id,{provisional_id,item,draft,updated_at});}
  else if(table===batches){const [batch_id,state,updated_at]=args;table.set(batch_id,{batch_id,state,updated_at});}
  else{const [draft_id,scope,item_id,state,updated_at]=args;table.set(draft_id,{draft_id,scope,item_id,state,updated_at});}
}};
const native={Platform:{OS:'android'},Alert:{alert(){throw Error('Native dialog must not be used');}},StyleSheet:{create:x=>x},
  Keyboard:{dismiss(){}},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},BackHandler:{addEventListener:()=>({remove(){}})},
  FlatList:({data,renderItem,ListEmptyComponent})=>React.createElement('View',null,data.length?data.map(item=>React.createElement(React.Fragment,{key:item.value},renderItem({item}))):ListEmptyComponent),
  ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator','ScrollView'].map(name=>[name,name]))};
const mocks={'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'View'},'../utils/queueDb':{useSQLiteContext:()=>db},
  './voiceDraft':{deleteVoiceDraft:async()=>{},flushVoiceDraft:async()=>{}}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const module={exports:{}};
  const source=process.env.REVIEW_DRAFT_ORIGINAL?path.join(process.env.REVIEW_DRAFT_ORIGINAL,path.relative(process.cwd(),file)):file;
  const actual=fs.existsSync(source)?source:file;
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(actual,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
    {module,exports:module.exports,console,Date,Intl,Math,JSON,Promise,Error,WeakMap,Map,Set,Symbol,Object,URLSearchParams,AbortController,setTimeout,clearTimeout,
      require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const next=path.resolve(path.dirname(file),name);return load(fs.existsSync(next+'.tsx')?next+'.tsx':next+'.ts');}},{filename:actual});
  cache.set(file,module.exports);return module.exports;}
const Provisional=load('components/ProvisionalReview.tsx').default,Batch=load('components/StatementBatchReview.tsx').default;
const Provider=load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider,Choice=load('components/ControlledChoice.tsx').default;
const alerts=load('utils/appAlert.ts'),operations=load('utils/userOperation.ts');
const wrap=element=>React.createElement(Provider,null,element);
const mount=element=>act(async()=>{renderer=create(wrap(element),{createNodeMock:()=>({})});});
const update=element=>act(async()=>renderer.update(wrap(element)));
const settle=(ms=20)=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
const text=()=>renderer.root.findAllByType('Text').map(node=>[node.props.children].flat(Infinity).filter(x=>typeof x==='string'||typeof x==='number').join('')).join('\n');
const findButton=label=>renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(child=>child.props.children===label));
const button=label=>{const node=findButton(label);assert.ok(node,'Button: '+label);return node;};
const press=label=>act(async()=>{const node=button(label);assert.ok(!node.props.disabled,label+' enabled');node.props.onPress();});
const field=label=>renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel===label);
const input=(label,value)=>act(async()=>{assert.ok(field(label));field(label).props.onChangeText(value);});
const confirm=label=>act(async()=>{const dialog=alerts.dialogSnapshot();assert.ok(dialog);assert.equal(dialog.options.inActivity,true);const option=dialog.buttons.find(item=>item.text===label);assert.ok(option);alerts.dismissDialog(option,dialog);});
const dispose=async()=>{if(renderer){await act(async()=>renderer.unmount());renderer=null;}await act(async()=>alerts.clearDialogs());};
const source={provisional_id:'capture',revision:1,status:'provisional',merchant_raw:'Synthetic purchase',vendedor:'Source',valor:-2,data_evento:'2026-10-01T12:00:00Z',fluxo:'saida',natureza:'COMPRA',categoria_sugerida:'Casa'};
const options={natureza:['COMPRA'],fluxo:['saida'],categoria:['Casa'],metodo_pagamento:[],categoria_subcategorias:{Casa:[]}};
const captureApi=(current=source)=>async(route,init={})=>{requests.push({route,method:init.method||'GET'});if(init.method)throw Error('Unexpected server write');if(route.endsWith('/classification-options'))return options;if(route==='/v1/account-positions')return{items:[]};if(route.includes('?'))return{items:[current],total:1};return{item:current};};
const captureProps=(api=captureApi())=>({api,initialItem:source,money:String,onUpdated:async()=>{}});
const seedReview=(item=source,value='Saved edit')=>reviews.set(item.provisional_id,{provisional_id:item.provisional_id,item:JSON.stringify(item),draft:JSON.stringify({vendedor:value,_classification_intent_version:'2'}),updated_at:'2026-10-06T12:00:00Z'});
const extracted={batch_id:'batch',filename:'synthetic.pdf',status:'draft',sha256:'synthetic',complete:true,rows:[{row_id:1,date:'2026-10-01',amount:'-2',description:'Source row',kind:'expense',currency:'BRL'}]};
const batchApi=(current=extracted,preview)=>async(route,init={})=>{requests.push({route,method:init.method||'GET'});if(route.endsWith('/preview'))return preview?preview.promise:{rows:[],new_count:0,duplicate_count:0,possible_duplicate_count:0,scan_complete:true};if(init.method)throw Error('Unexpected server write');if(route.includes('?'))return{items:[current],has_more:false};return current;};
const batchProps=(api=batchApi())=>({api,visible:true,revision:0,accounts:[{id:'account',tipo:'conta_corrente',instituicao:'Synthetic'}],profiles:[],onUpdated:async()=>{}});
const cases=[],test=(name,work)=>cases.push({name,work});
test('A capture draft can be cancelled, deleted and restarted without recreating it on remount',async()=>{
  const props=captureProps(captureApi({...source,revision:2,vendedor:'Latest source'}));reviews.set('other',{provisional_id:'other',item:'{}',draft:'{}',updated_at:'older'});
  await mount(React.createElement(Provisional,props));await settle();assert.equal(reviews.has('capture'),false,'Opening an unedited source does not create a draft');
  await input('Vendedor','My edit');await settle();assert.equal(JSON.parse(reviews.get('capture').draft).vendedor,'My edit');
  await press('Excluir rascunho desta revisão');await confirm('Cancelar');assert.equal(reviews.has('capture'),true);
  await press('Excluir rascunho desta revisão');await confirm('Excluir rascunho');await settle();assert.equal(field('Vendedor').props.value,'Latest source');assert.equal(reviews.has('capture'),false);assert.equal(reviews.has('other'),true);
  assert.doesNotMatch(text(),/Esta captura mudou/);assert.equal(findButton('Excluir rascunho desta revisão'),undefined);assert.ok(requests.every(item=>item.method==='GET'));
  await dispose();await mount(React.createElement(Provisional,{...props,initialItem:{...source,revision:2,vendedor:'Latest source'}}));await settle();assert.equal(reviews.has('capture'),false,'Deleted draft remains absent after restart');
  await input('Observações','New edit');await settle();assert.equal(JSON.parse(reviews.get('capture').draft).detalhe,'New edit');
});
test('Conflict deletion removes local corrections and uses current capture without preserving another copy',async()=>{
  seedReview();const fresh={...source,revision:2,vendedor:'Remote'};await mount(React.createElement(Provisional,{...captureProps(captureApi(fresh)),initialItem:fresh}));await settle();assert.match(text(),/Esta captura mudou/);assert.equal(field('Vendedor').props.value,'Saved edit');
  await press('Excluir rascunho desta revisão');await confirm('Excluir rascunho');await settle();assert.equal(field('Vendedor').props.value,'Remote');assert.equal(reviews.has('capture'),false);assert.equal(preserved.size,0);assert.doesNotMatch(text(),/Esta captura mudou/);
});
test('The interrupted review card can delete its draft without changing the source capture',async()=>{
  seedReview();await mount(React.createElement(Provisional,{api:captureApi(),money:String,onUpdated:async()=>{}}));await settle();assert.match(text(),/Revisão interrompida/);
  await press('Excluir rascunho da revisão interrompida');await confirm('Excluir rascunho');await settle();assert.equal(reviews.size,0);assert.doesNotMatch(text(),/Revisão interrompida/);assert.ok(button('Revisar e editar'));assert.equal(source.vendedor,'Source');
});
test('Capture deletion waits for a pending autosave and discards edits and repeated callbacks while busy',async()=>{
  await mount(React.createElement(Provisional,captureProps()));await settle();const pending=deferred();holdWrite=pending;await input('Vendedor','Slow saved edit');await settle();assert.ok(events.includes('write-held'));
  await press('Excluir rascunho desta revisão');await settle();assert.equal(operations.isUserOperationBusy(),true);assert.equal(alerts.dialogSnapshot(),null,'Confirmation waits until its autosave snapshot is durable');assert.ok(!events.includes('delete:capture'));
  await input('Vendedor','Ignored edit');assert.equal(field('Vendedor').props.value,'Slow saved edit');await act(async()=>pending.resolve());await settle();const callback=alerts.dialogSnapshot().buttons.find(item=>item.text==='Excluir rascunho').onPress;
  await confirm('Excluir rascunho');await act(async()=>callback());await settle();assert.equal(reviews.has('capture'),false);assert.equal(field('Vendedor').props.value,'Source');assert.equal(events.filter(item=>item==='delete:capture').length,1);assert.equal(operations.isUserOperationBusy(),false);
});
test('A stale capture confirmation cannot delete a draft after changing editor owner',async()=>{
  seedReview();const props=captureProps();await mount(React.createElement(Provisional,props));await settle();await press('Excluir rascunho desta revisão');const other={...source,provisional_id:'other',vendedor:'Other source'};
  await update(React.createElement(Provisional,{...props,initialItem:other,api:captureApi(other)}));await settle();await confirm('Excluir rascunho');await settle();assert.equal(reviews.has('capture'),true);assert.equal(field('Vendedor').props.value,'Other source');assert.ok(!events.includes('delete:capture'));
});
test('A failed local deletion retains the capture draft and editor values',async()=>{
  seedReview();await mount(React.createElement(Provisional,captureProps()));await settle();failDelete=true;await press('Excluir rascunho desta revisão');await confirm('Excluir rascunho');await settle();assert.equal(reviews.has('capture'),true);assert.equal(field('Vendedor').props.value,'Saved edit');assert.equal(alerts.dialogSnapshot().title,'Não foi possível excluir o rascunho');assert.equal(operations.isUserOperationBusy(),false);
});
test('A batch draft resets to its latest extraction and stays absent through close, reopen and remount',async()=>{
  const props=batchProps();await mount(React.createElement(Batch,props));await settle();await press('Revisar synthetic.pdf');await settle();assert.equal(batches.size,0);
  await input('Descrição da linha 1','Local row');await settle();assert.equal(JSON.parse(batches.get('batch').state).rows[0].description,'Local row');batches.set('other',{batch_id:'other',state:'{}',updated_at:'older'});
  await press('Excluir rascunho deste lote');await confirm('Cancelar');assert.equal(batches.has('batch'),true);
  await press('Excluir rascunho deste lote');await confirm('Excluir rascunho');await settle();assert.equal(field('Descrição da linha 1').props.value,'Source row');assert.equal(batches.has('batch'),false);assert.equal(batches.has('other'),true);assert.ok(requests.every(item=>item.method==='GET'));
  await press('Fechar e retomar depois');await press('Revisar synthetic.pdf');await settle();assert.equal(batches.has('batch'),false);await dispose();await mount(React.createElement(Batch,props));await settle();await press('Revisar synthetic.pdf');await settle();assert.equal(batches.has('batch'),false);
  await input('Descrição da linha 1','New draft');await settle();assert.equal(JSON.parse(batches.get('batch').state).rows[0].description,'New draft');
});
test('Batch conflict deletion resets rows, account and acknowledgements without a preserved copy',async()=>{
  batches.set('batch',{batch_id:'batch',state:JSON.stringify({source:'old-source',rows:[{...extracted.rows[0],description:'Old draft'}],account:'ledger:account',partial:true,brl:true}),updated_at:'today'});
  const current={...extracted,rows:[{...extracted.rows[0],description:'Latest row'}]};await mount(React.createElement(Batch,batchProps(batchApi(current))));await settle();await press('Revisar synthetic.pdf');await settle();assert.match(text(),/outra versão da extração/);
  await press('Excluir rascunho deste lote');await confirm('Excluir rascunho');await settle();assert.equal(field('Descrição da linha 1').props.value,'Latest row');assert.equal(renderer.root.findAllByType(Choice).find(node=>node.props.label==='Conta ou cartão do extrato').props.value,'');assert.equal(batches.has('batch'),false);assert.equal(preserved.size,0);assert.doesNotMatch(text(),/outra versão da extração/);
});
test('Batch deletion waits for its autosave, rejects stale comparison and does not replay a second deletion',async()=>{
  const comparison=deferred();await mount(React.createElement(Batch,batchProps(batchApi(extracted,comparison))));await settle();await press('Revisar synthetic.pdf');await settle();await press('Selecionar Conta ou cartão do extrato');await press('Escolher Synthetic · Conta');await settle(480);
  const pending=deferred();holdWrite=pending;await input('Descrição da linha 1','Delayed row');await settle();await press('Excluir rascunho deste lote');await settle();assert.equal(operations.isUserOperationBusy(),true);assert.equal(alerts.dialogSnapshot(),null);assert.ok(!events.includes('delete:batch'));
  await input('Descrição da linha 1','Ignored');assert.equal(field('Descrição da linha 1').props.value,'Delayed row');await act(async()=>pending.resolve());await settle();const callback=alerts.dialogSnapshot().buttons.find(item=>item.text==='Excluir rascunho').onPress;await confirm('Excluir rascunho');await act(async()=>callback());
  await act(async()=>comparison.resolve({rows:[],new_count:999,duplicate_count:0,possible_duplicate_count:0}));await settle();assert.doesNotMatch(text(),/999 novas/);
  assert.equal(batches.has('batch'),false);assert.equal(events.filter(item=>item==='delete:batch').length,1);assert.equal(field('Descrição da linha 1').props.value,'Source row');
});
test('Hidden batch owners reject old confirmation and failed deletion keeps the draft',async()=>{
  const props=batchProps();await mount(React.createElement(Batch,props));await settle();await press('Revisar synthetic.pdf');await input('Descrição da linha 1','Keep row');await settle();await press('Excluir rascunho deste lote');await update(React.createElement(Batch,{...props,visible:false}));await confirm('Excluir rascunho');await settle();assert.equal(batches.has('batch'),true);assert.ok(!events.includes('delete:batch'));
  await update(React.createElement(Batch,props));await settle();failDelete=true;await press('Excluir rascunho deste lote');await confirm('Excluir rascunho');await settle();assert.equal(batches.has('batch'),true);assert.equal(field('Descrição da linha 1').props.value,'Keep row');assert.match(text(),/Não foi possível excluir o rascunho/);assert.equal(operations.isUserOperationBusy(),false);
});
test('A newer capture draft from another writer survives an already-open deletion confirmation',async()=>{
  seedReview();await mount(React.createElement(Provisional,captureProps()));await settle();await press('Excluir rascunho desta revisão');const newer={...reviews.get('capture'),draft:JSON.stringify({vendedor:'Concurrent edit'}),updated_at:'newer'};reviews.set('capture',newer);
  await confirm('Excluir rascunho');await settle();assert.equal(reviews.get('capture'),newer);assert.equal(field('Vendedor').props.value,'Saved edit');assert.match(alerts.dialogSnapshot().message,/O rascunho mudou/);assert.ok(!events.includes('delete:capture'));
});
test('A newer batch draft survives confirmation without being overwritten by the stale editor',async()=>{
  await mount(React.createElement(Batch,batchProps()));await settle();await press('Revisar synthetic.pdf');await input('Descrição da linha 1','Local edit');await settle();await press('Excluir rascunho deste lote');const newer={...batches.get('batch'),state:JSON.stringify({rows:[{description:'Concurrent edit'}]}),updated_at:'newer'};batches.set('batch',newer);
  await confirm('Excluir rascunho');await settle();assert.equal(batches.get('batch'),newer);assert.equal(field('Descrição da linha 1').props.value,'Local edit');assert.match(text(),/O rascunho mudou/);assert.ok(!events.includes('delete:batch'));
});
test('Offline capture deletion removes only local edits and requires a fresh source before saving',async()=>{
  seedReview();const base=captureApi(),api=(route,init)=>route==='/v1/provisional/capture'?Promise.reject(Error('Synthetic offline')):base(route,init);
  await mount(React.createElement(Provisional,captureProps(api)));await settle();await press('Excluir rascunho desta revisão');await confirm('Excluir rascunho');await settle();assert.equal(reviews.has('capture'),false);assert.equal(field('Vendedor').props.value,'Source');assert.ok(button('Carregar captura atual e conferir'));assert.equal(button('Salvar correções').props.disabled,true);assert.doesNotMatch(text(),/Suas edições permanecem no rascunho/);
});
test('Offline batch deletion removes the local draft and permits opening its source later',async()=>{
  let offline=false;const base=batchApi(),api=(route,init)=>offline&&route==='/v1/capture-batches/batch'?Promise.reject(Error('Synthetic offline')):base(route,init);
  const props=batchProps(api);await mount(React.createElement(Batch,props));await settle();await press('Revisar synthetic.pdf');await input('Descrição da linha 1','Offline edit');await settle();offline=true;
  await press('Excluir rascunho deste lote');await confirm('Excluir rascunho');await settle();assert.equal(batches.has('batch'),false);assert.equal(field('Descrição da linha 1'),undefined);assert.ok(button('Revisar synthetic.pdf'));assert.match(text(),/Reabra o lote/);
  offline=false;await press('Revisar synthetic.pdf');await settle();assert.equal(field('Descrição da linha 1').props.value,'Source row');assert.equal(batches.has('batch'),false);
});
(async()=>{let failed=0;for(const {name,work} of cases){reviews.clear();batches.clear();preserved.clear();requests.length=0;events.length=0;failDelete=false;holdWrite=null;try{await work();console.log('PASS',name);}catch(error){failed++;console.error('FAIL',name,error.stack);}finally{if(holdWrite)holdWrite.resolve();await dispose();await settle();}}
  console.log(`${cases.length-failed}/${cases.length} review draft deletion regressions passed`);if(failed)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
