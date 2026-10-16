const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),system=[];
const native={Platform:{OS:'android'},Alert:{alert:(...args)=>system.push(args)},Keyboard:{dismiss(){}},BackHandler:{addEventListener:()=>({remove(){}})},StyleSheet:{create:x=>x},...Object.fromEntries(['View','Text','ScrollView','Pressable','Modal','ActivityIndicator'].map(name=>[name,name]))};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);const candidate=process.env.STATE_TRANSITIONS_ORIGINAL&&path.join(process.env.STATE_TRANSITIONS_ORIGINAL,path.relative(process.cwd(),file));const source=candidate&&fs.existsSync(candidate)?candidate:file;
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,{module:m,exports:m.exports,console,Error,require:name=>{if(name==='react')return React;if(name==='react-native')return native;const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:source});cache.set(file,m.exports);return m.exports;}
const dialogs=load('utils/appAlert.ts'),gate=load('utils/userOperation.ts'),Host=load('components/AppDialogHost.tsx').default;
(async()=>{
  let renderer,release,calls=0;await act(async()=>{renderer=create(React.createElement(Host));});
  await act(async()=>{dialogs.Alert.alert('Excluir cartão?','Synthetic',[{text:'Cancelar',style:'cancel'},{text:'Excluir',style:'destructive',onPress:()=>calls++}]);});
  assert.equal(system.length,0,'Every ordinary app Alert stays in the Activity');
  const button=label=>renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label));
  const stale=button('Excluir').props.onPress;await act(async()=>dialogs.clearDialogs({preserveNative:true}));await act(async()=>stale());assert.equal(calls,0,'External blur invalidates hosted callbacks');
  await act(async()=>{gate.runUserAction(()=>{dialogs.Alert.alert('Preview','Synthetic',[{text:'Confirmar',onPress:()=>calls++}]);return new Promise(resolve=>{release=resolve;});});});
  const confirm=button('Confirmar');assert.equal(confirm.props.disabled,true);await act(async()=>confirm.props.onPress());assert.equal(calls,0);
  await act(async()=>release());assert.equal(calls,0,'Busy dialog taps are discarded, never queued or replayed');assert.equal(button('Confirmar').props.disabled,false);
  await act(async()=>button('Confirmar').props.onPress());assert.equal(calls,1);assert.equal(dialogs.dialogSnapshot(),null);assert.equal(gate.isUserInteractionBlocked(),false);
  await act(async()=>renderer.unmount());console.log('PASS Shared in-Activity dialogs, invalidated callbacks and busy taps without replay; native focus is simulated.');
})().catch(error=>{console.error(error);process.exitCode=1;});
