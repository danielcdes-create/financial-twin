// Real App navigation: every category, scoped refresh/pages and clearing filters.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),requests=[];let renderer,releaseAccount;
const sqliteDb={getAllAsync:async()=>[],runAsync:async()=>{},execAsync:async()=>{}};
const queueSync=async()=>{};
const categories=Array.from({length:17},(_,index)=>({category:`Categoria ${index}`,amount:100-index}));
const summary={period_start:'2026-10-01',period_end:'2026-10-31',category_spending:categories,monthly_series:[],income:2000,expenses:1000};
const row=(id)=>({id,movement_source:'ledger',categoria:'Categoria 16',valor:-10,estabelecimento_original:id});
const api={json:async url=>{
  requests.push(url);
  if(url.startsWith('/v1/dashboard/overview'))return{selected:summary,month:summary,year:summary};
  if(url==='/v1/accounts')return{items:[{id:'savings',tipo:'poupanca',instituicao:'Poupança teste'}]};
  if(url.startsWith('/v1/movements?')){
    const query=new URL('https://synthetic.invalid'+url).searchParams;
    if(query.has('account_id'))return new Promise(resolve=>{releaseAccount=()=>resolve({items:[row('Conta nova')],total:1,has_more:false});});
    if(query.has('cursor'))return{items:[row('Página seguinte')],total:2,has_more:false};
    return{items:[row(query.has('category')?'Categoria aberta':'Histórico geral')],total:2,has_more:true,next_cursor:'next'};
  }
  return{items:[],total:0,mongodb:'connected',auth_required:true};
},upload:async()=>{},getReadGeneration:()=>0,subscribeAvailability:()=>()=>{},getAvailability:()=> 'connected',
configureMutationStore(){},pendingMutations:async()=>[],subscribeMutations:()=>()=>{}};
const native={Platform:{OS:'android'},StyleSheet:{create:x=>x},useWindowDimensions:()=>({width:390,height:844,fontScale:1}),
  ...Object.fromEntries(['ActivityIndicator','Pressable','ScrollView','Text','TextInput','View'].map(name=>[name,name])),
  FlatList:props=>React.createElement('ListSurface',{data:props.data},props.ListHeaderComponent,
    props.data.map((item,index)=>React.createElement(React.Fragment,{key:item.id},props.renderItem({item,index}))),props.ListFooterComponent)};
const mocks={'react':React,'react-native':{...native,BackHandler:{addEventListener:()=>({remove(){}})}},'react-native-safe-area-context':{SafeAreaView:'View',useSafeAreaInsets:()=>({top:0,bottom:0})},
  'expo-document-picker':{},'expo-image-picker':{},'expo-router':{usePathname:()=> '/',router:{push(){}}},'expo-notification-capture':{__esModule:true,default:{available:false}},
  './components/AuthProvider':{useAuth:()=>({lock(){}})},'./utils/queueDb':{useSQLiteContext:()=>sqliteDb},
  './utils/useQueueSync':{useQueueSync:()=>queueSync},'./utils/useForeground':{useForeground:()=>true},
  './utils/sessionClient':{API:'https://synthetic.invalid',api,authState:{getToken:()=> 'session',unlocked:()=>true,sessionIdentity:()=>1}},
  './utils/appAlert':{Alert:{alert(){}}},'../utils/appAlert':{Alert:{alert(){}}},'./sessionStore':{getItemAsync:async()=>null},'./utils/durableFiles':{},'./uploadFile':{appendQueuedFile:async()=>{}}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
    {module:m,exports:m.exports,console,Error,Date,Intl,Symbol,Promise,URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,
      require:name=>{if(mocks[name])return mocks[name];if(name.startsWith('./components/')&&name!=='./components/InteractionControls')return{__esModule:true,default:props=>React.createElement(name.slice(13)+'Stub',props)};
        if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:file});cache.set(file,m.exports);return m.exports;}
const App=load('App.tsx').default;
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,25));});
const button=label=>{const node=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(node,label);return node;};
const press=label=>act(async()=>{await button(label).props.onPress();});
const query=()=>new URL('https://synthetic.invalid'+requests.filter(url=>url.startsWith('/v1/movements?')).at(-1)).searchParams;
(async()=>{
  await act(async()=>{renderer=create(React.createElement(App),{createNodeMock:()=>({scrollTo(){}})});});await settle();
  assert.equal(renderer.root.findAllByType('Pressable').filter(node=>node.findAllByType('Text').some(t=>/^Categoria \d+$/.test(t.props.children))).length,17);
  await press('Categoria 16');assert.equal(query().get('period_basis'),'economic');assert.equal(query().get('spending_only'),'true');assert.equal(query().get('category'),'Categoria 16');
  await press('Carregar mais');assert.equal(query().get('cursor'),'next');assert.equal(query().get('period_basis'),'economic');
  assert.equal(renderer.root.findByType('ListSurface').props.data.length,2);
  let task;act(()=>{task=button('Conta · Poupança teste').props.onPress();});await settle();
  assert.equal(query().get('account_id'),'savings');assert.equal(renderer.root.findByType('ListSurface').props.data.length,0,'Old rows disappear while the new filter awaits HTTP');
  await act(async()=>{releaseAccount();await task;});assert.equal(renderer.root.findByType('ListSurface').props.data[0].id,'Conta nova');
  await press('Remover');assert.equal(query().has('category'),false);assert.equal(query().has('date_from'),false);assert.equal(query().has('account_id'),false);assert.equal(query().has('spending_only'),false);
  console.log('PASS all 17 categories, savings filter, economic refresh/page parameters, stale-row suppression and complete filter removal');
  await act(async()=>renderer.unmount());
})().catch(async error=>{console.error(error);if(releaseAccount)releaseAccount();if(renderer)await act(async()=>renderer.unmount());process.exitCode=1;});
