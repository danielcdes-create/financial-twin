// Real providers/client; controlled OS events, clock, secure prompts and HTTP.
// No owner credentials, finance writes, or physical device are used.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer'),{EventEmitter}=require('node:events');
global.IS_REACT_ACT_ENVIRONMENT=true;
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,10));});
const failures=[];
function harness({biometric=false,security=false}={}){
  const cache=new Map(),events=new EventEmitter(),timers=new Map(),prompts=[],requests=[];
  let clock=Date.now(),renderer,current,accountWrites=0,request,fetchDelegate;
  class TestDate extends Date{static now(){return clock;}}
  const native={Platform:{OS:'android'},AppState:{currentState:'active',addEventListener:(event,listener)=>{events.on(event,listener);return{remove:()=>events.off(event,listener)};}},Keyboard:{dismiss(){}},StyleSheet:{create:value=>value},
    ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator','KeyboardAvoidingView','ScrollView'].map(name=>[name,name]))};
  const mocks={'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'SafeAreaView'},'../utils/useScreenPrivacy':{useScreenPrivacy(){}},'../utils/appAlert':{clearDialogs(){},Alert:{alert(){}}},
    '../utils/biometricSession':{removeLegacySession:async()=>{},biometricAvailable:async()=>biometric,biometricEnabled:async()=>biometric,
      readBiometricSession:()=>{const prompt=deferred();prompts.push(prompt);return prompt.promise;},clearBiometricSession:async()=>{},updateBiometricSession:async()=>{},saveBiometricSession:async()=>{}}};
  if(security){mocks['../utils/sessionClient']={api:{json:(...args)=>request(...args)}};mocks['./AuthProvider']={useAuth:()=>({account:{username:'synthetic',display_name:'Synthetic'},setAccount(){accountWrites++;},replaceSession:async()=>{},biometricOn:false,biometricSupported:false}),
    AuthButton:({title,onPress,disabled})=>React.createElement('Pressable',{accessibilityLabel:title,onPress,disabled},React.createElement('Text',null,title)),authStyles:{},deviceLabel:'synthetic'};}
  const testTimeout=(callback,delay)=>{if(delay===0)return setTimeout(callback,0);const id={};timers.set(id,{callback,at:clock+delay});return id;};
  const clear=id=>{if(!timers.delete(id))clearTimeout(id);};
  function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const module={exports:{}};cache.set(file,module.exports);
    const candidate=process.env.AUTH_TRANSITIONS_ORIGINAL&&path.join(process.env.AUTH_TRANSITIONS_ORIGINAL,path.relative(process.cwd(),file));
    const source=candidate&&fs.existsSync(candidate)?candidate:file;
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
      {module,exports:module.exports,console,Error,Date:TestDate,URL,Headers,FormData,AbortController,setTimeout:testTimeout,clearTimeout:clear,process:{env:{EXPO_PUBLIC_API_BASE_URL:'https://synthetic.invalid'}},
        fetch:async(url,init)=>{const route=new URL(url).pathname;requests.push(route);if(fetchDelegate)return fetchDelegate(route,init);if(route==='/v1/auth/status')return new Response(JSON.stringify({configured:true,setup_required:false}));if(route==='/v1/auth/account')return new Response(JSON.stringify({username:'synthetic',display_name:'Synthetic'}));throw new Error('Unexpected '+route);},
        require:name=>{if(mocks[name])return mocks[name];const target=path.resolve(path.dirname(file),name);if(name.endsWith('.json'))return JSON.parse(fs.readFileSync(target,'utf8'));return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:source});cache.set(file,module.exports);return module.exports;}
  const provider=security?null:load('components/AuthProvider.tsx'),session=security?null:load('utils/sessionClient.ts');
  function Probe(){current=provider.useAuth();return current.authenticated?React.createElement('Text',null,'Private synthetic screen'):current.loginScreen;}
  return{prompts,requests,load,get current(){return current;},get session(){return session;},get accountWrites(){return accountWrites;},get clock(){return clock;},set request(value){request=value;},set fetchDelegate(value){fetchDelegate=value;},
    mount:async()=>{await act(async()=>{renderer=create(security?React.createElement(load('components/AccountSecurity.tsx').default):React.createElement(provider.default,null,React.createElement(Probe)));});await settle();},
    unmount:async()=>{if(renderer){await act(async()=>renderer.unmount());renderer=null;}await act(async()=>{prompts.forEach(prompt=>prompt.resolve({token:'synthetic-biometric',expiresAt:clock+60_000}));});await settle();},
    emit:async(event,state)=>act(async()=>{if(event==='change')native.AppState.currentState=state;events.emit(event,state);}),
    advance:async(ms)=>act(async()=>{clock+=ms;for(const[id,timer]of [...timers])if(timer.at<=clock){timers.delete(id);timer.callback();}}),
    press:async label=>act(async()=>{const button=renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(button,label);assert.ok(!button.props.disabled,label+' enabled');button.props.onPress();}),
    text:()=>renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join('')).join('\n'),
    covered:()=>renderer.root.findAllByProps({accessibilityLabel:'Dados protegidos'}).length>0,
    passwordFields:()=>renderer.root.findAllByType('TextInput').length};
}
async function check(name,options,work){const h=harness(options);try{await work(h);console.log('PASS',name);}catch(error){failures.push(name);console.error('FAIL',name,error.message);}finally{await h.unmount();}}
(async()=>{
  await check('An old cancelled biometric prompt cannot change the new lock cycle to password mode',{biometric:true},async h=>{
    await h.mount();assert.equal(h.prompts.length,1);await act(async()=>h.current.lock());await act(async()=>h.prompts[0].reject(new Error('Old cancelled prompt')));await settle();
    assert.equal(h.prompts.length,2,'The new lock cycle offers its own biometric attempt');assert.equal(h.passwordFields(),0);assert.doesNotMatch(h.text(),/Old cancelled prompt/);
  });
  await check('An external session lock invalidates an in-flight biometric result',{biometric:true},async h=>{
    await h.mount();await act(async()=>h.session.authState.lock());await act(async()=>h.prompts[0].resolve({token:'synthetic-biometric',expiresAt:h.clock+60_000}));await settle();
    assert.equal(h.session.authState.unlocked(),false,'A late OS result must not undo revocation/expiry');assert.equal(h.current.account,null);assert.equal(h.prompts.length,2);
  });
  await check('Server/client session invalidation clears the account identity',{},async h=>{
    await h.mount();await act(async()=>h.current.replaceSession({access_token:'synthetic-session',expires_in:3600,account:{username:'synthetic',display_name:'Synthetic'}}));assert.equal(h.current.account.username,'synthetic');
    await act(async()=>h.session.authState.lock());assert.equal(h.current.account,null,'401 and expiration use the same provider cleanup as manual lock');
  });
  await check('Android active events do not uncover a window that is still blurred',{},async h=>{
    await h.mount();await act(async()=>h.current.replaceSession({access_token:'synthetic-session',expires_in:3600,account:{username:'synthetic',display_name:'Synthetic'}}));
    await h.emit('blur');await h.emit('change','active');assert.equal(h.covered(),true);assert.equal(h.session.authState.getToken(),'');
    await h.emit('focus');assert.equal(h.covered(),false);assert.equal(h.session.authState.getToken(),'synthetic-session');
  });
  await check('Biometric OS results wait for foreground before private validation',{biometric:true},async h=>{
    await h.mount();await h.emit('blur');await act(async()=>h.prompts[0].resolve({token:'synthetic-biometric',expiresAt:h.clock+60_000}));await settle();
    assert.equal(h.requests.filter(route=>route==='/v1/auth/account').length,0,'Covered login cannot bypass suspended private networking');
    await h.emit('focus');await settle();assert.equal(h.requests.filter(route=>route==='/v1/auth/account').length,1);assert.equal(h.session.authState.unlocked(),true);
  });
  await check('Blur cancels biometric server validation and discards the late response',{biometric:true},async h=>{
    const response=deferred();let signal;
    await h.mount();h.fetchDelegate=async(route,init)=>{assert.equal(route,'/v1/auth/account');signal=init.signal;return response.promise;};
    await act(async()=>h.prompts[0].resolve({token:'synthetic-biometric',expiresAt:h.clock+60_000}));await settle();assert.ok(signal);
    await h.emit('blur');assert.equal(signal.aborted,true,'Private biometric validation is cancelled by the privacy cover');
    await act(async()=>response.resolve(new Response('{"username":"synthetic","display_name":"Synthetic"}')));await h.emit('focus');await settle();assert.equal(h.session.authState.unlocked(),false);assert.match(h.text(),/A validação foi interrompida/);assert.doesNotMatch(h.text(),/Consulta cancelada/);
  });
  await check('A cancelled request cannot later mark a newer connection as reconnecting',{},async h=>{
    const {createApiClient}=h.load('utils/apiClient.ts'),old=deferred();let calls=0;
    const client=createApiClient({baseUrl:'https://synthetic.invalid',token:()=>'',readTimeoutMs:50,readRetryDelaysMs:[],fetch:()=>++calls===1?old.promise:Promise.resolve(new Response('{"mongodb":"connected"}'))});
    const obsolete=client.json('/health').then(()=>null,error=>error);client.reset();await client.json('/health');assert.equal(client.getAvailability(),'connected');
    await h.advance(50);old.resolve(new Response('{"mongodb":"connected"}'));const error=await obsolete;
    assert.equal(error.name,'AbortError');assert.equal(client.getAvailability(),'connected');
  });
  await check('Profile ACK belongs to its mounted account editor',{security:true},async h=>{
    const ack=deferred();h.request=async(_route,init={})=>init.method==='PATCH'?ack.promise:{items:[]};await h.mount();await h.press('Salvar perfil');await h.unmount();await act(async()=>ack.resolve({username:'stale',display_name:'Stale'}));
    assert.equal(h.accountWrites,0,'The old editor cannot replace another session account');
  });
  await check('An obsolete sessions read error cannot overwrite a successful refresh',{security:true},async h=>{
    const old=deferred();let reads=0;h.request=()=>++reads===1?old.promise:Promise.resolve({items:[{id:'new',label:'Latest synthetic session',created_at:1,expires_at:2,current:true}]});
    await h.mount();await h.press('Atualizar sessões');await settle();assert.match(h.text(),/Latest synthetic session/);
    await act(async()=>old.reject(new Error('Obsolete sessions error')));assert.doesNotMatch(h.text(),/Obsolete sessions error/);
  });
  if(failures.length){console.error(`${failures.length} auth-transition regressions failed`);process.exitCode=1;}else console.log('9 auth-transition regressions passed; synthetic OS, clock and HTTP only.');
})().catch(error=>{console.error(error);process.exitCode=1;});
