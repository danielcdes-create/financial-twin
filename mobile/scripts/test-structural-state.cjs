// Controlled transport + real components. Synthetic data only; no real API/database.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),failures=[],dialogs=[];let checks=0;
const native={Platform:{OS:'android'},StyleSheet:{create:value=>value},useWindowDimensions:()=>({width:390,height:844,fontScale:1}),
  AppState:{currentState:'active',addEventListener:()=>({remove(){}})},BackHandler:{addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},
  ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator'].map(name=>[name,name])),
  FlatList:({data,renderItem})=>React.createElement('View',null,data.map(item=>React.createElement(React.Fragment,{key:item.value},renderItem({item}))))};
const mocks={react:React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'SafeAreaView'},
  '../utils/appAlert':{Alert:{alert:(...args)=>dialogs.push(args)}},'../utils/queueDb':{useSQLiteContext:()=>({getAllAsync:async()=>[],runAsync:async()=>{}})}};
function load(file){file=path.resolve(process.cwd(),file);if(cache.has(file))return cache.get(file);
  const original=process.env.STRUCTURAL_ORIGINAL,candidate=original&&path.join(original,path.relative(process.cwd(),file));
  const source=candidate&&fs.existsSync(candidate)?candidate:file,module={exports:{}};
  const code=ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  vm.runInNewContext(code,{module,exports:module.exports,console,fetch,Headers,FormData,URLSearchParams,AbortController,setTimeout,clearTimeout,Date,
    require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:source});
  cache.set(file,module.exports);return module.exports;}
const {createApiClient}=load('utils/apiClient.ts'),{rebaseDraft}=load('utils/draftConflict.ts');
const Transaction=load('components/TransactionEditor.tsx').default,Accounts=load('components/AccountPositions.tsx').default;
const Month=load('components/MonthClose.tsx').default,Pending=load('components/PendingMutations.tsx').default;
const Future=load('components/FutureCommitmentEditor.tsx').default,Delete=load('components/DeleteMovement.tsx').default;
const {retainCreation,acknowledgeCreation,pendingCreation}=load('utils/commitmentCreate.ts');
const {ChoiceOverlayProvider}=load('components/ChoiceOverlay.tsx');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return{promise,resolve,reject}};
const reply=body=>new Response(JSON.stringify(body),{status:200}),settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,15))});
let renderer;
const mount=element=>act(async()=>{renderer=create(React.createElement(ChoiceOverlayProvider,null,element))});
const text=()=>renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join('')).join('\n');
const button=label=>{const found=renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(child=>child.props.children===label));assert.ok(found,label);return found};
const press=label=>act(async()=>{const node=button(label);assert.ok(!node.props.disabled,`${label}: enabled`);node.props.onPress()});
async function check(name,work){checks++;try{await work();console.log('PASS',name)}catch(error){failures.push(name);console.error('FAIL',name,error.stack)}finally{if(renderer){await act(async()=>renderer.unmount());renderer=null}}}
function dbFixture(){const rows=new Map();return{rows,failReceipt:false,async getAllAsync(_sql,...params){return params.length?[rows.get(String(params[0]))].filter(Boolean):[...rows.values()]},async runAsync(_sql,...params){if(this.failReceipt&&params[3])throw new Error('disk full');rows.set(params[0],{command_key:params[0],path:params[1],body:params[2],result:params[3],updated_at:params[4]})}}}
(async()=>{
 await check('GET before write cannot satisfy refresh or publish stale data',async()=>{
   const old=deferred();let calls=0;
   const api=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[],fetch:async(_url,init)=>init.method==='PATCH'?reply({saved:true}):++calls===1?old.promise:reply({balance:20})});
   const previous=api.json('/v1/account-positions').catch(error=>error.name);
   await api.json('/v1/account-profiles/synthetic',{method:'PATCH',body:'{}'});
   const current=api.json('/v1/account-positions');old.resolve(reply({balance:10}));
   assert.equal((await current).balance,20);assert.equal(await previous,'AbortError');assert.equal(calls,2);
 });
 await check('Read-only POST preview preserves current reads and focus contract',async()=>{
   const held=deferred();const api=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[],fetch:async(_url,init)=>init.method==='POST'?reply({preview:true}):held.promise});
   const current=api.json('/v1/accounts');await api.json('/v1/capture-batches/synthetic/preview',{method:'POST',body:'{}'});held.resolve(reply({items:[]}));assert.ok(await current);
 });
 await check('Options invalidated by an unrelated write recover without remounting editor',async()=>{
   const old=deferred();let calls=0;
   const api=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[],fetch:async(_url,init)=>init.method==='PATCH'?reply({saved:true}):++calls===1?old.promise:reply({categoria:['Current']})});
   const options=api.json('/v1/transactions/classification-options');await api.json('/v1/card-catalog/synthetic',{method:'PATCH',body:'{}'});old.resolve(reply({categoria:['Old']}));
   assert.equal((await options).categoria[0],'Current');
 });
 await check('Two simultaneous confirmed writes do not cancel each other',async()=>{
   const held=deferred();let calls=0;const api=createApiClient({baseUrl:'http://fixture',token:()=>'',fetch:async()=>++calls===1?held.promise:reply({persisted:true})});
   const first=api.json('/one',{method:'PATCH',body:'{}'}),second=api.json('/two',{method:'PATCH',body:'{}'});await second;held.resolve(reply({persisted:true}));assert.equal((await first).persisted,true);
 });
 await check('Session generation discards a late response even with reused token',async()=>{
   let generation=1;const held=deferred(),api=createApiClient({baseUrl:'http://fixture',token:()=> 'same',sessionIdentity:()=>generation,readRetryDelaysMs:[],fetch:async()=>held.promise});
   const old=api.json('/v1/accounts');generation++;held.resolve(reply({items:['private']}));await assert.rejects(old,error=>error.name==='AbortError');
 });
 await check('Lost response survives restart and replays same operation identity',async()=>{
   const db=dbFixture(),bodies=[];let attempts=0;
   const fetcher=async(_url,init)=>{const body=JSON.parse(init.body);bodies.push(body);if(++attempts===1)throw new Error('response lost after commit');return reply({persisted:true,request_id:body.request_id,item:{id:'synthetic'}})};
   const settings={baseUrl:'http://fixture',token:()=>'',fetch:fetcher};let api=createApiClient(settings);api.configureMutationStore(db);
   await assert.rejects(api.json('/v1/transactions/synthetic/review',{method:'PATCH',body:'{"status_revisao":"validado","expected_token":"source-v1"}'}));
   api=createApiClient(settings);api.configureMutationStore(db);const pending=await api.pendingMutations();assert.equal(pending.length,1);await api.resumeMutation(pending[0]);
   assert.equal(bodies[0].request_id,bodies[1].request_id);assert.equal((await api.pendingMutations()).length,0);
 });
 await check('Failed local receipt keeps confirmed success and finalizes without repeating network',async()=>{
   const db=dbFixture();db.failReceipt=true;let calls=0;
   const api=createApiClient({baseUrl:'http://fixture',token:()=>'',fetch:async(_url,init)=>{calls++;return reply({persisted:true,request_id:JSON.parse(init.body).request_id})}});api.configureMutationStore(db);
   const response=await api.json('/v1/card-catalog/synthetic',{method:'PATCH',body:'{"apelido":"Novo","expected_token":"old"}'});assert.equal(response.persisted,true);assert.equal(response.local_finalization_pending,true);
   db.failReceipt=false;await api.resumeMutation((await api.pendingMutations())[0]);assert.equal(calls,1);assert.equal((await api.pendingMutations()).length,0);
 });
 await check('Wrong ACK and immutable request ID cannot overwrite a preserved attempt',async()=>{
   const db=dbFixture(),api=createApiClient({baseUrl:'http://fixture',token:()=>'',fetch:async()=>reply({persisted:true,request_id:'other'})});api.configureMutationStore(db);
   const body={request_id:'11111111-1111-4111-8111-111111111111',apelido:'First',expected_token:'old'};
   await assert.rejects(api.json('/v1/card-catalog/synthetic',{method:'PATCH',body:JSON.stringify(body)}));const original=JSON.stringify([...db.rows.values()]);
   await assert.rejects(api.json('/v1/card-catalog/synthetic',{method:'PATCH',body:JSON.stringify({...body,apelido:'Second'})}));assert.equal(JSON.stringify([...db.rows.values()]),original);
 });
 await check('In-progress concurrent operation keeps original journal and can replay',async()=>{
   const db=dbFixture();let calls=0;
   const api=createApiClient({baseUrl:'http://fixture',token:()=>'',fetch:async(_url,init)=>++calls===1?new Response('{"detail":"operation in progress"}',{status:409,headers:{'X-Operation-Pending':'1'}}):reply({persisted:true,request_id:JSON.parse(init.body).request_id})});api.configureMutationStore(db);
   await assert.rejects(api.json('/v1/card-catalog/synthetic',{method:'PATCH',body:'{"apelido":"same","expected_token":"old"}'}),error=>error.retryable);
   const pending=await api.pendingMutations();assert.equal(pending.length,1);await api.resumeMutation(pending[0]);assert.equal((await api.pendingMutations()).length,0);
 });
 await check('Three-way draft rebase updates untouched fields and marks overlapping edits',async()=>{
   const safe=rebaseDraft({supplier:'A',category:'old'},{supplier:'A',category:'current'},{supplier:'typed',category:'old'});assert.equal(safe.draft.category,'current');assert.equal(safe.draft.supplier,'typed');assert.equal(safe.conflicts.length,0);
   assert.equal(rebaseDraft({supplier:'A'},{supplier:'remote'},{supplier:'typed'}).conflicts[0],'supplier');
 });
 await check('Transaction conflict rebase preserves typed vendor without reverting new category',async()=>{
   const writes=[];let saved=0;
   const api=async(route,init)=>{if(route.endsWith('classification-options'))return{categoria:['Old','Current']};if(route.endsWith('match-preview'))return{total:0};if(init?.method){writes.push(JSON.parse(init.body));if(writes.length===1)throw Object.assign(new Error('stale'),{status:409});return{item:{id:'synthetic',vendedor:'Typed',categoria:'Current',source_token:'v3'}}}return{id:'synthetic',vendedor:'A',categoria:'Current',valor:-10,source_token:'v2'}};
   await mount(React.createElement(Transaction,{api,item:{id:'synthetic',vendedor:'A',categoria:'Old',valor:-10,source_token:'v1'},money:String,onSaved:()=>{saved++},onCancel(){}}));await settle();
   await act(async()=>renderer.root.findByProps({accessibilityLabel:'Vendedor'}).props.onChangeText('Typed'));await press('Salvar sem validar');await press('Carregar registro atual e conferir');await press('Salvar sem validar');
   assert.equal(writes[1].vendedor,'Typed');assert.equal(writes[1].categoria,undefined);assert.equal(writes[1].expected_token,'v2');assert.equal(saved,1);
 });
 await check('Confirmed transaction save reports refresh failure as saved',async()=>{
   dialogs.length=0;const api=async(route,init)=>init?.method?{item:{id:'synthetic',source_token:'v2'}}:route.endsWith('classification-options')?{}:{total:0};
   await mount(React.createElement(Transaction,{api,item:{id:'synthetic',vendedor:'A',source_token:'v1'},money:String,onSaved:()=>{throw new Error('refresh failed')},onCancel(){}}));await settle();await press('Salvar e validar');assert.equal(dialogs.at(-1)[0],'Alteração salva');
 });
 await check('Month-close balance draft keeps initial revision after refreshing current data',async()=>{
   const writes=[];let revision=1;const group={id:'synthetic',kind:'account',title:'Conta',detail:'',completed:0,total:0,items:[],balance:{opening:'0',closing:'0',flow:'0',expected:'0',difference:'0',cash_account:true}};
   const api=async(_route,init)=>{if(init?.method){writes.push(JSON.parse(init.body));throw Object.assign(new Error('stale'),{status:409})}return{month:'2026-09',start:'2026-09-01',end:'2026-09-30',revision:revision++,status:'open',groups:[group],completed:0,total:0,progress:0,totals:{income:'0',expenses:'0'},attestation:'',month_ended:true}};
   await mount(React.createElement(Month,{api,money:String,onBack(){},onNavigate(){}}));await settle();await press('Checklist de Conta');await press('Informar saldos de Conta');await press('Atualizar dados');await press('Salvar saldos do mês');assert.equal(writes[0].expected_revision,1);assert.ok(text().includes('Seu rascunho usa uma versão anterior'));
 });
 await check('Account initial response cannot overwrite a newer explicit reload',async()=>{
   const first=deferred();let calls=0;const api=async(_route,init)=>init?.method?{key:'created'}:++calls===1?first.promise:{items:[{key:'synthetic',instituicao:'Current',tipo:'conta_corrente',status:'aberta',balance:20,movement_count:0}]};
   await mount(React.createElement(Accounts,{api,money:String}));
   await press('Adicionar conta');await act(async()=>renderer.root.findByProps({accessibilityLabel:'Instituição'}).props.onChangeText('New account'));await press('Salvar cadastro');
   await act(async()=>first.resolve({items:[{key:'old',instituicao:'Old snapshot',tipo:'conta_corrente',status:'aberta',balance:10,movement_count:0}]}));
   assert.ok(text().includes('Current'));assert.ok(!text().includes('Old snapshot'));
 });
 await check('Pending mutation event surfaces immediately without a sync revision',async()=>{
   let listener,pending=[];const client={pendingMutations:async()=>pending,subscribeMutations:callback=>{listener=callback;return()=>{}},resumeMutation:async()=>{pending=[];listener()}};
   await mount(React.createElement(Pending,{client,onUpdated:async()=>{},revision:0,enabled:true}));await settle();assert.equal(text(),'');
   await act(async()=>{pending=[{command_key:'mutation:synthetic',path:'/fixture',body:'{}'}];listener()});assert.ok(text().includes('gravação(ões) sem confirmação local'));await press('Concluir tentativas pendentes');assert.equal(text(),'');
 });
 await check('Future schedule conflict keeps edited values visible until explicit reload',async()=>{
   const api=async(_route,init)=>{if(init?.method)throw Object.assign(new Error('source changed'),{status:409});return{editable_count:1,preview_token:'v1',rows:[{row_id:1,number:1,amount:'10.00',due_date:'2027-01-01',editable:true}]}};
   await mount(React.createElement(Future,{source:'installment',identity:'synthetic',name:'Synthetic schedule',api,onUpdated:async()=>{}}));await press('Editar futuras de Synthetic schedule');
   await act(async()=>renderer.root.findByProps({accessibilityLabel:'Valor da parcela 1'}).props.onChangeText('12,00'));await press('Revisar alterações');await press('Confirmar alterações');
   assert.equal(renderer.root.findByProps({accessibilityLabel:'Valor da parcela 1'}).props.value,'12,00');assert.equal(button('Revisar alterações').props.disabled,true);assert.ok(button('Carregar cronograma atual e conferir rascunho'));
 });
 await check('Confirmed deletion exposes refresh recovery without offering another delete',async()=>{
   const api=async(_route,init)=>init?.method?{deleted:true}:{can_delete:true,preview_token:'v1',blockers:[]};
   await mount(React.createElement(Delete,{item:{id:'synthetic'},api,onDeleted:async()=>{throw new Error('refresh failed')}}));await press('Excluir registro');await press('Confirmar exclusão');
   assert.ok(text().includes('Registro excluído. Não foi possível atualizar a tela: refresh failed'));assert.ok(button('Atualizar tela'));
   await press('Atualizar tela');assert.ok(text().includes('Registro excluído. Não foi possível atualizar a tela: refresh failed'));
   assert.ok(!renderer.root.findAllByType('Text').some(node=>node.props.children==='Confirmar exclusão'));
 });
 await check('Commitment receipt preserves confirmed creation across failed local cleanup',async()=>{
   let stored=null,failReceipt=false;const db={getAllAsync:async()=>stored?[{state:stored}]:[],runAsync:async(sql,...params)=>{if(sql.startsWith('DELETE'))throw new Error('cleanup failed');if(failReceipt)throw new Error('storage full');stored=params[1]}};
   const body={creation_id:'11111111-1111-4111-8111-111111111111',name:'Synthetic'};await retainCreation(db,body);
   assert.equal(await acknowledgeCreation(db,body,{persisted:true,creation_id:body.creation_id,commitment_id:'synthetic'}),true);assert.equal(await pendingCreation(db),null);
   await retainCreation(db,body);failReceipt=true;assert.equal(await acknowledgeCreation(db,body,{persisted:true,creation_id:body.creation_id,commitment_id:'synthetic'}),false);assert.equal((await pendingCreation(db)).creation_id,body.creation_id);
 });
 if(failures.length)throw new Error(`${failures.length} structural regressions failed: ${failures.join('; ')}`);console.log(`${checks} structural state regressions passed; transport/storage/component fixtures only.`);
})().catch(error=>{console.error(error);process.exitCode=1});
