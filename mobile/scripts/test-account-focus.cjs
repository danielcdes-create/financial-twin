// Real AuthProvider, AccountPositions, session client and native Alert callbacks.
// Only native focus events and HTTP responses are synthetic.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
const {EventEmitter} = require('node:events');
global.IS_REACT_ACT_ENVIRONMENT = true;
const events = new EventEmitter(), requests = [], cache = new Map();
let rows = [], renderer, nativeCalls=0;
const native = {Platform: {OS: 'android'}, BackHandler: {addEventListener:()=>({remove(){}})}, AppState: {currentState: 'active',
  addEventListener: (name, fn) => {events.on(name, fn); return {remove: () => events.off(name, fn)};}},
  Alert: {alert: () => {nativeCalls++;}}, StyleSheet: {create: value => value}, Keyboard: {dismiss() {}},
  FlatList: ({data, renderItem, keyExtractor}) => React.createElement('View', null, data.map((item,index) => React.createElement(React.Fragment, {key:keyExtractor?keyExtractor(item,index):index}, renderItem({item})))),
  ...Object.fromEntries(['ActivityIndicator','KeyboardAvoidingView','Modal','Pressable','ScrollView','Text','TextInput','View'].map(name => [name,name]))};
const mocks = {'react': React, 'react-native': native,
  'react-native-safe-area-context': {SafeAreaView:'SafeAreaView'},
  '../utils/useScreenPrivacy': {useScreenPrivacy() {}},
  '../utils/biometricSession': {removeLegacySession:async()=>{}, biometricAvailable:async()=>false, biometricEnabled:async()=>false}};
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports:{}};
  cache.set(file,module.exports);
  const override = process.env.ACCOUNT_FOCUS_ORIGINAL;
  const source = override && ['authState.ts','apiClient.ts','sessionClient.ts'].includes(path.basename(file))
    ? path.join(override,'mobile','utils',path.basename(file)) : file;
  const code = ts.transpileModule(fs.readFileSync(source,'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  vm.runInNewContext(code,{module,exports:module.exports,Error,Date,URL,Headers,FormData,AbortController,setTimeout,clearTimeout,
    process:{env:{EXPO_PUBLIC_API_BASE_URL:'https://test.invalid'}}, fetch:async(url,init)=>{
      const route = new URL(url).pathname, method = init.method || 'GET';
      requests.push({route,method,token:init.headers.get('Authorization')});
      if (route === '/v1/auth/status') return new Response(JSON.stringify({configured:true,setup_required:false}));
      if (route === '/v1/auth/session') return new Response(JSON.stringify({access_token:'synthetic-session',expires_in:43200,account:{username:'test',display_name:'Teste'}}));
      assert.equal(init.headers.get('Authorization'),'Bearer synthetic-session');
      if (route === '/v1/account-positions') return new Response(JSON.stringify({items:rows}));
      if (route === '/v1/account-profiles' && method === 'POST') {
        const body = JSON.parse(init.body);
        assert.equal(body.tipo,'pagamento'); assert.match(body.request_id,/^[0-9a-f-]{36}$/);
        rows = [{...body,key:'test_account',revision:1,balance:null,movement_count:0}];
        return new Response(JSON.stringify({item:rows[0]}),{status:201});
      }
      if (route.endsWith('/deletion')) return new Response(JSON.stringify({can_delete:true,revision:1}));
      if (route === '/v1/account-profiles/test_account' && method === 'DELETE') {
        assert.equal(JSON.parse(init.body).expected_revision,1); rows=[];
        return new Response('{}');
      }
      throw new Error(`Unexpected request ${method} ${route}`);
    },require:name=>{
      if (mocks[name]) return mocks[name];
      if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file),name),'utf8'));
      if (name.startsWith('.')) {const relative = path.resolve(path.dirname(file),name);return load(fs.existsSync(relative+'.tsx')?relative+'.tsx':relative+'.ts');}
      throw new Error(`Unexpected module ${name}`);
    }},{filename:file});
  cache.set(file,module.exports);return module.exports;
}
const {default:AuthProvider,useAuth} = load('components/AuthProvider.tsx');
const {authState,api} = load('utils/sessionClient.ts');
const Accounts = load('components/AccountPositions.tsx').default;
const Host=load('components/AppDialogHost.tsx').default, dialogs=load('utils/appAlert.ts');
const dialogButton=label=>renderer.root.findByType(Host).findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label)).props.onPress;
const dismiss=label=>act(async()=>dialogButton(label)());
function Screen() {const auth=useAuth();return auth.authenticated?React.createElement(Accounts,{api:api.json,money:value=>`R$ ${value.toFixed(2)}`}):auth.loginScreen;}
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,10));});
const press=label=>act(async()=>{const button=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label)||node.props.accessibilityLabel===label);assert.ok(button,`Button ${label}`);button.props.onPress();});
const input=(label,text)=>act(async()=>renderer.root.findByProps({accessibilityLabel:label}).props.onChangeText(text));
const emit=(name,state)=>act(async()=>{if(name==='change')native.AppState.currentState=state;events.emit(name,state);});
const count=method=>requests.filter(item=>item.method===method).length;
(async()=>{
  await act(async()=>{renderer=create(React.createElement(AuthProvider,null,React.createElement(Screen),React.createElement(Host)));}); await settle();
  await input('Usuário','test');await input('Senha','test-password');await press('Entrar');await settle();
  assert.equal(authState.unlocked(),true);
  await press('Adicionar conta');await input('Instituição','Conta sintética');
  await press('Selecionar Tipo de conta');await press('Escolher Pagamento');
  // A form callback can run before the native focus-restored event is delivered.
  if (!process.env.DELETE_FOCUS_ONLY) await emit('blur');
  await press('Salvar cadastro');await settle();
  if (!process.env.DELETE_FOCUS_ONLY) assert.equal(count('POST'),1,'Covered form must not send a private create');
  if (!rows.length && process.env.ACCOUNT_FOCUS_ORIGINAL) console.log('Reproduced UI error:', renderer.root.findAllByType('Text').map(node=>node.props.children).filter(value=>typeof value==='string'&&value.includes('Entre')));
  await emit('focus');await settle();
  assert.equal(rows.length,1,'Create must finish when the same session regains focus');
  assert.equal(count('POST'),2,'Create is sent exactly once after password login');
  assert.equal(dialogs.dialogSnapshot().title,'Conta salva');assert.equal(nativeCalls,0);
  await dismiss('OK');await settle();
  await press('Detalhes de Conta sintética');await press('Excluir conta');await settle();
  assert.equal(dialogs.dialogSnapshot().title,'Excluir conta');
  const stale=dialogButton('Excluir');
  await emit('blur');
  await act(async()=>stale());await settle();
  assert.equal(count('DELETE'),0,'Confirmation callback must wait while covered');
  assert.equal(authState.unlocked(),true);
  await emit('focus');await settle();
  assert.equal(rows.length,1,'External blur discards the old confirmation without replay');
  await press('Excluir conta');await settle();await dismiss('Excluir');await settle();
  if (rows.length && process.env.ACCOUNT_FOCUS_ORIGINAL) console.log('Reproduced deletion UI error:', renderer.root.findAllByType('Text').map(node=>node.props.children).filter(value=>typeof value==='string'&&value.includes('Entre')));
  assert.equal(rows.length,0,'Confirmed deletion completes after focus');assert.equal(count('DELETE'),1);
  assert.equal(dialogs.dialogSnapshot().title,'Conta excluída');
  await dismiss('OK');await settle();
  assert.ok(!renderer.root.findAllByType('Text').some(node=>String(node.props.children).includes('Entre na sua conta')));
  // Explicit locking must discard waiting work, even after login again.
  await press('Adicionar conta');await input('Instituição','Cancelada');
  await emit('blur');await press('Salvar cadastro');await settle();
  await emit('change','background');
  assert.equal(authState.unlocked(),true,'Briefly minimizing keeps the session');
  // Activity resume and Android window focus are independent events. Complete
  // the actual foreground return before interacting with the covered login.
  await act(async()=>authState.lock());await emit('change','active');await emit('focus');await settle();
  assert.equal(count('POST'),2,'Explicit lock cancels the pending creation');
  assert.equal(authState.unlocked(),false);
  await input('Usuário','test');await input('Senha','test-password');await press('Entrar');await settle();
  assert.equal(count('POST'),3,'New login never replays old account work');
  const controller = new AbortController();await emit('blur');
  const abortedWrite = api.json('/v1/account-profiles',{method:'POST',body:'{}',signal:controller.signal});
  const result=assert.rejects(abortedWrite,error=>error.name==='AbortError');controller.abort();await result;
  await emit('focus');await settle();assert.equal(count('POST'),3,'Canceled waiting request is never sent');
  await emit('blur');
  const replacedSessionWrite=api.json('/v1/account-profiles',{method:'POST',body:JSON.stringify({instituicao:'Cancelada',tipo:'pagamento',request_id:'00000000-0000-4000-8000-000000000000'})});
  const replacedResult=assert.rejects(replacedSessionWrite,error=>error.name==='AbortError');
  await act(async()=>{events.emit('focus');authState.lock();authState.activate('synthetic-session',Date.now()+60_000);});
  await replacedResult;assert.equal(count('POST'),3,'A session replaced before the focus callback is consumed never admits old work, even with the same token');
  rows=[{key:'reference_date',instituicao:'Sem Parar sintético',tipo:'pagamento',status:'aberta',moeda:'BRL',
    balance:70.07,balance_as_of:'2026-10-03T03:00:00+00:00',balance_reference_date:'2026-10-02',movement_count:0}];
  await act(async()=>renderer.update(React.createElement(AuthProvider,null,React.createElement(Screen,{key:'date'}))));await settle();
  const labels=renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join(''));
  assert.ok(labels.some(label=>label.includes('Referência: 02/10/2026')),'Display the informed civil date, independent of the cutoff instant/timezone');
  assert.ok(!labels.some(label=>label.includes('03/10/2026')));
  await act(async()=>renderer.unmount());assert.equal(events.eventNames().length,0);
  console.log('PASS: real account form create/delete across Android focus race; no covered network, no duplicate write, background/login and AbortSignal cancel pending work; civil reference date preserved.');
})().catch(async error=>{console.error(error);process.exitCode=1;await act(async()=>renderer?.unmount());});
