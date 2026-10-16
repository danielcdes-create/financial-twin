// Real overlay/choice/dialog modules. All data, native widgets and events are
// synthetic; browser layout and physical Android are checked separately.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const deferred=()=>{let resolve;const promise=new Promise(yes=>{resolve=yes});return {promise,resolve};};
let failures=0;
function harness(){
  const cache=new Map();
  const native={Platform:{OS:'android'},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},
    BackHandler:{addEventListener:()=>({remove(){}})},Alert:{alert(){}},StyleSheet:{create:x=>x},
    FlatList:({data,renderItem})=>React.createElement('View',null,data.map(item=>React.createElement(React.Fragment,{key:item.value},renderItem({item})))),
    ...Object.fromEntries(['View','Text','Pressable','TextInput','ScrollView','Modal','ActivityIndicator'].map(name=>[name,name]))};
  function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);
    const previous=process.env.OVERLAY_ORIGINAL&&path.join(process.env.OVERLAY_ORIGINAL,'mobile',path.relative(process.cwd(),file));
    const source=previous&&fs.existsSync(previous)?previous:file;
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
      {module:m,exports:m.exports,console,Error,Symbol,Promise,require:name=>{if(name==='react')return React;if(name==='react-native')return native;
        if(name==='react-native-safe-area-context')return {SafeAreaView:'View'};const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:source});
    cache.set(file,m.exports);return m.exports;
  }
  const Provider=load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider,Choice=load('components/ControlledChoice.tsx').default;
  const dialogs=load('utils/appAlert.ts'),gate=load('utils/userOperation.ts');
  let renderer;
  return {dialogs,gate,load,async mount(onChange){await act(async()=>{renderer=create(React.createElement(Provider,null,
    React.createElement(Choice,{label:'Categoria sintética',value:'A',options:['A','B'],onChange})));});},
    button(label){const node=renderer.root.findAllByType('Pressable').find(item=>item.props.accessibilityLabel===label);assert.ok(node,label);return node.props.onPress;},
    async press(label){await act(async()=>this.button(label)({}));},async unmount(){if(renderer)await act(async()=>renderer.unmount());dialogs.clearDialogs();}};
}
async function check(name,work){const h=harness();try{await work(h);console.log('PASS',name);}catch(error){failures++;console.error('FAIL',name,error.message);}finally{await h.unmount();}}
(async()=>{
  await check('A late option from a closed selector cannot consume the reopened selector',async h=>{
    const chosen=[];await h.mount(value=>chosen.push(value));await h.press('Selecionar Categoria sintética');const stale=h.button('Escolher A');
    await h.press('Fechar opções');await h.press('Selecionar Categoria sintética');await act(async()=>stale({}));
    assert.deepEqual(chosen,[]);await h.press('Escolher B');assert.deepEqual(chosen,['B']);
  });
  await check('A late close cannot dismiss a new selector request from the same owner',async h=>{
    await h.mount(()=>{});await h.press('Selecionar Categoria sintética');const stale=h.button('Fechar opções');await h.press('Fechar opções');
    await h.press('Selecionar Categoria sintética');await act(async()=>stale({}));assert.ok(h.button('Escolher B'));
  });
  await check('An asynchronous selection keeps the operation gate until its completion',async h=>{
    const pending=deferred();let task;await h.mount(()=>pending.promise);await h.press('Selecionar Categoria sintética');
    act(()=>{task=h.button('Escolher A')({});});try{assert.equal(h.gate.isUserOperationBusy(),true);let competing=0;
      h.gate.runUserAction(()=>competing++);assert.equal(competing,0);
    }finally{await act(async()=>{pending.resolve();await task;});}assert.equal(h.gate.isUserOperationBusy(),false);
  });
  await check('Busy dialog callbacks are discarded at the shared entry point without replay',async h=>{
    const pending=deferred();let calls=0;h.dialogs.Alert.alert('Sintético','',[{text:'Continuar',onPress:()=>calls++}]);
    // The explicit owner task starts before presenting its confirmation.
    h.dialogs.clearDialogs();const task=h.gate.runUserAction(()=>pending.promise);h.dialogs.Alert.alert('Sintético','',[{text:'Continuar',onPress:()=>calls++}]);
    const dialog=h.dialogs.dialogSnapshot();h.dialogs.dismissDialog(dialog.buttons[0],dialog);
    try{assert.equal(h.dialogs.dialogSnapshot(),dialog,'The busy tap must leave the confirmation open');}
    finally{pending.resolve();await task;}
    assert.equal(calls,0,'No callback is replayed after completion');h.dialogs.dismissDialog(dialog.buttons[0],dialog);assert.equal(calls,1);
  });
  await check('Ownership is invalidated during the commit before later layout callbacks',async h=>{
    const {useAsyncScope}=h.load('utils/useAsyncScope.ts');let previous;const seen=[];
    function Scope({owner}){const capture=useAsyncScope(owner);React.useEffect(()=>{previous=capture();},[capture]);React.useLayoutEffect(()=>{if(previous)seen.push(previous());},[owner]);return null;}
    let renderer;await act(async()=>{renderer=create(React.createElement(Scope,{owner:'first'}));});
    try{await act(async()=>renderer.update(React.createElement(Scope,{owner:'second'})));assert.deepEqual(seen,[false]);}
    finally{await act(async()=>renderer.unmount());}
  });
  await check('A request from an unmounted owner is rejected before transport starts',async h=>{
    const {useAsyncScope,useScopedRequest}=h.load('utils/useAsyncScope.ts');let request,sent=0;
    const transport=async()=>{sent++;return 'synthetic';};
    function Scope(){request=useScopedRequest(transport,useAsyncScope());return null;}
    let renderer;await act(async()=>{renderer=create(React.createElement(Scope));});await act(async()=>renderer.unmount());
    await assert.rejects(request(),error=>error.name==='AbortError');assert.equal(sent,0);
  });
  await check('Selector callbacks are invalidated in the owner unmount commit',async h=>{
    const Provider=h.load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider,Choice=h.load('components/ControlledChoice.tsx').default;
    let renderer,stale;const chosen=[];
    function Frame({show}){React.useLayoutEffect(()=>{if(!show)stale({});},[show]);return React.createElement(Provider,null,
      show&&React.createElement(Choice,{label:'Owner commit',value:'',options:['A'],onChange:value=>chosen.push(value)}));}
    await act(async()=>{renderer=create(React.createElement(Frame,{show:true}));});
    const button=label=>renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label).props.onPress;
    try{await act(async()=>button('Selecionar Owner commit')({}));stale=button('Escolher A');
      await act(async()=>renderer.update(React.createElement(Frame,{show:false})));assert.deepEqual(chosen,[]);
    }finally{await act(async()=>renderer.unmount());}
  });
  if(failures)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
