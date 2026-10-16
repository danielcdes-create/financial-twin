// Production App: isolated local storage, child widgets and HTTP. Local ACK,
// subsequent read failures, delayed synchronization and retired UI callbacks.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const deferred=()=>{let resolve;const promise=new Promise(yes=>{resolve=yes});return{promise,resolve};};
let failures=0;
function harness(){
  const cache=new Map(),alerts=[],rows=[],nativeMarks=[],deletes=[];let renderer,io,failCount=false,token='session',sync=async()=>{},permission=Promise.resolve({granted:true}),cameraCalls=0,markFailure=false,nativeEvents=[],nativeReads=0,healthFailure=false;
  const db={getAllAsync:async()=>{if(failCount&&rows.length)throw new Error('Synthetic count failure');return rows;},
    runAsync:async(sql,...args)=>{if(sql.startsWith('INSERT OR IGNORE INTO event_queue')&&!rows.some(row=>row.event_id===args[0]))rows.push({event_id:args[0],kind:args[1],payload:args[2]});if(sql.startsWith('DELETE')){deletes.push({sql,args});if(sql.startsWith('DELETE FROM event_queue')){const index=rows.findIndex(row=>row.event_id===args[0]);if(index>=0)rows.splice(index,1);}}},execAsync:async()=>{}};
  const api={json:async(route)=>{if(route==='/health'&&healthFailure)throw Error('Synthetic server offline');return{items:[],mongodb:'connected',total:0,auth_required:true};},upload:async()=>{},getReadGeneration:()=>0,
    subscribeAvailability:()=>()=>{},getAvailability:()=> 'connected',configureMutationStore(){},pendingMutations:async()=>[],subscribeMutations:()=>()=>{}};
  const native={Platform:{OS:'android'},StyleSheet:{create:x=>x},useWindowDimensions:()=>({width:390,height:844,fontScale:1}),
    ...Object.fromEntries(['ActivityIndicator','FlatList','Pressable','ScrollView','Text','TextInput','View'].map(name=>[name,name]))};
  const mocks={'react':React,'react-native':{...native,BackHandler:{addEventListener:()=>({remove(){}})}},'react-native-safe-area-context':{SafeAreaView:'View',useSafeAreaInsets:()=>({top:0,bottom:0})},
    'expo-document-picker':{},'expo-image-picker':{requestCameraPermissionsAsync:()=>permission,launchCameraAsync:async()=>{cameraCalls++;return{canceled:true};}},'expo-router':{usePathname:()=> '/',router:{push(){}}},'expo-notification-capture':{__esModule:true,default:{get available(){return nativeEvents.length>0;},isEnabled:()=>true,getInboxSnapshot:()=>{nativeReads++;return{version:2,events:nativeEvents,state:'ready',recovery_count:0,error:''};},markSynced:id=>{if(markFailure)throw new Error('Synthetic native commit failure');nativeMarks.push(id);}}},
    './components/AuthProvider':{useAuth:()=>({lock(){}})},'./utils/queueDb':{useSQLiteContext:()=>db},
    './utils/useQueueSync':{useQueueSync:value=>{io=value;return ()=>sync();}},'./utils/useForeground':{useForeground:()=>true},
    './utils/sessionClient':{API:'https://synthetic.invalid',api,authState:{getToken:()=>token,unlocked:()=>true,sessionIdentity:()=>1}},
    './utils/appAlert':{Alert:{alert:(...args)=>alerts.push(args)}},'../utils/appAlert':{Alert:{alert:(...args)=>alerts.push(args)}},
    './sessionStore':{getItemAsync:async()=>null},'./utils/durableFiles':{},'./uploadFile':{appendQueuedFile:async()=>{}}};
  function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);
    const source=file===path.resolve('App.tsx')&&process.env.APP_TRANSITIONS_ORIGINAL?process.env.APP_TRANSITIONS_ORIGINAL:file;
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
      {module:m,exports:m.exports,console,Error,Date,Intl,Symbol,Promise,URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
        require:name=>{if(mocks[name])return mocks[name];if(name.startsWith('./components/')&&name!=='./components/InteractionControls')return{__esModule:true,default:props=>React.createElement(name.slice(13)+'Stub',props)};
          if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:source});cache.set(file,m.exports);return m.exports;}
  const App=load('App.tsx').default,gate=load('utils/userOperation.ts');
  const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,15));});
  return {rows,alerts,gate,nativeMarks,deletes,get nativeReads(){return nativeReads;},set nativeEvents(value){nativeEvents=value;},set healthFailure(value){healthFailure=value;},get cameraCalls(){return cameraCalls;},get io(){return io;},set failCount(value){failCount=value;},set token(value){token=value;},set sync(value){sync=value;},set permission(value){permission=value;},set markFailure(value){markFailure=value;},
    async mount(){await act(async()=>{renderer=create(React.createElement(App),{createNodeMock:()=>({scrollTo(){}})});});await settle();
      const button=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children==='Capturar'));
      await act(async()=>button.props.onPress());await settle();},
    props(){return renderer.root.findByType('CapturePanelStub').props;},
    drafts(){return renderer.root.findByType('DraftManagerStub').props;},
    button(label){return renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label)).props.onPress;},
    async press(label){const button=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(button,label);await act(async()=>button.props.onPress());await settle();},
    async fill(){await act(async()=>{this.props().onMerchant('Loja sintética');this.props().onAmount('10,00');});},
    settle,async unmount(){if(renderer){await act(async()=>renderer.unmount());renderer=null;}}};
}
async function check(name,work){const h=harness();try{await work(h);console.log('PASS',name);}catch(error){failures++;console.error('FAIL',name,error.message);}finally{await h.unmount();}}
(async()=>{
  await check('Native evidence enters the local queue and count before a failing health probe',async h=>{
    const event={event_id:'11111111-1111-4111-8111-111111111111',source_type:'android_notification',source_provider:'com.nu.production',captured_at:'2026-10-09T10:00:00.000Z',raw_payload:{title:'Compra aprovada'},parsed_payload:{amount:8}};
    h.nativeEvents=[event];h.healthFailure=true;await h.mount();
    await act(async()=>{await assert.rejects(h.io.prepare(),/server offline/);});await h.settle();
    assert.ok(h.nativeReads>0);assert.equal(h.rows.length,1);assert.equal(h.rows[0].event_id,event.event_id);assert.equal(h.props().queueCount,1);assert.equal(h.nativeMarks.length,0,'Local import does not mark native source synced');
  });
  await check('Count failure after durable capture ACK cannot report a failed save',async h=>{
    await h.mount();await h.fill();h.failCount=true;await act(async()=>h.props().onSave());await h.settle();
    assert.equal(h.rows.length,1);assert.equal(h.props().error,'');assert.match(h.props().message,/salva no aparelho/);
  });
  await check('Discarding a manual capture clears its fields and voice draft without inserting or deleting financial commands',async h=>{
    await h.mount();await h.fill();await act(async()=>{h.props().onKind('own_transfer');h.props().onOrigin('origin');h.props().onDestination('destination');h.props().onDate('06/10/2026');});
    await act(async()=>h.gate.runUserAction(()=>h.props().onDiscard()));await h.settle();
    assert.equal(h.props().merchant,'');assert.equal(h.props().amount,'');assert.equal(h.props().kind,'purchase');assert.equal(h.props().date,'');assert.equal(h.props().origin,'');assert.equal(h.props().destination,'');
    assert.equal(h.rows.length,0);assert.equal(h.deletes.length,2);assert.equal(h.deletes[0].sql,'DELETE FROM batch_drafts WHERE batch_id=?');assert.equal(h.deletes[0].args[0],'manual-capture');assert.equal(h.deletes[1].sql,'DELETE FROM voice_drafts WHERE draft_key=?');assert.equal(h.deletes[1].args[0],'capture');
  });
  await check('A retired manual draft deletion callback leaves local storage untouched',async h=>{
    await h.mount();await h.fill();const stale=h.props().onDiscard;await h.unmount();await stale();assert.equal(h.deletes.length,0);assert.equal(h.rows.length,0);
  });
  await check('The drafts manager is reachable from More and returns there without touching financial storage',async h=>{
    await h.mount();await h.press('Mais');await h.press('Rascunhos');assert.equal(typeof h.drafts().onBack,'function');
    await act(async()=>h.drafts().onBack());await h.settle();await h.press('Rascunhos');await act(async()=>h.drafts().onPendingCreation());await h.settle();
    assert.equal(h.rows.length,0);assert.equal(h.deletes.length,0);
  });
  await check('A local capture releases its gate while older remote uploads are still pending',async h=>{
    await h.mount();await h.fill();const pending=deferred();h.sync=()=>pending.promise;let task;
    act(()=>{task=h.gate.runUserAction(()=>h.props().onSave());});await h.settle();
    try{assert.equal(h.rows.length,1);assert.equal(h.props().busy,false);assert.equal(h.gate.isUserOperationBusy(),false);}
    finally{await act(async()=>{pending.resolve();await task;});}
  });
  await check('A retired capture callback cannot insert a new financial event',async h=>{
    await h.mount();await h.fill();const stale=h.props().onSave;await h.unmount();await stale();assert.equal(h.rows.length,0);assert.equal(h.alerts.length,0);
  });
  await check('Suspension after server ACK is an interruption rather than a new 401 failure',async h=>{
    await h.mount();assert.equal(await h.io.prepare(),true);h.token='';
    assert.throws(()=>h.io.done({kind:'event',event_id:'synthetic'},{}),error=>error.name==='AbortError');
  });
  await check('A permission result cannot open a camera after its screen was disposed',async h=>{
    await h.mount();const pending=deferred();h.permission=pending.promise;let task;act(()=>{task=h.props().onPhoto();});await h.settle();
    await h.unmount();pending.resolve({granted:true});await task;assert.equal(h.cameraCalls,0);
  });
  const seedNative=h=>h.rows.push({id:1,event_id:'native-synthetic',kind:'native_event',payload:'{}',local_uri:null,filename:null,mime_type:null,attempt_count:1,last_error:'synthetic quarantine'});
  async function discardDialog(h){await h.mount();await act(async()=>h.io.changed(0,''));await h.settle();await h.press('Mais');await h.press('Ajustes');await h.press('Excluir este item');return h.alerts.at(-1)[2].find(button=>button.style==='destructive').onPress;}
  await check('A late native discard cannot acknowledge a replaced local row',async h=>{
    seedNative(h);const discard=await discardDialog(h);h.rows[0]={...h.rows[0],id:2,payload:'{"changed":true}'};
    await assert.rejects(discard(),/mudou/);assert.equal(h.nativeMarks.length,0);assert.equal(h.deletes.length,0);
  });
  await check('Native retirement failure preserves the local row for explicit retry',async h=>{
    seedNative(h);const discard=await discardDialog(h);h.markFailure=true;await assert.rejects(discard(),/native commit failure/);
    assert.equal(h.rows.length,1);assert.equal(h.deletes.length,0);
  });
  await check('A failed receipt leaves the local queue when it is excluded',async h=>{
    h.rows.push({id:3,event_id:'receipt-synthetic',kind:'document',payload:'{}',local_uri:'file://receipt.jpg',filename:'MG-synthetic.jpg',mime_type:'image/jpeg',attempt_count:1,last_error:'Este arquivo não foi reconhecido como extrato.'});
    const discard=await discardDialog(h);
    await act(async()=>discard());await h.settle();
    assert.equal(h.rows.length,0);
    assert.equal(h.deletes.some(item=>String(item.sql).startsWith('DELETE FROM event_queue')),true);
  });
  await check('A stale local retry cannot clear the replacement row failure state',async h=>{
    seedNative(h);await h.mount();await act(async()=>h.io.changed(0,''));await h.settle();await h.press('Mais');await h.press('Ajustes');
    const retry=h.button('Tentar este item novamente');h.rows[0]={...h.rows[0],id:2,payload:'{"changed":true}'};
    await act(async()=>retry({}));await h.settle();assert.equal(h.deletes.length,0);assert.equal(h.rows[0].attempt_count,1);
  });
  if(failures)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
