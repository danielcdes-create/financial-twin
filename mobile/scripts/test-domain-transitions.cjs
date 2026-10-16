// Domain components and real controls/command journal, isolated HTTP/storage.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),dialogs=[],drafts=new Map(),commands=new Map(),voiceDrafts=new Map(),preserved=new Map(),cleanups=[];
let renderer,voiceReadFailure=false,captureImage=async()=>'',sharedImages=0;
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});cleanups.push(()=>resolve());return{promise,resolve,reject};};
const db={getAllAsync:async(sql,id)=>{
  const rows=sql.includes('review_commands')?commands:sql.includes('review_drafts')?drafts:new Map();
  return id?(rows.has(id)?[rows.get(id)]:[]):[...rows.values()];
},runAsync:async(sql,...args)=>{
  if(sql.includes('review_commands')){const [key,route,body,result,updated_at]=args;commands.set(key,{command_key:key,path:route,body,result,updated_at});}
  if(sql.includes('review_drafts')){const [id,item,draft,updated_at]=args;if(sql.startsWith('DELETE'))drafts.delete(id);else drafts.set(id,{provisional_id:id,item,draft,updated_at});}
  if(sql.includes('preserved_drafts')){const [id,scope,item_id,state,updated_at]=args;preserved.set(id,{scope,item_id,state,updated_at});}
}};
const recorder={prepareToRecordAsync:async()=>{},stop:async()=>{},record(){},uri:'synthetic:voice'};
const native={useWindowDimensions:()=>({width:390,height:800,fontScale:1}),Platform:{OS:'android'},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},BackHandler:{addEventListener:()=>({remove(){}})},StyleSheet:{create:x=>x},
  FlatList:({data,renderItem,ListEmptyComponent})=>React.createElement('View',null,data.length?data.map(item=>React.createElement(React.Fragment,{key:item.value},renderItem({item}))):ListEmptyComponent),
  ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator','ScrollView','Image'].map(name=>[name,name]))};
const alert={alert:(...args)=>dialogs.push(args)};
const mocks={'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'View'},'../utils/queueDb':{useSQLiteContext:()=>db},'../utils/appAlert':{Alert:alert,ActivityAlert:alert},
  'expo-audio':{AudioModule:{requestRecordingPermissionsAsync:async()=>({granted:true})},RecordingPresets:{HIGH_QUALITY:{}},setAudioModeAsync:async()=>{},useAudioRecorder:()=>recorder,useAudioRecorderState:()=>({durationMillis:0})},
  'expo-document-picker':{},'../utils/voiceFile':{voiceFormData:async()=>new FormData()},'../utils/devicePrompt':{withDevicePrompt:task=>task()},
  'expo-image-picker':{},'expo-sharing':{isAvailableAsync:async()=>true,shareAsync:async()=>{sharedImages++;}},'react-native-view-shot':{captureRef:(...args)=>captureImage(...args)},'../utils/purchaseImage':{purchaseImageFormData:async()=>new FormData()},
  '../utils/voiceDraft':{loadVoiceDraft:async(_db,key)=>{if(voiceReadFailure)throw Error('Synthetic voice read failure');return voiceDrafts.get(key)||null;},saveVoiceDraft:async(_db,key,state)=>{voiceDrafts.set(key,{...state});},preserveVoiceDraft:async()=>{},durableVoiceClip:async clip=>clip,voiceDraftGeneration:()=>0,subscribeVoiceDraftChanges:()=>()=>{},deleteVoiceDraft:async(_db,key)=>{voiceDrafts.delete(key);},flushVoiceDraft:async()=>{}}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const module={exports:{}};
  const baseline=process.env.DOMAIN_TRANSITIONS_ORIGINAL&&path.join(process.env.DOMAIN_TRANSITIONS_ORIGINAL,path.relative(process.cwd(),file));
  const source=baseline&&fs.existsSync(baseline)?baseline:file;
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
    {module,exports:module.exports,console,Date,Intl,Math,JSON,Promise,Error,URLSearchParams,AbortController,FormData,setTimeout,clearTimeout,
      require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const next=path.resolve(path.dirname(file),name);return load(fs.existsSync(next+'.tsx')?next+'.tsx':next+'.ts');}},{filename:source});
  cache.set(file,module.exports);return module.exports;}
const Batch=load('components/StatementBatchReview.tsx').default,Provisional=load('components/ProvisionalReview.tsx').default,Voice=load('components/VoiceCapture.tsx').default,Transaction=load('components/TransactionEditor.tsx').default;
const Category=load('components/CategoryManager.tsx').default,Subcategory=load('components/SubcategoryManager.tsx').default,Accounts=load('components/AccountPositions.tsx').default,Commitments=load('components/CommitmentManager.tsx').default,Month=load('components/MonthClose.tsx').default;
const Purchase=load('components/PurchaseCheck.tsx').default;
const Projection=load('components/CommitmentProjection.tsx').default;
const Provider=load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider;
const Choice=load('components/ControlledChoice.tsx').default;
const Schedule=load('components/ScheduleOverlay.tsx').ScheduleOverlayProvider;
const wrap=element=>React.createElement(Provider,null,React.createElement(Schedule,null,element));
const mount=element=>act(async()=>{renderer=create(wrap(element),{createNodeMock:()=>({})});});
const update=element=>act(async()=>renderer.update(wrap(element)));
const settle=(ms=20)=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
const text=()=>renderer.root.findAllByType('Text').map(node=>[node.props.children].flat(Infinity).filter(x=>typeof x==='string'||typeof x==='number').join('')).join('\n');
const findButton=label=>renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(child=>child.props.children===label));
const button=label=>{const result=findButton(label);assert.ok(result,'Button: '+label);return result;};
const press=label=>act(async()=>{const node=button(label);assert.ok(!node.props.disabled,label+' enabled');node.props.onPress();});
const field=label=>renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel===label);
const input=(label,value)=>act(async()=>{assert.ok(field(label),'Field: '+label);field(label).props.onChangeText(value);});
const choiceValue=label=>renderer.root.findAllByType(Choice).find(node=>node.props.label===label).props.value;
const clearChoice=async label=>{await press(`Selecionar ${label}`);await press(`Limpar seleção de ${label}`);};
const cases=[],test=(name,work)=>cases.push({name,work});
test('Cancelling a batch comparison releases its indicator and retains the row draft',async()=>{
  const read=deferred();let previews=0;
  const api=async(route)=>{if(route.includes('?'))return{items:[{batch_id:'batch',filename:'synthetic.pdf'}],has_more:false};if(route.endsWith('/preview')){previews++;return read.promise;}return{batch_id:'batch',filename:'synthetic.pdf',status:'draft',sha256:'synthetic',complete:true,rows:[{row_id:1,date:'2026-10-01',amount:'-2',description:'Row',kind:'expense'}]};};
  const props={api,visible:true,revision:0,accounts:[{id:'account',tipo:'conta_corrente',instituicao:'Synthetic'}],profiles:[],onUpdated(){}};
  await mount(React.createElement(Batch,props));await settle();await press('Revisar synthetic.pdf');await settle();await press('Selecionar Conta ou cartão do extrato');await press('Escolher Synthetic · Conta');await settle(480);
  assert.equal(previews,1);assert.match(text(),/Comparando com as movimentações/);
  await update(React.createElement(Batch,{...props,visible:false}));await settle();assert.doesNotMatch(text(),/Comparando com as movimentações/);
  assert.equal(field('Descrição da linha 1').props.value,'Row');
  await act(async()=>read.resolve({rows:[],new_count:999,duplicate_count:0,possible_duplicate_count:0}));assert.doesNotMatch(text(),/999 novas/);
});
const oldCapture={provisional_id:'capture',revision:1,merchant_raw:'Synthetic purchase',vendedor:'Original',plataforma:'Original platform',valor:-2,data_evento:'2026-10-01T12:00:00Z',fluxo:'saida',natureza:'COMPRA',tipo:'compra',categoria_sugerida:'Casa',subcategoria:'Itens'};
const options={natureza:['COMPRA'],fluxo:['saida'],categoria:['Casa','Lazer'],categoria_subcategorias:{Casa:['Itens'],Lazer:[]},metodo_pagamento:[]};
test('A provisional 409 preserves the draft and obtains explicit acceptance before using the new revision',async()=>{
  let writes=0,lastBody;const fresh={...oldCapture,revision:2,vendedor:'Remote',plataforma:'Remote platform'};
  const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return options;if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){writes++;lastBody=JSON.parse(init.body);if(writes===1)throw Object.assign(new Error('Source changed'),{status:409});assert.equal(lastBody.expected_revision,2);return{persisted:true,provisional_id:'capture',request_id:lastBody.request_id,item:{...fresh,...lastBody}};}if(route==='/v1/provisional/capture')return{item:fresh};throw Error(route);};
  await mount(React.createElement(Provisional,{api,initialItem:oldCapture,money:String,onUpdated:async()=>{}}));await settle();await input('Vendedor','My draft');await press('Salvar correções');await settle();
  assert.equal(field('Vendedor').props.value,'My draft');assert.equal(button('Salvar correções').props.disabled,true,'No blind retry against an old revision');
  await press('Carregar captura atual e conferir');await settle();assert.match(text(),/Remote/);assert.match(text(),/My draft/);
  await press('Conferi a mudança: recuperar minhas correções');await settle();assert.equal(field('Vendedor').props.value,'My draft');assert.equal(field('Plataforma').props.value,'Remote platform','Untouched fields use current source');
  await press('Salvar correções');await settle();assert.equal(writes,2);assert.equal(lastBody.vendedor,'My draft');assert.ok(!('plataforma' in lastBody),'Untouched current fields are preserved without being rewritten');
});
test('Restoring a different voice draft key never copies the previous transcript into an empty draft',async()=>{
  voiceDrafts.set('first',{clip:null,text:'First private transcript',hasTranscript:true,appliedContext:null});
  const props={api:async()=>({draft:{}}),upload:async()=>({text:'unused'}),context:'movement',onApply(){}};
  await mount(React.createElement(Voice,{...props,draftKey:'first'}));await settle();assert.equal(field('Transcrição da voz').props.value,'First private transcript');
  await update(React.createElement(Voice,{...props,draftKey:'second'}));await settle();assert.ok(!field('Transcrição da voz'),'Empty owner must show an empty voice editor');assert.equal(voiceDrafts.get('second').text,'');
});
test('An interpretation response cannot apply a transcript to a changed voice owner',async()=>{
  const interpretation=deferred();let applied=0;
  voiceDrafts.set('first',{clip:null,text:'First transcript',hasTranscript:true,appliedContext:null});
  const props={api:()=>interpretation.promise,upload:async()=>({text:'unused'}),context:'movement',onApply(){applied++;}};
  await mount(React.createElement(Voice,{...props,draftKey:'first'}));await settle();await press('Preencher campos com a voz');
  await update(React.createElement(Voice,{...props,draftKey:'second',context:'purchase'}));await settle();await act(async()=>interpretation.resolve({draft:{price:'999'}}));await settle();
  assert.equal(applied,0,'Disposed owner callback must never run');assert.doesNotMatch(text(),/Campos preenchidos/);assert.equal(button('Gravar voz').props.disabled,false);
});
test('A failed voice-draft restore has an explicit retry that unlocks the existing editor',async()=>{
  voiceReadFailure=true;
  try{
    await mount(React.createElement(Voice,{draftKey:'voice-recovery',context:'movement',api:async()=>({draft:{}}),upload:async()=>({text:''}),onApply(){}}));await settle();assert.equal(button('Gravar voz').props.disabled,true);
    voiceReadFailure=false;await press('Recuperar rascunho de voz');await settle();assert.equal(button('Gravar voz').props.disabled,false);assert.doesNotMatch(text(),/Synthetic voice read failure/);
  }finally{voiceReadFailure=false;}
});
test('Stopping voice recording persists its original-owner clip after disposal without a second native stop',async()=>{
  const stopped=deferred();let stops=0;const previous=recorder.stop;recorder.stop=()=>{stops++;return stopped.promise;};
  try{
    await mount(React.createElement(Voice,{draftKey:'stopped-voice',context:'movement',api:async()=>({}),upload:async()=>({}),onApply(){}}));await settle();await press('Gravar voz');await settle();await press('Parar gravação');
    await act(async()=>renderer.unmount());renderer=null;await act(async()=>stopped.resolve());await settle();assert.equal(stops,1);assert.equal(voiceDrafts.get('stopped-voice').clip.uri,recorder.uri);
  }finally{recorder.stop=previous;}
});
test('Classification patches explicitly clear the optional values visible as empty in the real editor',async()=>{
  const writes=[];const item={id:'transaction',source_token:'token',vendedor:'Original',plataforma:'Platform',categoria:'Casa',subcategoria:'Itens',valor:-2,detalhe:'Old note'};
  const api=async(route,init={})=>{if(init.method==='PATCH'){writes.push(JSON.parse(init.body));return{item:{...item,...writes.at(-1)}};}if(route.endsWith('/classification-options'))return options;return{total:0};};
  await mount(React.createElement(Transaction,{item,api,money:String,onSaved(){},onCancel(){}}));await settle();await input('Plataforma','');await input('Observações','');await press('Selecionar Categoria');await press('Escolher Lazer');await press('Salvar sem validar');await settle();
  assert.equal(writes.length,1);assert.equal(writes[0].plataforma,'');assert.equal(writes[0].detalhe,'');assert.equal(writes[0].categoria,'Lazer');assert.equal(writes[0].subcategoria,'','Changing parent category removes the old child');
});
const inferredNatureOptions={natureza:['ESTORNO','RENDIMENTO'],natureza_labels:{ESTORNO:'Estorno'},fluxo:['entrada'],categoria:['Viagem'],categoria_subcategorias:{Viagem:[]},metodo_pagamento:[],vendedor:['Reembolso Amigo Viagem'],localidade:['São Paulo']};
const unclassifiedIncome={valor:100,fluxo:'entrada',tipo:'receita',natureza_sugerida:'ESTORNO',descricao_original:'REEMBOLSO AMIGO VIAGEM',data_evento:'2026-10-01T01:30:00Z'};
test('A ledger note patch leaves inferred economic nature separate from persisted classification',async()=>{
  const writes=[],item={...unclassifiedIncome,id:'inferred-ledger',source_token:'source',categoria:'Viagem'};
  const api=async(route,init={})=>{if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);return{item:{...item,...body}};}if(route.endsWith('/classification-options'))return inferredNatureOptions;return{total:0};};
  await mount(React.createElement(Transaction,{item,api,money:String,onSaved(){},onCancel(){}}));await settle();
  assert.equal(choiceValue('Natureza'),'','An inferred value is not a draft selection');assert.match(text(),/Sugestão: Estorno/);
  await input('Observações','A note only');await press('Salvar sem validar');await settle();
  assert.equal(writes.length,1);assert.deepEqual(writes[0],{detalhe:'A note only',expected_token:'source'});assert.equal(choiceValue('Natureza'),'');
});
test('Validating an unclassified ledger note never accepts its inferred economic nature',async()=>{
  const writes=[],item={...unclassifiedIncome,id:'validated-note',source_token:'source',categoria:'Viagem'};
  const api=async(route,init={})=>{if(init.method==='PATCH'){writes.push({route,body:JSON.parse(init.body)});return{item:{...item,...writes.at(-1).body,status_revisao:'validado'}};}if(route.endsWith('/classification-options'))return inferredNatureOptions;return{total:0};};
  await mount(React.createElement(Transaction,{item,api,money:String,onSaved(){},onCancel(){}}));await settle();await input('Observações','Validated note');await press('Salvar e validar');await settle();
  assert.equal(writes.length,1);assert.deepEqual(writes[0].body,{detalhe:'Validated note',validate_review:true,expected_token:'source'});
});
test('Review-only validation of an unclassified ledger leaves the suggested nature unaccepted',async()=>{
  const writes=[],item={...unclassifiedIncome,id:'review-only',source_token:'source',categoria:'Viagem'};
  const api=async(route,init={})=>{if(init.method==='PATCH'){writes.push({route,body:JSON.parse(init.body)});return{item:{...item,status_revisao:'validado'}};}if(route.endsWith('/classification-options'))return inferredNatureOptions;return{total:0};};
  await mount(React.createElement(Transaction,{item,api,money:String,onSaved(){},onCancel(){}}));await settle();await press('Salvar e validar');await settle();
  assert.equal(writes.length,1);assert.equal(writes[0].route,'/v1/transactions/review-only/review');assert.deepEqual(writes[0].body,{status_revisao:'validado',expected_token:'source'});
});
test('A confirmed capture note sends only explicit edits and never accepts source suggestions',async()=>{
  const writes=[],item={...unclassifiedIncome,provisional_id:'inferred-capture',revision:1,status:'confirmed',status_revisao:'validado',categoria_sugerida:'Viagem',merchant_raw:'REEMBOLSO AMIGO VIAGEM',localidade:'Sao Paulo',plataforma_sugerida:'iFood'};
  const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return inferredNatureOptions;if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);return{persisted:true,provisional_id:item.provisional_id,request_id:body.request_id,item:{...item,...body}};}throw Error(route);};
  await mount(React.createElement(Provisional,{initialItem:item,api,money:String,reviewRecord:true,onUpdated:async()=>{}}));await settle();
  assert.equal(choiceValue('Natureza'),'');assert.match(text(),/Sugestão: Estorno/);assert.equal(field('Origem').props.value,'','Matching source text does not fill a persisted vendor');assert.equal(field('Localidade').props.value,'Sao Paulo','Opening does not rewrite a persisted locality');
  await input('Observações','Only this note');await press('Salvar correções');await settle();assert.equal(writes.length,1);
  const {request_id,...body}=writes[0];assert.ok(request_id);assert.deepEqual(body,{detalhe:'Only this note',validate_review:true,expected_revision:1});
});
test('Choosing the suggested nature explicitly includes it in a ledger patch',async()=>{
  const writes=[],item={...unclassifiedIncome,id:'explicit-ledger',source_token:'source',categoria:'Viagem'};
  const api=async(route,init={})=>{if(init.method==='PATCH'){writes.push(JSON.parse(init.body));return{item:{...item,...writes.at(-1)}};}if(route.endsWith('/classification-options'))return inferredNatureOptions;return{total:0};};
  await mount(React.createElement(Transaction,{item,api,money:String,onSaved(){},onCancel(){}}));await settle();await press('Selecionar Natureza');await press('Escolher Estorno');assert.doesNotMatch(text(),/Selecione para aceitar/);await press('Salvar sem validar');await settle();
  assert.equal(writes.length,1);assert.deepEqual(writes[0],{natureza:'ESTORNO',expected_token:'source'});assert.equal(choiceValue('Natureza'),'ESTORNO');
});
test('Choosing the suggested nature explicitly includes it in a confirmed capture patch',async()=>{
  const writes=[],item={...unclassifiedIncome,provisional_id:'explicit-capture',revision:1,status:'confirmed',categoria_sugerida:'Viagem',merchant_raw:'REEMBOLSO AMIGO VIAGEM'};
  const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return inferredNatureOptions;if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);return{persisted:true,provisional_id:item.provisional_id,request_id:body.request_id,item:{...item,...body}};}throw Error(route);};
  await mount(React.createElement(Provisional,{initialItem:item,api,money:String,onUpdated:async()=>{}}));await settle();await press('Selecionar Natureza');await press('Escolher Estorno');assert.doesNotMatch(text(),/Selecione para aceitar/);await press('Salvar correções');await settle();
  assert.equal(writes.length,1);const {request_id,...body}=writes[0];assert.ok(request_id);assert.deepEqual(body,{natureza:'ESTORNO',expected_revision:1});
});
test('A legacy draft preserves notes and its original nature separately without accepting old automatic classification',async()=>{
  const writes=[],item={...unclassifiedIncome,provisional_id:'legacy-nature',revision:1,status:'confirmed',categoria_sugerida:'Viagem',merchant_raw:'REEMBOLSO AMIGO VIAGEM',vendedor:'Original'};
  const original={natureza:'ESTORNO',detalhe:'Saved note',vendedor:'Edited origin'};
  drafts.set(item.provisional_id,{provisional_id:item.provisional_id,item:JSON.stringify(item),draft:JSON.stringify(original),updated_at:'2026-10-03T12:00:00Z'});
  const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return inferredNatureOptions;if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);return{persisted:true,provisional_id:item.provisional_id,request_id:body.request_id,item:{...item,...body}};}throw Error(route);};
  await mount(React.createElement(Provisional,{initialItem:item,api,money:String,onUpdated:async()=>{}}));await settle();
  assert.equal(choiceValue('Natureza'),'');assert.match(text(),/Rascunho anterior: Estorno, ainda não confirmado/);
  assert.equal(field('Observações').props.value,'Saved note');assert.equal(field('Origem').props.value,'Edited origin');
  assert.equal(preserved.size,1);assert.deepEqual(JSON.parse([...preserved.values()][0].state).draft,original,'Original draft survives intact before migration');
  const migrated=JSON.parse(drafts.get(item.provisional_id).draft);assert.equal(migrated._classification_intent_version,'2');assert.equal(migrated._unconfirmed_legacy_nature,'ESTORNO');assert.equal(migrated.natureza,'');
  await input('Observações','Updated note');await press('Salvar correções');await settle();assert.equal(writes.length,1);
  const {request_id,...body}=writes[0];assert.ok(request_id);assert.deepEqual(body,{vendedor:'Edited origin',detalhe:'Updated note',expected_revision:1});
  assert.equal(JSON.parse([...preserved.values()][0].state).draft.natureza,'ESTORNO');
});
test('Explicitly accepting a migrated nature survives editor restart and sends no draft metadata',async()=>{
  const writes=[],item={...unclassifiedIncome,provisional_id:'legacy-accepted',revision:1,status:'confirmed',categoria_sugerida:'Viagem',merchant_raw:'REEMBOLSO AMIGO VIAGEM'};
  drafts.set(item.provisional_id,{provisional_id:item.provisional_id,item:JSON.stringify(item),draft:JSON.stringify({natureza:'ESTORNO',detalhe:'Keep my note'}),updated_at:'2026-10-03T12:00:00Z'});
  const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return inferredNatureOptions;if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);return{persisted:true,provisional_id:item.provisional_id,request_id:body.request_id,item:{...item,...body}};}throw Error(route);};
  const props={initialItem:item,api,money:String,onUpdated:async()=>{}};
  await mount(React.createElement(Provisional,props));await settle();assert.equal(choiceValue('Natureza'),'');await press('Selecionar Natureza');await press('Escolher Estorno');await settle();
  assert.equal(choiceValue('Natureza'),'ESTORNO');assert.doesNotMatch(text(),/Rascunho anterior|Selecione para aceitar/);
  const stored=JSON.parse(drafts.get(item.provisional_id).draft);assert.equal(stored._classification_intent_version,'2');assert.equal(stored._unconfirmed_legacy_nature,'');
  await act(async()=>renderer.unmount());renderer=null;await mount(React.createElement(Provisional,props));await settle();assert.equal(choiceValue('Natureza'),'ESTORNO','Versioned explicit selection survives restart');assert.equal(field('Observações').props.value,'Keep my note');assert.equal(preserved.size,1);
  assert.doesNotMatch(text(),/Rascunho anterior|Selecione para aceitar/);
  await press('Salvar correções');await settle();assert.equal(writes.length,1);const {request_id,...body}=writes[0];assert.ok(request_id);assert.deepEqual(body,{natureza:'ESTORNO',detalhe:'Keep my note',expected_revision:1});
});
test('Provisional patches preserve an explicit clearing of optional fields and category children',async()=>{
  let body;const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return options;if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){body=JSON.parse(init.body);return{persisted:true,provisional_id:'capture',request_id:body.request_id,item:{...oldCapture,...body}};}throw Error(route);};
  await mount(React.createElement(Provisional,{initialItem:oldCapture,api,money:String,onUpdated:async()=>{}}));await settle();await input('Plataforma','');await press('Selecionar Categoria');await press('Escolher Lazer');await press('Salvar correções');await settle();
  assert.equal(body.plataforma,'');assert.equal(body.categoria_sugerida,'Lazer');assert.equal(body.subcategoria,'');
});
test('Reopening a provisional editor keeps cleared optional fields empty despite source suggestions',async()=>{
  let current={...oldCapture,plataforma:'iFood',plataforma_sugerida:'iFood',subcategoria_sugerida:'Itens',metodo_pagamento:'pix',metodo_pagamento_sugerida:'pix'};const writes=[];
  const api=async(route,init={})=>{if(route.endsWith('/classification-options'))return{...options,metodo_pagamento:['pix']};if(route==='/v1/account-positions')return{items:[]};if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);current={...current,...body,revision:current.revision+1};return{persisted:true,provisional_id:'capture',request_id:body.request_id,item:{...current}};}throw Error(route);};
  const props={api,money:String,onUpdated:async()=>{}};
  await mount(React.createElement(Provisional,{...props,initialItem:{...current}}));await settle();await input('Plataforma','');await clearChoice('Subcategoria');await clearChoice('Meio de pagamento');await press('Salvar correções');await settle();
  assert.equal(current.plataforma,'');assert.equal(current.subcategoria,'');assert.equal(current.metodo_pagamento,'');
  await act(async()=>renderer.unmount());renderer=null;await mount(React.createElement(Provisional,{...props,initialItem:{...current}}));await settle();
  assert.equal(field('Plataforma').props.value,'','Detected platform is a suggestion, not a persisted choice');assert.equal(choiceValue('Subcategoria'),'');assert.equal(choiceValue('Meio de pagamento'),'');
  await input('Observações','A new explicit note');await press('Salvar correções');await settle();assert.equal(writes.length,2);assert.equal(current.plataforma,'');assert.equal(current.subcategoria,'');assert.equal(current.metodo_pagamento,'');
});
test('Reopening a ledger editor keeps cleared optional fields empty despite source suggestions',async()=>{
  let current={id:'transaction',source_token:'token-1',valor:-2,vendedor:'Original',plataforma:'iFood',plataforma_sugerida:'iFood',categoria:'Casa',subcategoria:'Itens',subcategoria_sugerida:'Itens',metodo_pagamento:'pix',metodo_pagamento_sugerida:'pix'};const writes=[];
  const api=async(route,init={})=>{if(init.method==='PATCH'){const body=JSON.parse(init.body);writes.push(body);current={...current,...body,source_token:'token-'+(writes.length+1)};return{item:{...current}};}if(route.endsWith('/classification-options'))return{...options,metodo_pagamento:['pix']};return{total:0};};
  const props={api,money:String,onSaved(){},onCancel(){}};
  await mount(React.createElement(Transaction,{...props,item:{...current}}));await settle();await input('Plataforma','');await clearChoice('Subcategoria');await clearChoice('Meio de pagamento');await press('Salvar sem validar');await settle();
  assert.equal(current.plataforma,'');assert.equal(current.subcategoria,'');assert.equal(current.metodo_pagamento,'');
  await act(async()=>renderer.unmount());renderer=null;await mount(React.createElement(Transaction,{...props,item:{...current}}));await settle();assert.equal(field('Plataforma').props.value,'');assert.equal(choiceValue('Subcategoria'),'');assert.equal(choiceValue('Meio de pagamento'),'');
  await input('Observações','An explicit ledger note');await press('Salvar sem validar');await settle();assert.equal(writes.length,2);for(const key of ['plataforma','subcategoria','metodo_pagamento'])assert.ok(!(key in writes[1]),'Unchanged empty optional must not be restored: '+key);
});
test('A category deletion confirmation cannot execute after its screen is disposed',async()=>{
  let writes=0;const api=async(_route,init={})=>{if(init.method==='DELETE')writes++;return{details:[{name:'Synthetic',usage_count:0}]};};
  await mount(React.createElement(Category,{api,money:String,spending:[],onUpdated:async()=>{}}));await settle();await press('Excluir');const confirm=dialogs.at(-1)[2].find(item=>item.text==='Excluir').onPress;
  await act(async()=>renderer.unmount());renderer=null;await act(async()=>confirm());assert.equal(writes,0);
});
test('A subcategory confirmation belongs to the category that opened it',async()=>{
  let writes=0;const api=async(_route,init={})=>{if(init.method==='PATCH')writes++;return{details:[{name:'Itens',usage_count:1,token:'token'}]};};
  const props={api,disabled:false,onBusyChange(){},onUpdated:async()=>{}};
  await mount(React.createElement(Subcategory,{...props,category:'Casa'}));await settle();await press('Editar subcategoria Itens');await input('Novo nome da subcategoria','My rename');await press('Salvar');const confirm=dialogs.at(-1)[2].find(item=>item.text==='Renomear').onPress;
  await update(React.createElement(Subcategory,{...props,category:'Lazer'}));await settle();await act(async()=>confirm());assert.equal(writes,0,'Old category callback cannot mutate the new category owner');
});
test('A provisional discard confirmation cannot execute after its screen is disposed',async()=>{
  let writes=0;const api=async(route,init={})=>{if(init.method==='POST'){writes++;const body=JSON.parse(init.body);return{persisted:true,provisional_id:'capture',request_id:body.request_id};}if(route.endsWith('/classification-options'))return options;if(route==='/v1/account-positions')return{items:[]};return{items:[oldCapture],total:1};};
  await mount(React.createElement(Provisional,{api,money:String,onUpdated:async()=>{}}));await settle();await press('Descartar');const confirm=dialogs.at(-1)[2].find(item=>item.text==='Descartar').onPress;
  await act(async()=>renderer.unmount());renderer=null;await act(async()=>confirm());assert.equal(writes,0);
});
test('An account deletion confirmation cannot execute after its screen is disposed',async()=>{
  let writes=0;const item={key:'synthetic-account',instituicao:'Synthetic',apelido:'Draft',status:'aberta',tipo:'conta_corrente',revision:1,moeda:'BRL',balance:null};
  const api=async(route,init={})=>{if(init.method==='DELETE')writes++;if(route.endsWith('/deletion'))return{can_delete:true,revision:1};return{items:[item]};};
  await mount(React.createElement(Accounts,{api,money:String}));await settle();await press('Detalhes de Draft · Synthetic');await press('Excluir conta');await settle();const confirm=dialogs.at(-1)[2].find(item=>item.text==='Excluir').onPress;
  await act(async()=>renderer.unmount());renderer=null;await act(async()=>confirm());assert.equal(writes,0);
});
test('A month-close confirmation cannot execute after its screen is disposed',async()=>{
  let writes=0;const data={month:'2026-09',start:'2026-09-01',end:'2026-09-30',revision:1,status:'open',groups:[],completed:0,total:0,progress:100,can_close:true,totals:{income:'0',expenses:'0'}};
  const api=async(_route,init={})=>{if(init.method==='POST')writes++;return data;};
  await mount(React.createElement(Month,{api,money:String,onBack(){},onNavigate(){}}));await settle();await press('Concluir checklist do mês');const confirm=dialogs.at(-1)[2].find(item=>item.text==='Concluir').onPress;
  await act(async()=>renderer.unmount());renderer=null;await act(async()=>confirm());assert.equal(writes,0);
});
test('A managed commitment conflict preserves local edits, rebases untouched fields and refreshes its revision',async()=>{
  let writes=0,body;const base={commitment_id:'managed',name:'Original',revision:1,kind:'assinatura',purchase_date:'2026-10-01',next_due_date:'2026-10-09',amount:10,frequency:'mensal',payment_method:'pix',renewal_status:'automatica',status:'ativo',notes:'Original note',schedule:[]};let current=base;
  const api=async(route,init={})=>{if(init.method==='PUT'){writes++;body=JSON.parse(init.body);if(writes===1){current={...base,name:'Remote name',notes:'Remote note',revision:2};throw Object.assign(new Error('Source changed'),{status:409});}assert.equal(body.revision,2);current={...current,...body};return{item:current};}if(route==='/v1/commitments')return{items:[current],has_more:false};return{items:[],has_more:false};};
  await mount(React.createElement(Commitments,{api,upload:async()=>({}),accounts:[],money:String,onUpdated(){}}));await settle();await press('Abrir Original');await press('Editar compromisso Original');await input('Nome ou serviço','My name');await press('Salvar e sincronizar');await settle();assert.equal(field('Nome ou serviço').props.value,'My name');assert.equal(button('Salvar e sincronizar').props.disabled,true);
  await press('Carregar compromisso atual e conferir');await settle();await press('Avançado');assert.equal(field('Observações (opcional)').props.value,'Remote note');assert.match(text(),/Remote name/);assert.equal(button('Salvar e sincronizar').props.disabled,true);
  await press('Conferi as diferenças e quero reaplicar meus campos');await press('Salvar e sincronizar');await settle();assert.equal(writes,2);assert.equal(body.name,'My name');assert.equal(body.notes,'Remote note');
});
test('Commitments use current card names, exclude deleted cards and keep legacy bank identities selectable',async()=>{
  const account='bank-id';let saved;
  const item={commitment_id:'named-card',name:'Service',revision:1,kind:'assinatura',purchase_date:'2026-10-01',next_due_date:'2026-11-01',amount:100,frequency:'mensal',payment_method:'cartao',card_id:'card',account_id:account,renewal_status:'automatica',status:'ativo',schedule:[]};
  const api=async(route,init={})=>{if(init.method==='PUT'){saved=JSON.parse(init.body);return{persisted:true};}return{items:route==='/v1/commitments'?[item]:[],has_more:false};};
  await mount(React.createElement(Commitments,{api,upload:async()=>({}),accounts:[{id:'card',tipo:'cartao_credito',instituicao:'Old name',cartao_final:'0000'}],cards:[{id:'card',apelido:'Renamed card',instituicao:'Bank',cartao_final:'4242'},{id:'deleted',apelido:'Deleted card',deleted:true}],profiles:[{key:'itau',instituicao:'Itau',tipo:'conta_corrente',legacy_account_id:account}],money:String,onUpdated(){}}));await settle();
  assert.match(text(),/Renamed card/);assert.doesNotMatch(text(),/Old name/);
  await press('Abrir Service');await press('Editar compromisso Service');assert.doesNotMatch(text(),/Deleted card/);
  const bank=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(t=>t.children.join('').includes('Itau')));
  await act(async()=>bank.props.onPress());await press('Salvar e sincronizar');await settle();
  assert.equal(saved.account_id,account);assert.equal(saved.card_id,'card');
});
const projected=(months=16)=>({months,start_month:'2026-10',end_month:months===16?'2028-01':months===24?'2028-09':'2027-03',last_installment_month:'2028-01',total:'120',as_of:'2026-10-08',warnings:[],items:Array.from({length:months},(_,i)=>{const n=2026*12+9+i;return{month:`${Math.floor(n/12)}-${String(n%12+1).padStart(2,'0')}`,total:'120',installments:'80',subscriptions:'40',other:'0',estimated:'0',bill_balance:'0',items:[{source_ref:'plan:1',name:'Parcelada',amount:'80',category:'installments'}]};})});
test('Monthly projection defaults to the last installment and applies a custom period without accepting invalid months',async()=>{
  const requests=[];const api=async route=>{requests.push(route);const m=new URLSearchParams(route.split('?')[1]).get('months');return projected(m?Number(m):16);};
  await mount(React.createElement(Projection,{api,money:String}));await settle();assert.match(text(),/jan\/2028/);assert.equal(requests[0],'/v1/commitment-projection');
  await press('Personalizado');await input('Quantidade de meses do gráfico','24');await press('Aplicar período');await settle();assert.equal(requests.at(-1),'/v1/commitment-projection?months=24');assert.match(text(),/set\/2028/);
  await press('Personalizado');await input('Quantidade de meses do gráfico','121');await press('Aplicar período');await settle();assert.match(text(),/Escolha de 1 a 120 meses/);assert.equal(requests.length,2);
  await press('Tudo');await settle();assert.equal(requests.at(-1),'/v1/commitment-projection');
});
test('Monthly projection ignores an older period response and preserves the applied period during refresh failures',async()=>{
  const older=deferred();let fail=false;
  const api=async route=>{if(route.endsWith('months=6'))return older.promise;if(fail)throw Error('Synthetic read failed');return projected(route.endsWith('months=24')?24:16);};
  await mount(React.createElement(Projection,{api,money:String}));await settle();await press('6 meses');await press('24 meses');await settle();older.resolve(projected(6));await settle();assert.match(text(),/set\/2028/);assert.doesNotMatch(text(),/Exibindo out\/2026 a mar\/2027/);
  fail=true;await update(React.createElement(Projection,{api,money:String,refreshKey:1}));await settle();assert.match(text(),/Synthetic read failed/);assert.match(text(),/set\/2028/);assert.equal(button('24 meses').props.accessibilityState.selected,true);
});
test('Monthly projection selects a month and lists its charges and separate invoice balance',async()=>{
  const data=projected();data.items[1].items=[{source_ref:'utility',name:'Energia',category:'subscriptions',amount:'100',estimated:true}];data.items[1].bill_balance='500';data.items[1].estimated='100';
  await mount(React.createElement(Projection,{api:async()=>data,money:String}));await settle();await press('nov/2026: 120 previstos');assert.match(text(),/Energia/);assert.match(text(),/Faturas conhecidas em aberto: 500/);assert.match(text(),/100 de contas variáveis/);
});
test('A rolling estimate can be changed to a fixed value and cancelled by its owner',async()=>{
  let saved;const item={commitment_id:'utility',name:'Cemig',revision:1,kind:'assinatura',purchase_date:'2026-10-01',next_due_date:'2026-11-01',amount:159.87,amount_mode:'last_three_bills',amount_rule_available:true,amount_estimate:{requires_review:false},frequency:'mensal',payment_method:'debito',account_id:'bank',renewal_status:'automatica',status:'ativo',schedule:[{due_date:'2026-11-01'}]};
  const api=async(route,init={})=>{if(init.method==='PUT'){saved=JSON.parse(init.body);return{persisted:true};}if(route==='/v1/commitments')return{items:[item],has_more:false};return{items:[],has_more:false};};
  await mount(React.createElement(Commitments,{api,upload:async()=>({}),accounts:[],money:String,onUpdated(){}}));await settle();await press('Abrir Cemig');await press('Editar compromisso Cemig');assert.equal(field('Valor estimado mensal (R$)').props.editable,false);
  assert.equal(renderer.root.findAllByType(Voice).length,0);
  assert.equal(field('Valor estimado mensal (R$)').props.value,'159,87');
  await press('Valor fixo');assert.equal(field('Valor por cobrança (R$)').props.editable,true);await input('Valor por cobrança (R$)','175.20');await press('Avançado');await press('Cancelado');await press('Salvar e sincronizar');await settle();assert.equal(saved.amount_mode,'fixed');assert.equal(saved.amount,175.2);assert.equal(saved.status,'cancelado');
});
test('An ambiguous commitment payment requires an explicit due date before confirming the same command',async()=>{
  let body,writes=0;
  const item={commitment_id:'weekly',name:'Weekly',revision:3,kind:'assinatura',purchase_date:'2026-09-01',next_due_date:'2026-10-01',amount:29.9,frequency:'semanal',payment_method:'pix',renewal_status:'automatica',status:'ativo',schedule:[{due_date:'2026-10-01'}]};
  const tx={id:'payment',source_kind:'transaction',source_token:'observed-source',merchant_raw:'Weekly actual payment',valor:-29.9,data_evento:'2026-10-04T12:00:00Z',requires_occurrence_selection:true,next_occurrence_due_date:'2026-10-01',occurrence_options:[{due_date:'2026-10-01'},{due_date:'2026-10-08'}]};
  const api=async(route,init={})=>{if(init.method==='POST'){writes++;body=JSON.parse(init.body);return{persisted:true,revision:4};}if(route.endsWith('/payment-candidates'))return{items:[tx],commitment_revision:3};if(String(route).includes('/statement-search'))return{items:[tx],commitment_revision:3,total:1,truncated:false};if(route==='/v1/commitments')return{items:[item],has_more:false};return{items:[],has_more:false};};
  await mount(React.createElement(Commitments,{api,upload:async()=>({}),accounts:[],money:String,onUpdated(){}}));await settle();await press('Abrir Weekly');await press('Vincular movimentação de Weekly');await settle();
  assert.equal(button('Vincular este lançamento').props.disabled,true,'No inferred occurrence before a choice');assert.equal(writes,0);
  await press('Weekly actual payment');
  const choices=renderer.root.findAllByType('Pressable').filter(node=>String(node.props.accessibilityLabel||'').startsWith('Liquidar ocorrência'));
  assert.equal(choices.length,2);assert.equal(choices[1].props.disabled,true,'Later occurrence requires agenda review');
  await act(async()=>choices[0].props.onPress());await press('Vincular este lançamento');await settle();
  assert.equal(writes,1);assert.equal(body.occurrence_due_date,'2026-10-01');assert.equal(body.expected_revision,3);assert.equal(body.expected_source_token,'observed-source');assert.ok(body.request_id);
});
test('Imported installments show their reviewed card and flag a missing identity',async()=>{
  const plans=[{id:'itau-plan',descricao:'Compra exemplo',numero_parcelas:12,data_compra:'2026-01-01',card_label:'Cartão exemplo 4242',card_id:'itau',installments:[{status:'projetada',valor:100}]},
    {id:'mp-plan',descricao:'Loja exemplo',numero_parcelas:8,data_compra:'2026-01-01',card_label:'Cartão exemplo 1111',card_id:'mp',installments:[]},
    {id:'missing-plan',descricao:'Conferir identidade',numero_parcelas:2,data_compra:'2026-09-01',card_link_requires_review:true,installments:[]}];
  const api=async route=>({items:route==='/v1/installments'?plans:[]});
  await mount(React.createElement(Commitments,{api,upload:async()=>({}),accounts:[],money:String,onUpdated(){}}));await settle();
  assert.match(text(),/Cartão exemplo 4242/);
  await press('Concluídos e cancelados');
  assert.match(text(),/Cartão: Cartão exemplo 1111/);
  assert.match(text(),/Cartão: não vinculado/);
});
test('A delayed purchase-card capture cannot open the native sharing window after disposal',async()=>{
  const captured=deferred();captureImage=()=>captured.promise;sharedImages=0;
  const horizon={start:'2026-10-04',end:'2027-01-03',days:90,minimum_months:3};
  const api=async(route)=>route.includes('purchase-context')?{horizon,flows:[],suggested_flows:[],gaps:[]}:{horizon,price:'10',installments:1,work_hours:null,gaps:[]};
  await mount(React.createElement(Purchase,{api,upload:async()=>({}),onBack(){},ready:true}));await settle(330);await input('Preço total confirmado (R$)','10');await press('Ver impacto da compra');await settle();await press('Prévia do cartão compartilhável');await press('Compartilhar cartão como imagem');
  await act(async()=>renderer.unmount());renderer=null;await act(async()=>captured.resolve('synthetic:public-card'));await settle();assert.equal(sharedImages,0,'A disposed screen must discard the pending native share');
});
(async()=>{let failed=0,total=0;for(const {name,work} of cases){if(process.env.DOMAIN_CASE&&!name.includes(process.env.DOMAIN_CASE))continue;total++;console.log('START',name);dialogs.length=0;drafts.clear();commands.clear();voiceDrafts.clear();preserved.clear();try{await work();console.log('PASS',name);}catch(error){failed++;console.error('FAIL',name,error.message);}finally{if(renderer){await act(async()=>renderer.unmount());renderer=null;}await act(async()=>cleanups.splice(0).forEach(cleanup=>cleanup()));await settle();}}
  console.log(`${total-failed}/${total} domain transition regressions passed`);if(failed)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
