// Production mechanisms with isolated local storage, durable files and HTTP.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const storage=new Map(),cache=new Map(),alerts=[];
let failKey='',renderer;
const recorder={uri:'blob:recorded',prepareToRecordAsync:async()=>{},record(){},stop:async()=>{}};
const native={Platform:{OS:'android'},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},BackHandler:{addEventListener:()=>({remove(){}})},
 FlatList:({data,renderItem,ListEmptyComponent})=>React.createElement('View',null,data.length?data.map(item=>React.createElement(React.Fragment,{key:item.value},renderItem({item}))):ListEmptyComponent),
  StyleSheet:{create:x=>x},...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator'].map(name=>[name,name]))};
const mocks={'react':React,'react-native':native,'../utils/appAlert':{Alert:{alert:(...args)=>alerts.push(args)}},
 'react-native-safe-area-context':{SafeAreaView:'SafeAreaView'},
 'expo-audio':{useAudioRecorder:()=>recorder,useAudioRecorderState:()=>({durationMillis:0}),RecordingPresets:{HIGH_QUALITY:{}},setAudioModeAsync:async()=>{},AudioModule:{requestRecordingPermissionsAsync:async()=>({granted:true})}},
 'expo-document-picker':{getDocumentAsync:async()=>({assets:[{uri:'blob:synthetic-audio',name:'voz.webm',mimeType:'audio/webm',size:80}]})},
 './durableFiles':{persistQueuedFile:async(_uri,id)=>'idb:'+id},
 '../utils/voiceFile':{voiceFormData:async()=>new FormData()},'../utils/devicePrompt':{withDevicePrompt:work=>work()}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};
 const original=process.env.CAPTURE_ORIGINAL_DIR&&path.join(process.env.CAPTURE_ORIGINAL_DIR,path.relative(process.cwd(),file));
 const selected=original&&fs.existsSync(original)?original:file;
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(selected,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
 {module:m,exports:m.exports,console,URLSearchParams,FormData,Date,Promise,AbortController,setTimeout,clearTimeout,Blob,
 navigator:{locks:{request:async(_key,work)=>work()}},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>{if(key===failKey)throw Error('Synthetic storage failure');storage.set(key,value)}},
 require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:selected});cache.set(file,m.exports);return m.exports;}
const db=load('utils/queueDb.web.tsx').webQueueDb;mocks['../utils/queueDb']={useSQLiteContext:()=>db};
const {createQueueSync,requirePersistedAck,permanentQueueError}=load('utils/syncQueue.ts');
const {readQueueAcknowledgement,persistQueueAcknowledgement}=load('utils/queueAcknowledgements.ts');
const {runReviewCommand,pendingReviewCommand}=load('utils/reviewCommand.ts');
const {sameReviewSource}=load('utils/reviewDraftGuard.ts');
const tests=[],test=(name,work)=>tests.push({name,work});
const row={id:1,event_id:'33333333-3333-4333-8333-333333333333',kind:'document',payload:'{}',local_uri:'idb:original',filename:'receipt.png',mime_type:'image/png'};
const ack={persisted:true,event_id:row.event_id};
test('Documents reject persisted ACK from another operation',()=>{
 assert.throws(()=>requirePersistedAck(row,{...ack,event_id:'44444444-4444-4444-8444-444444444444'}),/outro evento/);
});
test('In-progress replay is retried while a definitive conflict is quarantined',()=>{
 assert.equal(permanentQueueError({status:409,retryable:true}),false);
 assert.equal(permanentQueueError({status:409,retryable:false}),true);
});
test('Image, PDF, voice evidence, CSV/XLSX and batch carry immutable operation identity',async()=>{
 mocks['./apiClient']={ApiError:class extends Error{constructor(message,status){super(message);this.status=status}}};
 mocks['./pdfCpf']={isPdfCapture:()=>false,appendPdfCpf:async()=>false};
 mocks['./uploadFile']={appendQueuedFile:async(form,item)=>form.append('file',item.local_uri)};
 const {submitQueuedCapture}=load('utils/captureSubmission.ts');
 for(const [kind,filename,route] of [['document','receipt.jpg','/v1/documents/extract'],['document','receipt.pdf','/v1/documents/extract'],['document','voice-id.txt','/v1/documents/extract'],['document','statement.csv','/v1/import'],['document','statement.xlsx','/v1/import'],['batch','statement.pdf','/v1/capture-batches/scan']]){
   await submitQueuedCapture({...row,kind,filename},async(url,body)=>{assert.equal(url,route);assert.equal(body.get('event_id'),row.event_id);if(kind==='batch')assert.equal(body.get('batch_id'),row.event_id);return ack},()=>{});
 }
});
test('Receipt prevents resend after local cleanup failure and process restart',async()=>{
 let rows=[row],sends=0,failures=0,cleanFails=true,lastError='';
 const options={prepare:async()=>true,list:async()=>rows,send:async()=>{sends++;return ack},
  getAcknowledgement:item=>readQueueAcknowledgement(db,item),acknowledge:(item,result)=>persistQueueAcknowledgement(db,item,result),done(){},
  remove:async()=>{if(cleanFails)throw Error('Synthetic cleanup failure');rows=[]},recordFailure:async()=>{failures++},changed:(_count,error)=>lastError=error};
 const sync=createQueueSync(options);await sync();await sync();
 assert.equal(sends,1);assert.equal(failures,0);assert.match(lastError,/confirmada no servidor/);assert.equal(rows.length,1);
 cleanFails=false;await createQueueSync(options)();assert.equal(sends,1,'Restart reads receipt and only finalizes');assert.equal(rows.length,0);
});
test('Changed payload cannot consume a receipt belonging to previous content',async()=>{
 await persistQueueAcknowledgement(db,row,ack);
 await assert.rejects(readQueueAcknowledgement(db,{...row,payload:'{"altered":true}'}),/outro conteúdo/);
});
test('Receipt storage failure retains in-memory outcome and does not classify write as failed',async()=>{
 const receipt={...row,event_id:'55555555-5555-4555-8555-555555555555'};let sends=0,failures=0,removed=0;
 const sync=createQueueSync({prepare:async()=>true,list:async()=>removed?[]:[receipt],send:async()=>{sends++;return {...ack,event_id:receipt.event_id}},
  acknowledge:(item,result)=>persistQueueAcknowledgement(db,item,result),done(){},remove:async()=>{removed++},recordFailure:async()=>{failures++},changed(){}});
 failKey='financial-twin-queue-acknowledgements';await sync();assert.equal(removed,0,'Receipt must become durable before local row deletion');failKey='';await sync();assert.equal(sends,1);assert.equal(failures,0);assert.equal(removed,1);
});
test('Review command response loss retains UUID and exact body through restart; changed fields are blocked',async()=>{
 let sends=0,firstRequest,lastRequest;
 const api=async(_path,init)=>{sends++;const body=JSON.parse(init.body);firstRequest??=body;lastRequest=body;if(sends===1)throw Error('Response lost after server commit');return {persisted:true,request_id:body.request_id,provisional_id:'capture',item:{revision:2,status:'confirmed'}}};
 await assert.rejects(runReviewCommand(db,api,'capture','/v1/provisional/capture',{vendedor:'Loja',expected_revision:1,action:'confirm'}),/Response lost/);
 assert.ok(await pendingReviewCommand(db,'capture'));
 await assert.rejects(runReviewCommand(db,api,'capture','/v1/provisional/capture',{vendedor:'Different',expected_revision:2}),/tentativa anterior/);assert.equal(sends,1);
 await runReviewCommand(db,api,'capture');assert.deepEqual(lastRequest,firstRequest);assert.equal(await pendingReviewCommand(db,'capture'),null);
});
test('Explicit review conflict retains rejected request history and permits a new reviewed revision',async()=>{
 await assert.rejects(runReviewCommand(db,async()=>{throw Object.assign(Error('Conflict'),{status:409})},'other','/v1/provisional/other',{expected_revision:1}),/Conflict/);
 assert.equal(await pendingReviewCommand(db,'other'),null);
 const saved=await runReviewCommand(db,async(_path,init)=>({persisted:true,provisional_id:'other',request_id:JSON.parse(init.body).request_id}),'other','/v1/provisional/other',{expected_revision:2});assert.equal(saved.persisted,true);
});
test('Local draft source rejects newer revision and terminal status',()=>{
 const original={provisional_id:'capture',revision:1,status:'provisional',status_revisao:'pendente'};
 assert.equal(sameReviewSource(original,{...original,revision:2}),false);assert.equal(sameReviewSource(original,{...original,status:'confirmed'}),false);
});
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,15))});
const press=label=>act(async()=>{const button=renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label);assert.ok(button,label);assert.ok(!button.props.disabled,label+' enabled');button.props.onPress()});
test('Voice clip and edited transcription survive editor disposal and process restart without posting',async()=>{
 const Voice=load('components/VoiceCapture.tsx').default;let postings=0;
 const props={api:async()=>({draft:{name:'Synthetic'}}),upload:async()=>({text:'Transcrição original'}),context:'movement',draftKey:'voice-synthetic',onApply:async()=>{postings++}};
 await act(async()=>{renderer=create(React.createElement(Voice,props))});await settle();await press('Escolher áudio');await settle();await press('Transcrever com xAI');await settle();
 await act(async()=>renderer.root.findByProps({accessibilityLabel:'Transcrição da voz'}).props.onChangeText('Texto revisado pelo usuário'));await settle();await act(async()=>renderer.unmount());
 await act(async()=>{renderer=create(React.createElement(Voice,props))});await settle();
 assert.equal(renderer.root.findByProps({accessibilityLabel:'Transcrição da voz'}).props.value,'Texto revisado pelo usuário');assert.equal(postings,0);
 await press('Preencher campos com a voz');await settle();assert.equal(postings,1);await act(async()=>renderer.unmount());
});
test('Provisional editor does not silently apply old draft over current revision; it preserves before rebase',async()=>{
  const Review=load('components/ProvisionalReview.tsx').default;
  const {ChoiceOverlayProvider}=load('components/ChoiceOverlay.tsx');
 const old={provisional_id:'stale',revision:1,vendedor:'Original',valor:-12,status:'provisional',status_revisao:'pendente'};
 await db.runAsync('INSERT INTO review_drafts','stale',JSON.stringify(old),JSON.stringify({vendedor:'Minhas edições',valor:'-12,00'}),'2026-10-03');
 await act(async()=>{renderer=create(React.createElement(ChoiceOverlayProvider,null,React.createElement(Review,{initialItem:{...old,revision:2,vendedor:'Versão atual'},api:async()=>({items:[]}),money:String,onUpdated:async()=>{}})))});await settle();
 const save=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children==='Salvar correções'));
 assert.equal(save.props.disabled,true);assert.equal(renderer.root.findByProps({accessibilityLabel:'Vendedor'}).props.value,'Minhas edições');
 assert.equal(JSON.parse((await db.getAllAsync('SELECT * FROM review_drafts WHERE provisional_id=?','stale'))[0].item).revision,1);
 await press('Usar versão atual e preservar meu rascunho');await settle();
 assert.equal(renderer.root.findByProps({accessibilityLabel:'Vendedor'}).props.value,'Versão atual');
 assert.ok((await db.getAllAsync('SELECT * FROM preserved_drafts','stale')).some(saved=>JSON.parse(saved.state).draft.vendedor==='Minhas edições'));
 await act(async()=>renderer.unmount());
});
test('Batch draft cannot silently overlay changed extraction and preserves source before explicit recovery',async()=>{
 const Batch=load('components/StatementBatchReview.tsx').default,{ChoiceOverlayProvider}=load('components/ChoiceOverlay.tsx');
 const batch={batch_id:'changed-batch',filename:'teste.pdf',sha256:'b'.repeat(64),complete:true,rows:[{row_id:1,date:'2026-10-03',amount:'-10',description:'Extração atual',kind:'expense'}],status:'draft'};
 const draft={source:'old extraction',rows:[{...batch.rows[0],date:'03/10/2026',amount:'-10,00',description:'Minha correção',selected:true}],account:'ledger:account',partial:false};
 await db.runAsync('INSERT INTO batch_drafts',batch.batch_id,JSON.stringify(draft),'2026-10-03');
 const api=async route=>route.includes('?')?{items:[batch]}:route.endsWith('/preview')?{rows:[],scan_complete:true}:batch;
 await act(async()=>{renderer=create(React.createElement(ChoiceOverlayProvider,null,React.createElement(Batch,{api,visible:true,revision:0,accounts:[],profiles:[],onUpdated:async()=>{}})))});await settle();await press('Revisar teste.pdf');await settle();
 assert.equal(renderer.root.findByProps({accessibilityLabel:'Descrição da linha 1'}).props.value,'Extração atual');
 assert.equal(JSON.parse((await db.getAllAsync('SELECT * FROM batch_drafts WHERE batch_id=?',batch.batch_id))[0].state).source,'old extraction');
 await press('Conferi a mudança: recuperar minhas edições');await settle();
 assert.equal(renderer.root.findByProps({accessibilityLabel:'Descrição da linha 1'}).props.value,'Minha correção');
 assert.ok((await db.getAllAsync('SELECT * FROM preserved_drafts',batch.batch_id)).some(saved=>JSON.parse(saved.state).source==='old extraction'));
 await act(async()=>renderer.unmount());
});
test('A pending decision is visible even when no active captures or local review draft remain',async()=>{
 const Review=load('components/ProvisionalReview.tsx').default;
 await assert.rejects(runReviewCommand(db,async()=>{throw Error('Response lost')},'invisible','/v1/provisional/invisible/decision',{action:'confirm',expected_revision:1}),/Response lost/);
 const api=async(_route,init)=>init?.method==='POST'?{persisted:true,provisional_id:'invisible',request_id:JSON.parse(init.body).request_id,action:'confirm'}:{items:[]};
 await act(async()=>{renderer=create(React.createElement(Review,{api,money:String,onUpdated:async()=>{}}))});await settle();
 await press('Concluir tentativa de invisible');await settle();
 assert.equal(await pendingReviewCommand(db,'invisible'),null);
 assert.equal(renderer.root.findAllByType('Pressable').some(node=>node.props.accessibilityLabel==='Concluir tentativa de invisible'),false);
 await act(async()=>renderer.unmount());
});
(async()=>{let failed=0;for(const {name,work} of tests){try{await work();console.log('PASS '+name)}catch(error){failed++;console.error('FAIL '+name+'\n'+error.stack)}finally{failKey='';if(renderer){await act(async()=>renderer.unmount());renderer=null}}}if(failed)process.exitCode=1;console.log(`${tests.length-failed}/${tests.length} structural capture probes passed; no real ledger, files or native device used.`)})().catch(error=>{console.error(error);process.exitCode=1});
