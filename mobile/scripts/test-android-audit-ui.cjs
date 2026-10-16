// Read-only adversarial probes: production React components; synthetic transport/native UI only.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const mobile=process.cwd();
const ts=require(path.join(mobile,'node_modules/typescript')),React=require(path.join(mobile,'node_modules/react'));
const {act,create}=require(path.join(mobile,'node_modules/react-test-renderer'));
global.IS_REACT_ACT_ENVIRONMENT=true;
const failures=[];let dbRows=[];const db={getAllAsync:async()=>dbRows,runAsync:async()=>{}};
function harness(){
 const cache=new Map();let renderer;
 const native={Platform:{OS:'android'},StyleSheet:{create:x=>x},useWindowDimensions:()=>({width:390,height:844,fontScale:1}),Keyboard:{dismiss(){}},Alert:{alert(){}},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},BackHandler:{addEventListener:()=>({remove(){}})},
  FlatList:({data,renderItem,...rest})=>React.createElement('FlatList',rest,(data||[]).map((item,index)=>React.createElement(React.Fragment,{key:index},renderItem({item,index})))),
  ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator','ScrollView','Modal','Image'].map(name=>[name,name]))};
 const mocks={'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'View'},'./VoiceCapture':{__esModule:true,default:()=>null},'../utils/queueDb':{useSQLiteContext:()=>db},'expo-image-picker':{},'expo-sharing':{},'react-native-view-shot':{}};
 function load(relative){const file=path.resolve(mobile,relative);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  vm.runInNewContext(code,{module:m,exports:m.exports,console,Error,Date,Intl,Math,URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,Promise,Symbol,
   require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(path.join(mobile,'node_modules',name));const target=path.resolve(path.dirname(file),name);if(name.endsWith('.json'))return JSON.parse(fs.readFileSync(target,'utf8'));return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:file});cache.set(file,m.exports);return m.exports;}
 const dialogs=load('utils/appAlert.ts'),gate=load('utils/userOperation.ts'),Provider=load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider;
 return {load,dialogs,gate,get renderer(){return renderer},async mount(component,props){const C=load('components/'+component+'.tsx').default;const Schedule=load('components/ScheduleOverlay.tsx').ScheduleOverlayProvider;await act(async()=>{renderer=create(React.createElement(Provider,null,React.createElement(Schedule,null,React.createElement(C,props))))});await this.settle();},
  async settle(ms=20){await act(async()=>{await new Promise(r=>setTimeout(r,ms))})},
  button(label){const button=renderer.root.findAllByType('Pressable').find(n=>n.props.accessibilityLabel===label||n.findAllByType('Text').some(t=>t.props.children===label));assert.ok(button,'Missing button '+label);return button;},
  async press(label){const b=this.button(label);assert.ok(!b.props.disabled,label+' is disabled');await act(async()=>{await b.props.onPress({})})},
  async input(label,value){await act(async()=>{const n=renderer.root.findAllByType('TextInput').find(n=>n.props.accessibilityLabel===label);assert.ok(n,'Missing input '+label);n.props.onChangeText(value)})},
  text(){return renderer.root.findAllByType('Text').map(n=>[].concat(n.props.children).join('')).join('\n')},
  async answer(label){const d=dialogs.dialogSnapshot();assert.ok(d,'Missing dialog');const b=d.buttons.find(b=>b.text===label);assert.ok(b,'Missing dialog choice '+label);await act(async()=>{dialogs.dismissDialog(b,d)});await this.settle()},
  async unmount(){if(renderer){await act(async()=>renderer.unmount());renderer=null}},async close(){await this.unmount();dialogs.clearDialogs();assert.equal(gate.isUserOperationBusy(),false)}};
}

async function check(name,work){const h=harness();dbRows=[];try{await work(h);console.log('PASS',name)}catch(error){failures.push(name);console.error('FAIL',name,error.stack)}finally{await h.close()}}
const card={id:'card',instituicao:'Banco',apelido:'Sintetico',cartao_final:'1234',bill_count:1,deleted:false,token:'v1'};
const planContext={flows:[],suggested_flows:[],excluded_sources:[],unmapped_obligations:0,gaps:[],accounts:[]};
(async()=>{
 await check('UI03 strict money grouping, signs, cents, range and zero by domain',async h=>{
  const parse=h.load('utils/moneyInput.ts').parseMoneyInput;
  for(const [raw,expected] of [['1234','1234.00'],['1.234,56','1234.56'],['1234.56','1234.56'],['R$ 32,9','32.90'],['1.234','1234.00']])assert.equal(parse(raw),expected,raw);
  for(const raw of ['1.2,34','1..234,56','1e3','1,234','1.234.56','1 2,34','R$ R$ 12','-1','+1'])assert.equal(parse(raw),null,raw);
  assert.equal(parse('-1.234,56',{signed:true}),'-1234.56');assert.equal(parse('0',{allowZero:false}),null);assert.equal(parse('100000001',{max:100000000}),null);
  assert.equal(h.load('utils/statementBatch.ts').signedBatchAmount('1.2,34'),null);
 });
 await check('UI01 overlap stays blocked through repeated reload until explicit field choice',async h=>{
  let remote=100,revision=1;const writes=[];
  const api=async(_p,init={})=>{if(init.method==='PUT'){writes.push(JSON.parse(init.body));if(writes.length===1){remote=200;revision=2;throw Object.assign(new Error('Fonte mudou'),{status:409})}return{persisted:true,source:'installment',identity:'synthetic'}}return{rows:[{editable:true,row_id:1,number:1,amount:remote,due_date:'2026-12-01'}],editable_count:1,preview_token:'v'+revision}};
  await h.mount('FutureCommitmentEditor',{api,source:'installment',identity:'synthetic',name:'Sintetico',onUpdated:async()=>{}});
  await h.press('Editar futuras de Sintetico');await h.input('Valor da parcela 1','110,00');await h.press('Revisar alterações');await h.press('Confirmar alterações');
  for(let i=0;i<2;i++){await h.press('Carregar cronograma atual e conferir rascunho');assert.match(h.text(),/Atual: 200.00/);assert.match(h.text(),/Origem: 100.00/);assert.equal(h.button('Revisar alterações').props.disabled,true);assert.equal(writes.length,1);}
  await h.press('Aplicar meu valor: parcela 1, amount');await h.press('Revisar alterações');await h.press('Confirmar alterações');assert.equal(writes[1].rows[0].amount,'110.00');assert.equal(writes[1].preview_token,'v2');assert.notEqual(writes[0].request_id,writes[1].request_id);
 });
 await check('UI01 disjoint due-date change converges with local amount and new preview token',async h=>{
  let changed=false;const writes=[];const api=async(_p,init={})=>{if(init.method){writes.push(JSON.parse(init.body));if(!changed){changed=true;throw Object.assign(new Error('Fonte mudou'),{status:409})}return{persisted:true,identity:'synthetic',source:'installment'}}return{rows:[{row_id:1,number:1,editable:true,amount:100,due_date:changed?'2026-12-10':'2026-12-01'}],editable_count:1,preview_token:changed?'v2':'v1'}};
  await h.mount('FutureCommitmentEditor',{api,source:'installment',identity:'synthetic',name:'Sintetico',onUpdated(){}});await h.press('Editar futuras de Sintetico');await h.input('Valor da parcela 1','110');await h.press('Revisar alterações');await h.press('Confirmar alterações');await h.press('Carregar cronograma atual e conferir rascunho');assert.equal(h.button('Revisar alterações').props.disabled,false);await h.press('Revisar alterações');await h.press('Confirmar alterações');assert.equal(writes[1].rows[0].due_date,'2026-12-10');assert.equal(writes[1].rows[0].amount,'110.00');
 });
 await check('UI01 a remotely paid or cancelled row keeps its local edit through repeated reloads',async h=>{
  let changed=false,writes=0;const api=async(_p,init={})=>{if(init.method){writes++;changed=true;throw Object.assign(new Error('Fonte mudou'),{status:409})}return{rows:[{row_id:1,number:1,editable:!changed,excluded:changed,amount:100,due_date:'2026-12-01'}],editable_count:changed?0:1,preview_token:changed?'v2':'v1'}};
  await h.mount('FutureCommitmentEditor',{api,source:'installment',identity:'synthetic',name:'Sintetico',onUpdated(){}});await h.press('Editar futuras de Sintetico');await h.input('Valor da parcela 1','110');await h.press('Revisar alterações');await h.press('Confirmar alterações');
  for(let i=0;i<2;i++){await h.press('Carregar cronograma atual e conferir rascunho');assert.match(h.text(),/Edição preservada/);assert.equal(h.button('Revisar alterações').props.disabled,true);assert.equal(h.renderer.root.findAllByType('TextInput').find(n=>n.props.accessibilityLabel==='Valor da parcela 1').props.value,'110');}
  assert.equal(writes,1);await h.press('Retirar alteração indisponível da parcela 1');assert.equal(h.renderer.root.findAllByType('TextInput').length,0);
 });
 await check('UI01 remote due-date overlap needs explicit choice and preserves remote date when selected',async h=>{
  let changed=false;const writes=[];const api=async(_p,init={})=>{if(init.method){writes.push(JSON.parse(init.body));if(!changed){changed=true;throw Object.assign(new Error('Fonte mudou'),{status:409})}return{persisted:true,identity:'synthetic',source:'installment'}}return{rows:[{row_id:1,editable:true,amount:100,due_date:changed?'2026-12-10':'2026-12-01'}],editable_count:1,preview_token:changed?'v2':'v1'}};
  await h.mount('FutureCommitmentEditor',{api,source:'installment',identity:'synthetic',name:'Sintetico',onUpdated(){}});await h.press('Editar futuras de Sintetico');await h.input('Valor da parcela 1','110');await h.input('Vencimento da parcela 1','05/12/2026');await h.press('Revisar alterações');await h.press('Confirmar alterações');await h.press('Carregar cronograma atual e conferir rascunho');assert.equal(h.button('Revisar alterações').props.disabled,true);await h.press('Manter atual: parcela 1, due_date');await h.press('Revisar alterações');await h.press('Confirmar alterações');assert.equal(writes[1].rows[0].due_date,'2026-12-10');assert.equal(writes[1].rows[0].amount,'110.00');
 });
 await check('UI02 all scenario premises hide the previous result immediately',async h=>{
  const api=async p=>p.includes('/closure?')?{gaps:[],trail_preview:[]}:p.includes('/planning-context')?planContext:{gaps:[],scenarios:[{name:'buy_now',minimum_balance:'900',closing_balance:'900',curve:[]}]};
  await h.mount('FinancialLab',{api,onBack(){}});await h.press('Simular');await h.input('Valor total da compra (R$)','100');
  for(const [label,value] of [['Saldo inicial assumido (R$)','1000'],['Reserva mínima por conta / reserva do cenário (R$)','20'],['Valor total da compra (R$)','500'],['Número de parcelas','2'],['Primeiro pagamento em caixa (AAAA-MM-DD)','2026-12-01'],['Alternativa: esperar quantos dias?','40'],['Alternativa: valor reduzido (R$; opcional)','50'],['Outros fluxos: data; valor com sinal; descrição; conta (um por linha)','2026-12-01; -2; Teste; itau']]){
   await h.press('Comparar quatro cenários por 90 dias');assert.match(h.text(),/Menor saldo/);await h.input(label,value);assert.doesNotMatch(h.text(),/Menor saldo/);
  }
 });
 await check('UI02 liquidity result is invalidated by reserve edits',async h=>{
  const api=async p=>p.includes('/closure?')?{gaps:[],trail_preview:[]}:p.includes('/planning-context')?planContext:{gaps:[],proposals:[{amount:10,from_account_key:'a',to_account_key:'b',date:'2026-12-01',reason:'Synthetic proposal'}],uncovered:[]};
  await h.mount('FinancialLab',{api,onBack(){}});await h.press('Liquidez');await h.press('Calcular propostas de liquidez (30 dias)');assert.match(h.text(),/Synthetic proposal/);await h.input('Reserva mínima por conta / reserva do cenário (R$)','100');assert.doesNotMatch(h.text(),/Synthetic proposal/);
 });
 await check('UI03 malformed bill total starts neither confirmation nor PATCH',async h=>{
  const bill={id:'bill',competencia:'2026-12',data_vencimento:'2026-12-10',valor_total:'100',valor_pago:'0',saldo_aberto:'100',status:'aberta',observacoes:'',deleted:false,token:'v1'};let writes=0;
  const api=async(p,init={})=>{if(init.method){writes++;return{item:bill}}return p.includes('/bills?')?{card,items:[bill]}:{items:[card]}};
  await h.mount('CardManager',{api,cards:[card],money:String,onUpdated(){}});await h.press('Ver faturas de Sintetico');await h.settle();await h.press('Editar fatura 2026-12');await h.input('Total da fatura (R$)','1.2,34');await h.press('Salvar alterações');assert.equal(h.dialogs.dialogSnapshot(),null);assert.equal(writes,0);assert.match(h.text(),/até duas casas/);
 });
 await check('Card bill overlap remains blocked after repeated reload until explicit acknowledgement',async h=>{
  let changed=false;const writes=[];
  const bill=()=>({id:'bill',competencia:'2026-12',data_vencimento:'2026-12-10',valor_total:changed?'200':'100',valor_pago:'0',saldo_aberto:changed?'200':'100',status:'aberta',observacoes:'',deleted:false,token:changed?'v2':'v1'});
  const api=async(p,init={})=>{if(init.method){writes.push(JSON.parse(init.body));if(writes.length===1){changed=true;throw Object.assign(Error('Fonte mudou'),{status:409})}return{persisted:true}}return p.includes('/bills?')?{card,items:[bill()]}:{items:[card]}};
  await h.mount('CardManager',{api,cards:[card],money:String,onUpdated(){}});await h.press('Ver faturas de Sintetico');await h.settle();await h.press('Editar fatura 2026-12');await h.input('Total da fatura (R$)','110');await h.press('Salvar alterações');await h.answer('Salvar');
  for(let i=0;i<2;i++){await h.press('Carregar cadastro atual e conferir');assert.equal(h.button('Salvar alterações').props.disabled,true);assert.match(h.text(),/origem “100,00”; atual “200,00”; rascunho “110”/);assert.equal(writes.length,1);}
  await h.press('Conferi as diferenças e quero reaplicar meus campos');await h.press('Salvar alterações');await h.answer('Salvar');assert.equal(writes[1].valor_total,'110.00');assert.equal(writes[1].expected_token,'v2');
 });
 await check('UI04 card deletion callback after unmount sends zero DELETEs',async h=>{
  let writes=0;const api=async(_p,init={})=>{if(init.method)writes++;return{items:[card]}};
  await h.mount('CardManager',{api,cards:[card],money:String,onUpdated(){}});await h.press('Excluir cartão Sintetico');await h.unmount();await h.answer('Excluir');assert.equal(writes,0);
 });
 await check('UI06 same empty search retries a failed read and updates list and total',async h=>{
  let reads=0;const api=async()=>{if(++reads===1)throw Error('Falha temporária sintética');return{items:[{id:'record',vendedor:'Recovered record',valor:2}],total:1}};
  await h.mount('RecordReview',{api,money:String,onUpdated(){}});assert.match(h.text(),/Falha temporária/);await h.press('Buscar');await h.settle();assert.equal(reads,2);assert.match(h.text(),/Recovered record/);assert.doesNotMatch(h.text(),/Falha temporária/);
 });
 await check('UI07 retry restores options without remounting or accepting a category',async h=>{
  let reads=0;const api=async p=>{if(p.endsWith('classification-options')){if(++reads===1)throw Error('Falha temporária sintética');return{categoria:['Lazer']}}return{total:0}};
  await h.mount('TransactionEditor',{api,item:{id:'synthetic',valor:-10,vendedor:'Loja'},money:String,onSaved(){},onCancel(){}});await h.input('Observações','Keep this draft');assert.match(h.text(),/Falha temporária/);await h.press('Tentar carregar sugestões novamente');assert.doesNotMatch(h.text(),/Falha temporária/);await h.press('Selecionar Categoria');assert.ok(h.button('Escolher Lazer'));await h.press('Fechar opções');assert.equal(h.renderer.root.findAllByType('TextInput').find(n=>n.props.accessibilityLabel==='Observações').props.value,'Keep this draft');assert.match(h.text(),/Selecione categoria/);
 });
 await check('UI08 dirty editor Back preserves draft until save or explicit discard',async h=>{
  let exited=0;await h.mount('TransactionEditor',{api:async()=>({}),item:{id:'synthetic',valor:2,vendedor:'Loja'},money:String,onSaved(){},onCancel(){exited++}});await h.input('Observações','Keep me');await act(async()=>h.load('utils/localBack.ts').dispatchLocalBack());assert.equal(exited,0);assert.equal(h.dialogs.dialogSnapshot().title,'Rascunho nesta tela');await h.answer('Continuar editando');assert.equal(h.renderer.root.findAllByType('TextInput').find(n=>n.props.accessibilityLabel==='Observações').props.value,'Keep me');
 });
 for(const archived of [false,true])await check(`BE02 historical ${archived?'archived':'closed'} account remains linked on cancellation`,async h=>{
  const item={commitment_id:'managed',name:'Service',revision:1,kind:'assinatura',purchase_date:'2026-10-01',next_due_date:'2026-12-01',amount:10,frequency:'mensal',payment_method:'pix',account_id:'profile:old',renewal_status:'automatica',status:'ativo',schedule:[]};let body;
  const api=async(p,init={})=>{if(init.method){body=JSON.parse(init.body);return{persisted:true}}return p==='/v1/commitments'?{items:[item]}:p==='/v1/commitment-projection'?{items:[],months:1}: {items:[]}};
  await h.mount('CommitmentManager',{api,upload:async()=>({}),accounts:[],profiles:[{key:'old',instituicao:'Old bank',tipo:'conta_corrente',status:archived?'aberta':'encerrada',archived}],money:String,onUpdated(){}});await h.press('Abrir Service');await h.press('Editar compromisso Service');assert.match(h.text(),/mantida neste cadastro/);await h.press('Avançado');await h.press('Cancelado');await h.press('Salvar e sincronizar');assert.equal(body.account_id,'profile:old');assert.equal(body.status,'cancelado');
 });
 await check('BE05 completed installment sentinel survives a notes-only edit',async h=>{
  const item={commitment_id:'managed',name:'Completed plan',revision:1,kind:'parcelamento',purchase_date:'2026-10-01',next_due_date:'2026-10-01',amount:10,frequency:'mensal',payment_method:'pix',installment_count:1,next_installment_number:2,renewal_status:'nao_aplicavel',status:'concluido',schedule:[]};let body;
  const api=async(p,init={})=>{if(init.method){body=JSON.parse(init.body);return{persisted:true}}return p==='/v1/commitments'?{items:[item]}:{items:[]}};
  await h.mount('CommitmentManager',{api,upload:async()=>({}),accounts:[],money:String,onUpdated(){}});await h.press('Concluídos e cancelados');await h.press('Abrir Completed plan');await h.press('Editar compromisso Completed plan');await h.press('Avançado');await h.input('Observações (opcional)','New note');await h.press('Salvar e sincronizar');assert.equal(body.status,'concluido');assert.equal(body.next_installment_number,2);assert.equal(body.notes,'New note');
 });
 if(failures.length)throw Error(failures.join(', '));console.log('All Android audit UI repairs passed with real components and synthetic I/O.');
})().catch(error=>{console.error(error);process.exitCode=1});
