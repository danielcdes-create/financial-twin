// Cover screen CostControlCard: MTD vs prior month same period, weekly/monthly mode switching, insights and category drilldown.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),requests=[];let renderer;
const sqliteDb={getAllAsync:async()=>[],runAsync:async()=>{},execAsync:async()=>{}};
const queueSync=async()=>{};
const costControl={
  status:'controlling',
  status_label:'Custos sob controle',
  headline:'Você reduziu os gastos em 20.0% (-R$ 80.00) em relação ao mesmo período anterior (01/09 a 20/09).',
  mtd:{
    reference_date:'2026-10-20',
    current_month:'2026-10',
    previous_month:'2026-09',
    day_of_month:20,
    days_in_month:31,
    previous_days_compared:20,
    current_period_label:'01/10 a 20/10',
    previous_period_label:'01/09 a 20/09',
    current_expenses:320,
    previous_mtd_expenses:400,
    previous_full_expenses:600,
    current_income:3000,
    previous_mtd_income:2800,
    delta_amount:-80,
    delta_pct:-20,
    daily_burn_current:16,
    daily_burn_previous:20,
    projected_month_expenses:496,
    projected_vs_previous_full_pct:-17.3,
    buckets:[
      {key:'S1',label:'Sem 1',range_label:'01–07',current_expenses:180,previous_mtd_expenses:250,previous_full_expenses:250,current_cumulative:180,previous_mtd_cumulative:250,previous_full_cumulative:250,delta_pct:-28,reached:true},
      {key:'S2',label:'Sem 2',range_label:'08–14',current_expenses:140,previous_mtd_expenses:150,previous_full_expenses:150,current_cumulative:320,previous_mtd_cumulative:400,previous_full_cumulative:400,delta_pct:-6.7,reached:true},
      {key:'S3',label:'Sem 3',range_label:'15–21',current_expenses:0,previous_mtd_expenses:0,previous_full_expenses:0,current_cumulative:320,previous_mtd_cumulative:400,previous_full_cumulative:400,delta_pct:null,reached:true},
      {key:'S4',label:'Sem 4',range_label:'22–28',current_expenses:0,previous_mtd_expenses:0,previous_full_expenses:200,current_cumulative:null,previous_mtd_cumulative:400,previous_full_cumulative:600,delta_pct:null,reached:false},
      {key:'S5',label:'Sem 5',range_label:'29+',current_expenses:0,previous_mtd_expenses:0,previous_full_expenses:0,current_cumulative:null,previous_mtd_cumulative:400,previous_full_cumulative:600,delta_pct:null,reached:false},
    ],
  },
  weekly_series:[
    {key:'W-3',label:'Sem -3',period_label:'23/09–29/09',current_expenses:200,previous_expenses:150,current_income:0,delta_amount:50,delta_pct:33.3},
    {key:'W-2',label:'Sem -2',period_label:'30/09–06/10',current_expenses:180,previous_expenses:200,current_income:3000,delta_amount:-20,delta_pct:-10},
    {key:'W-1',label:'Sem -1',period_label:'07/10–13/10',current_expenses:140,previous_expenses:180,current_income:0,delta_amount:-40,delta_pct:-22.2},
    {key:'W0',label:'Atual',period_label:'14/10–20/10',current_expenses:95,previous_expenses:140,current_income:0,delta_amount:-45,delta_pct:-32.1},
  ],
  weekly_summary:{
    current_week_expenses:95,
    previous_week_expenses:140,
    week_over_week_pct:-32.1,
    rolling_4w_expenses:615,
    prior_4w_expenses:720,
    rolling_4w_change_pct:-14.6,
    weekly_avg_current:153.75,
    weekly_avg_previous:180,
  },
  monthly_comparison_series:[
    {month:'2026-09',label:'09/26',current_expenses:600,previous_expenses:650,income:2800,net:2200,delta_amount:-50,delta_pct:-7.7},
    {month:'2026-10',label:'10/26',current_expenses:320,previous_expenses:600,income:3000,net:2680,delta_amount:-280,delta_pct:-46.7},
  ],
  category_drivers:[
    {category:'Mercado',current_amount:180,previous_amount:250,delta_amount:-70,delta_pct:-28},
  ],
  insights:[
    {id:'burn_projection',tone:'good',title:'Ritmo diário e projeção do mês',detail:'Velocidade atual de R$ 16.00/dia.',metric:'R$ 16.00/dia'},
    {id:'top_saving_category',tone:'good',title:'Economia em Mercado',detail:'Caiu de R$ 250.00 para R$ 180.00.',metric:'-R$ 70.00',category:'Mercado'},
  ],
};
const summary={
  period_start:'2026-10-01',
  period_end:'2026-10-20',
  category_spending:[{category:'Mercado',amount:180},{category:'Restaurantes',amount:140}],
  monthly_series:[{month:'2026-09',income:2800,expenses:600,net:2200},{month:'2026-10',income:3000,expenses:320,net:2680}],
  income:3000,
  expenses:320,
  net_flow:2680,
  comparison:{period_start:'2026-09-01',period_end:'2026-09-20',income:2800,expenses:400,net_flow:2400,income_change_pct:7.1,expenses_change_pct:-20,net_change:280},
  cost_control:costControl,
};
const api={json:async url=>{
  requests.push(url);
  if(url.startsWith('/v1/dashboard/overview'))return{selected:summary,month:summary,year:summary};
  if(url.startsWith('/v1/movements?'))return{items:[],total:0,has_more:false};
  return{items:[],total:0,mongodb:'connected',auth_required:true};
},upload:async()=>{},getReadGeneration:()=>0,subscribeAvailability:()=>()=>{},getAvailability:()=> 'connected',
configureMutationStore(){},pendingMutations:async()=>[],subscribeMutations:()=>()=>{}};
const native={Platform:{OS:'android'},StyleSheet:{create:x=>x},useWindowDimensions:()=>({width:390,height:844,fontScale:1}),
  ...Object.fromEntries(['ActivityIndicator','Pressable','ScrollView','Text','TextInput','View'].map(name=>[name,name])),
  FlatList:props=>React.createElement('ListSurface',{data:props.data},props.ListHeaderComponent,props.ListFooterComponent)};
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
const allText=()=>renderer.root.findAllByType('Text').map(n=>[n.props.children].flat(Infinity).filter(Boolean).join('')).join('\n');
const button=label=>{const node=renderer.root.findAllByType('Pressable').find(node=>node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(node,label);return node;};
const press=label=>act(async()=>{await button(label).props.onPress();});
(async()=>{
  await act(async()=>{renderer=create(React.createElement(App),{createNodeMock:()=>({scrollTo(){}})});});await settle();
  const rendered=allText();
  assert.match(rendered,/Custos sob controle/);
  assert.match(rendered,/MTD atual \(01\/10 a 20\/10\)/);
  assert.match(rendered,/Mesmo período ant\. \(01\/09 a 20\/09\)/);
  assert.match(rendered,/Curva de tendência acumulada no mês/);
  assert.match(rendered,/Economia em Mercado/);
  await press('Semanal');await settle();
  assert.match(allText(),/Últimas 4 semanas vs\. anteriores/);
  assert.match(allText(),/14\/10–20\/10/);
  await press('Mensal');await settle();
  assert.match(allText(),/Saídas do mês anterior/);
  await press('Ver Mercado no Extrato ›');await settle();
  const lastMov=requests.filter(u=>u.startsWith('/v1/movements?')).at(-1);
  assert.ok(lastMov && lastMov.includes('category=Mercado'));
  console.log('PASS cover screen CostControlCard MTD, weekly, monthly charts, cost control insights and category drilldown');
  await act(async()=>renderer.unmount());
})().catch(async error=>{console.error(error);if(renderer)await act(async()=>renderer.unmount());process.exitCode=1;});
