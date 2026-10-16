// Production category screens, auth, API, operation gate and dialog host.
// Android focus and transport are simulated; no real financial data is written.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
const {EventEmitter} = require('node:events');
global.IS_REACT_ACT_ENVIRONMENT = true;
const events = new EventEmitter(), cache = new Map(), nativeDialogs = [], writes = [], backHandlers = [];
let renderer, pendingWrite, conflict = false, reads = 0;
let rows = [{name:'estacionamento',usage_count:3,token:'v1'}, {name:'mobilidade',usage_count:1,token:'v1'}, {name:'Transporte por Aplicativo',usage_count:31,token:'v1'}];
let categories = [{name:'Transporte',usage_count:35,subcategory_count:rows.length},{name:'Turismo',usage_count:0,subcategory_count:0}];
const native = {Platform:{OS:'android'}, AppState:{currentState:'active',addEventListener:(name,fn)=>{events.on(name,fn);return{remove:()=>events.off(name,fn)};}},
  BackHandler:{addEventListener:(_name,fn)=>{backHandlers.push(fn);return{remove:()=>backHandlers.splice(backHandlers.indexOf(fn),1)};}},
  Alert:{alert:(...args)=>nativeDialogs.push(args)},Keyboard:{dismiss(){}},StyleSheet:{create:x=>x},
  ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator','ScrollView','KeyboardAvoidingView','Modal'].map(name=>[name,name]))};
const mocks = {'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'View'},
  '../utils/useScreenPrivacy':{useScreenPrivacy(){}},'../utils/biometricSession':{removeLegacySession:async()=>{},biometricAvailable:async()=>false,biometricEnabled:async()=>false}};
const reply = value => new Response(JSON.stringify(value));
function load(file) {
  file = path.resolve(file); if(cache.has(file))return cache.get(file);
  const m={exports:{}};cache.set(file,m.exports);
  const before=process.env.CATEGORY_ORIGINAL;
  const original=before&&path.join(before,path.basename(file));
  const source=original&&fs.existsSync(original)?original:file;
  const code=ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  vm.runInNewContext(code,{module:m,exports:m.exports,console,Error,Date,URL,URLSearchParams,Headers,FormData,AbortController,setTimeout,clearTimeout,
    process:{env:{EXPO_PUBLIC_API_BASE_URL:'https://categories.invalid'}},fetch:async(url,init)=>{
      const route=new URL(url).pathname,method=init.method||'GET';
      if(route==='/v1/auth/status')return reply({configured:true});
      assert.equal(init.headers.get('Authorization'),'Bearer synthetic');
      if(method==='GET') {
        reads++;
        if(route==='/v1/categories')return reply({details:categories});
        return reply({details:rows});
      }
      writes.push({route,method,body:init.body&&JSON.parse(init.body),query:new URL(url).searchParams});
      if(conflict){conflict=false;rows=rows.map(row=>({...row,token:'v3'}));return new Response('{"detail":"Dados alterados"}',{status:409});}
      return new Promise((resolve,reject)=>{
        const cancel=()=>reject(Object.assign(new Error('Consulta cancelada'),{name:'AbortError'}));
        init.signal.addEventListener('abort',cancel,{once:true});
        pendingWrite=()=>{init.signal.removeEventListener('abort',cancel);
          if(method==='PATCH'){const body=JSON.parse(init.body);rows=rows.map(row=>row.name===body.name?{...row,name:body.new_name,token:'v2'}:row);}
          if(method==='DELETE'&&route.endsWith('subcategories'))rows=rows.filter(row=>row.name!==new URL(url).searchParams.get('name'));
          if(method==='DELETE'&&route==='/v1/categories')categories=categories.filter(row=>row.name!==new URL(url).searchParams.get('name'));
          resolve(reply({persisted:true}));};
      });
    },require:name=>{
      if(mocks[name])return mocks[name];
      if(name.endsWith('.json'))return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file),name),'utf8'));
      if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}
      throw new Error(name);
    }},{filename:file});cache.set(file,m.exports);return m.exports;
}
const {default:AuthProvider}=load('components/AuthProvider.tsx'),{authState,api}=load('utils/sessionClient.ts');
const Category=load('components/CategoryManager.tsx').default,Subcategory=load('components/SubcategoryManager.tsx').default;
const Boundary=load('components/UserOperationBoundary.tsx').default,Host=load('components/AppDialogHost.tsx').default;
const dialogs=load('utils/appAlert.ts'),gate=load('utils/userOperation.ts');
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,15));});
const button=label=>{const surface=dialogs.dialogSnapshot()?renderer.root.findByType(Host):renderer.root;const node=surface.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(node,label);return node;};
const press=label=>act(async()=>{const node=button(label);assert.ok(!node.props.disabled,label+' enabled');node.props.onPress();});
const input=(label,value)=>act(async()=>renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel===label).props.onChangeText(value));
const emit=(name,state)=>act(async()=>{if(name==='change')native.AppState.currentState=state;events.emit(name,state);});
const finish=()=>act(async()=>pendingWrite());
async function startRename(name,value){await press('Editar subcategoria '+name);await input('Novo nome da subcategoria',value);await press('Salvar');}
(async()=>{
  authState.activate('synthetic',Date.now()+3600000);
  await act(async()=>{renderer=create(React.createElement(AuthProvider,null,
    React.createElement(Boundary,null,React.createElement(Category,{api:api.json,money:()=> 'R$ 0,00',spending:[],onUpdated:()=>new Promise(()=>{})})),React.createElement(Host)));});
  await settle();await press('Subcategorias de Transporte');await settle();
  await startRename('estacionamento','Estacionamento');
  if(process.env.CATEGORY_REPRODUCE){
    await act(async()=>nativeDialogs.at(-1)[2].find(button=>button.text==='Renomear').onPress());await settle();
    assert.equal(writes.length,1);await emit('blur');await settle();
    assert.equal(nativeDialogs.at(-1)[0],'Não foi possível salvar a subcategoria');
    assert.equal(nativeDialogs.at(-1)[1],'Consulta cancelada');
    console.log('REPRODUCED: confirmation callback starts PATCH before delayed Android blur; session suspension cancels the write and reports Consulta cancelada.');
    return;
  }
  assert.equal(nativeDialogs.length,0,'Category confirmation stays in the Activity and cannot blur its own session');
  assert.ok(authState.getToken());await press('Renomear');await settle();
  assert.equal(writes.length,1);assert.equal(gate.isUserOperationBusy(),true);
  act(()=>{button('Criar').props.onPress();button('Subcategorias de Transporte').props.onPress();});
  assert.equal(writes.length,1,'Repeated/busy taps are discarded');
  await finish();await settle();
  assert.equal(dialogs.dialogSnapshot().title,'Subcategoria salva');
  assert.equal(gate.isUserOperationBusy(),false,'Secondary dashboard refresh cannot hold the write gate');
  await press('OK');await startRename('Estacionamento','Estacionamento coberto');
  const staleConfirmation=button('Renomear').props.onPress;
  await emit('blur');await emit('focus');await settle();
  await act(async()=>staleConfirmation());await settle();
  assert.equal(dialogs.dialogSnapshot(),null,'External blur discards the in-Activity confirmation');
  assert.equal(writes.length,1,'Returning never replays a discarded confirmation');
  assert.equal(renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel==='Novo nome da subcategoria').props.value,'Estacionamento coberto','Draft survives interruption');
  await press('Salvar');await press('Renomear');await settle();await finish();await settle();await press('OK');
  await press('Excluir subcategoria mobilidade');await input('Buscar subcategoria substituta','Transporte por Aplicativo');
  await press('Substituir e excluir');await press('Confirmar');await settle();
  assert.equal(writes.at(-1).query.get('replacement'),'Transporte por Aplicativo');await finish();await settle();await press('OK');
  assert.ok(!rows.some(row=>row.name==='mobilidade'));
  // Conflict reload must replace the obsolete token while retaining edited text.
  await startRename('Estacionamento coberto','Novo nome preservado');conflict=true;await press('Renomear');await settle();
  await press('OK');assert.equal(renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel==='Novo nome da subcategoria').props.value,'Novo nome preservado');
  await press('Salvar');await press('Renomear');await settle();assert.equal(writes.at(-1).body.expected_token,'v3');await finish();await settle();await press('OK');
  await press('Excluir subcategoria Novo nome preservado');await press('Cancelar');assert.equal(dialogs.dialogSnapshot(),null);
  await startRename('Novo nome preservado','Nome cancelado');act(()=>backHandlers.at(-1)());await settle();assert.equal(dialogs.dialogSnapshot(),null,'Android Back cancels Activity confirmation without writing');await press('Cancelar');
  await act(async()=>{const row=renderer.root.findAllByType('View').find(node=>node.findAllByType('Text').some(text=>text.props.children==='Turismo')&&node.findAllByType('Pressable').filter(button=>button.findAllByType('Text').some(text=>text.props.children==='Excluir')).length===1);row.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children==='Excluir')).props.onPress();});
  await press('Excluir');await settle();await finish();await settle();assert.equal(dialogs.dialogSnapshot().title,'Categoria removida');await press('OK');
  assert.ok(!categories.some(row=>row.name==='Turismo'));assert.equal(gate.isUserOperationBusy(),false);
  await act(async()=>renderer.unmount());renderer=null;
  // Ignore late responses from a previous category and invalidate on unmount.
  let resolveOld,resolveCurrent;
  const controlled=category=>new Promise(resolve=>{if(category.includes('Anterior'))resolveOld=resolve;else resolveCurrent=resolve;});
  const props={api:controlled,disabled:false,onBusyChange:()=>{},onUpdated:async()=>{}};
  await act(async()=>{renderer=create(React.createElement(Subcategory,{...props,category:'Anterior'}));});await settle();
  await act(async()=>renderer.update(React.createElement(Subcategory,{...props,category:'Atual'})));await settle();
  await act(async()=>resolveCurrent({details:[{name:'Atual',usage_count:0,token:'current'}]}));
  await act(async()=>resolveOld({details:[{name:'Antiga',usage_count:0,token:'old'}]}));
  assert.ok(renderer.root.findAllByType('Text').some(node=>node.props.children==='Atual'));
  assert.ok(!renderer.root.findAllByType('Text').some(node=>node.props.children==='Antiga'));
  await act(async()=>renderer.unmount());renderer=null;
  let interrupted=true;
  await act(async()=>{renderer=create(React.createElement(Subcategory,{...props,category:'Transporte',api:async()=>{if(interrupted)throw Object.assign(new Error('Consulta cancelada'),{name:'AbortError'});return{details:rows};}}));});await settle();
  assert.ok(renderer.root.findAllByType('Text').some(node=>String(node.props.children).includes('Atualização interrompida')));
  interrupted=false;await press('Tentar novamente');await settle();assert.equal(button('Editar subcategoria Novo nome preservado').props.disabled,false);
  console.log('PASS: category Activity confirmations, focus interruption without replay, rename/delete, draft and conflict-token recovery, ACK gate release and obsolete reads; '+reads+' synthetic reads.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await act(async()=>{dialogs.clearDialogs();if(renderer)renderer.unmount();});});
