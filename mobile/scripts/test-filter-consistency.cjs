// Real filter helpers and screens, with isolated HTTP and native surfaces.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map();let renderer;
const native={Platform:{OS:'android'},StyleSheet:{create:x=>x},useWindowDimensions:()=>({width:390,height:844,fontScale:1}),
  BackHandler:{addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},AppState:{addEventListener:()=>({remove(){}})},
  ...Object.fromEntries(['View','Text','Pressable','TextInput','ScrollView','FlatList','ActivityIndicator'].map(name=>[name,name]))};
const mocks={'react':React,'react-native':native,'../utils/appAlert':{Alert:{alert(){}},ActivityAlert:{alert(){}}},
  '../utils/queueDb':{useSQLiteContext:()=>({getAllAsync:async()=>[],runAsync:async()=>{}})}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,
    {module:m,exports:m.exports,console,Date,Intl,URLSearchParams,setTimeout,clearTimeout,AbortController,require:name=>{
      if(mocks[name])return mocks[name];if(name==='./ControlledChoice')return{__esModule:true,default:props=>React.createElement('Choice',props)};
      if(['./TransactionEditor','./ProvisionalReview'].includes(name))return{__esModule:true,default:()=>null};
      if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');
    }},{filename:file});cache.set(file,m.exports);return m.exports;}
const helpers=load('utils/movementFilters.ts');
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,20));});
const text=()=>renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join('')).join('\n');
const button=label=>{const node=renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(t=>t.props.children===label));assert.ok(node,label);return node;};
const press=label=>act(async()=>{button(label).props.onPress();});
const mount=async element=>{await act(async()=>{renderer=create(element);});await settle();};
const unmount=async()=>{if(renderer){await act(async()=>renderer.unmount());renderer=null;}};
(async()=>{
  const expected=['Alimentação','Transporte'];
  assert.deepEqual(Array.from(helpers.categoryOptions(['Alimentação',{name:'Transporte'},{categoria:'Alimentação'},null,''])),expected);
  assert.equal(helpers.recordCategory({provisional_id:'p',categoria_sugerida:'Transporte'}),'Transporte');
  assert.equal(helpers.recordCategory({categoria:null,categoria_sugerida:'Inferência'}),'Sem categoria');
  assert.equal(helpers.ownAccountTransfer({natureza:'TRANSFERENCIA',transfer:{from_account_key:'origem',to_account_key:'destino'}}),true);
  assert.equal(helpers.ownAccountTransfer({natureza:'TRANSFERENCIA',transfer:{from_account_key:'mesma',to_account_key:'mesma'}}),false);
  assert.equal(helpers.ownAccountTransfer({natureza:'TRANSFERENCIA',vendedor:'Instituição',subcategoria:'Doação'}),false);
  assert.equal(helpers.ownAccountTransfer({natureza:'COMPRA',transfer:{from_account_key:'origem',to_account_key:'destino'}}),false);
  assert.equal(helpers.classificationLine({natureza:'COMPRA',categoria:'Outros',subcategoria:'Doação',metodo_pagamento:'cartao_credito'}),'Compras e Consumo · Outros › Doação · Cartão de crédito');
  assert.equal(helpers.classificationLine({natureza:'TRANSFERENCIA',categoria:'Outros',subcategoria:'Doação',metodo_pagamento:'cartao_credito'}),'Transferência Neutra · Outros › Doação · Cartão de crédito');
  assert.equal(helpers.classificationLine({natureza:'TRANSFERENCIA',categoria:'Outros',subcategoria:'Doação',metodo_pagamento:'pix',transfer:{from_account_key:'origem',to_account_key:'destino'}}),'Transferência Neutra · Pix');
  assert.equal(helpers.parcelamentoListMark({parcelamento_purchase:true,installment_count:3,valor:-25,parcelamento:{count:4,total:100}}),'parcela 1/4');
  assert.equal(helpers.parcelamentoListMark({parcelamento_purchase:true,installment_count:1,valor:-25}),'');
  assert.equal(helpers.natureLabel('COMPRA'),'Compras e Consumo');
  assert.equal(helpers.paymentLabel('cartao_credito'),'Cartão de crédito');
  const fromValue=helpers.parcelamentoPurchaseSummary({parcelamento_purchase:true,installment_count:4,valor:-25});
  assert.equal(fromValue.count,4);assert.equal(fromValue.total,100);
  const fromPlan=helpers.parcelamentoPurchaseSummary({commitment:{purchase:true},parcelamento:{count:4,total:100}});
  assert.equal(fromPlan.count,4);assert.equal(fromPlan.total,100);
  assert.equal(helpers.parcelamentoPurchaseSummary({parcelamento_purchase:true,installment_count:1,valor:-25}),null);
  assert.equal(helpers.parcelamentoPurchaseSummary({installment_count:4,valor:-25}),null);
  for(const cursor of [null,'next']){
    const query=new URL('https://synthetic.invalid'+helpers.movementPath({category:'Sem categoria',account:'profile:itau',range:{from:'2026-10-01',to:'2026-10-31'},spendingOnly:true,cursor})).searchParams;
    assert.equal(query.get('period_basis'),'economic');assert.equal(query.get('spending_only'),'true');assert.equal(query.get('account_id'),'profile:itau');
  }
  assert.ok(!helpers.movementPath({}).includes('spending_only'));
  const Lab=load('components/FinancialLab.tsx').default;
  await mount(React.createElement(Lab,{onBack(){},api:async url=>url==='/v1/categories'?{items:expected}:{items:[],trail_preview:[],gaps:[],accounts:[]}}));
  await press('Experimentos');await settle();
  const choice=renderer.root.findByType('Choice');assert.deepEqual(Array.from(choice.props.options),[...expected,'Sem categoria']);await unmount();
  const Records=load('components/RecordReview.tsx').default;let resolveRecords;
  const api=async url=>new URL('https://synthetic.invalid'+url).searchParams.get('review_status')==='validado'?new Promise(resolve=>{resolveRecords=resolve;}):
    {items:[{id:'old',provisional_id:'old',movement_source:'capture',valor:-10,categoria_sugerida:'Alimentação',merchant_raw:'Linha pendente antiga',status_revisao:'pendente'}],total:1};
  await mount(React.createElement(Records,{api,money:String,onUpdated:async()=>{}}));assert.match(text(),/Alimentação/);
  await press('Validados');assert.doesNotMatch(text(),/Linha pendente antiga/);await settle();
  await act(async()=>{resolveRecords({items:[{id:'new',valor:-7,categoria:'Transporte',estabelecimento_original:'Linha validada nova',status_revisao:'validado'}],total:1});});
  assert.match(text(),/Linha validada nova/);assert.doesNotMatch(text(),/Linha pendente antiga/);await unmount();
  const Cards=load('components/CardManager.tsx').default;let resolveCards;
  const card={id:'c',instituicao:'Itaú',apelido:'Cartão correto',bill_count:1,deleted:false};
  const cardApi=async url=>url.includes('include_deleted=true')?new Promise(resolve=>{resolveCards=resolve;}):{items:[card]};
  await mount(React.createElement(Cards,{api:cardApi,cards:[card],money:String,onUpdated:async()=>{}}));
  await press('Mostrar excluídos');assert.doesNotMatch(text(),/Cartão correto/);await settle();
  await act(async()=>resolveCards({items:[{...card,apelido:'Lista com excluídos',deleted:true}]}));assert.match(text(),/Lista com excluídos/);await unmount();
  console.log('PASS category formats, persisted labels, drilldown parameters, review filter ownership, deleted-card filter ownership, own-transfer label and parcelamentoListMark');
})().catch(async error=>{console.error(error);await unmount();process.exitCode=1;});
