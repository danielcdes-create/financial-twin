// Production form actions, dialog host protocol and interaction gate; only
// transport, storage and native surfaces are synthetic. No financial data used.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
let renderer,dbRows=[],dbWrites=0,pdfWrites=0,authWrites=0;
const cache=new Map(),failures=[];
const native={useWindowDimensions:()=>({width:390,height:800,fontScale:1}),Platform:{OS:'android'},StyleSheet:{create:x=>x},Alert:{alert(){throw new Error('Native alert must not be used');}},
  BackHandler:{addEventListener:()=>({remove(){}})},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},
  ...Object.fromEntries(['View','Text','TextInput','Pressable','Image','ActivityIndicator','ScrollView','FlatList'].map(name=>[name,name]))};
const db={getAllAsync:async()=>dbRows,runAsync:async()=>{dbWrites++;}};
const auth={account:{username:'synthetic',display_name:'Synthetic name'},biometricOn:false,biometricSupported:false};
const mocks={'react':React,'react-native':native,'../utils/queueDb':{useSQLiteContext:()=>db},
  './VoiceCapture':{__esModule:true,default:()=>null},'./StatementBatchReview':{__esModule:true,default:()=>null},'./ControlledChoice':{__esModule:true,default:()=>null},
  'expo-image-picker':{},'expo-sharing':{},'react-native-view-shot':{},'../utils/devicePrompt':{},
  '../utils/pdfCpf':{getPdfCpf:async()=> 'synthetic-saved-cpf',setPdfCpf:async()=>{pdfWrites++;},clearPdfCpf:async()=>{pdfWrites++;}},
  '../utils/sessionClient':{api:{json:async(_route,init={})=>{if(init.method)authWrites++;return{items:[]};}}},
  './AuthProvider':{useAuth:()=>auth,authStyles:{},deviceLabel:'synthetic',AuthButton:({title,onPress,disabled})=>React.createElement('Pressable',{accessibilityLabel:title,onPress,disabled},React.createElement('Text',null,title))}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  vm.runInNewContext(code,{module:m,exports:m.exports,console,Error,Date,Intl,Math,JSON,URLSearchParams,AbortController,setTimeout,clearTimeout,
    require:name=>{if(mocks[name])return mocks[name];const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:file});cache.set(file,m.exports);return m.exports;}
const Discard=load('components/DiscardDraftAction.tsx').default,Controls=load('components/InteractionControls.tsx'),dialogs=load('utils/appAlert.ts'),gate=load('utils/userOperation.ts');
const Schedule=load('components/ScheduleOverlay.tsx').ScheduleOverlayProvider;
const hosted=element=>React.createElement(Schedule,null,element);
const Capture=load('components/CapturePanel.tsx').default,Commitment=load('components/CommitmentManager.tsx').default,Transaction=load('components/TransactionEditor.tsx').default;
const Purchase=load('components/PurchaseCheck.tsx').default,Category=load('components/CategoryManager.tsx').default,Subcategory=load('components/SubcategoryManager.tsx').default;
const Lab=load('components/FinancialLab.tsx').default,Security=load('components/AccountSecurity.tsx').default,Pdf=load('components/PdfPasswordSettings.tsx').default;
const settle=(ms=15)=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
const mount=async element=>act(async()=>{renderer=create(element);});
const button=label=>{const node=renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(node,'Button: '+label);return node;};
const press=label=>act(async()=>{const node=button(label);assert.ok(!node.props.disabled,label+' enabled');node.props.onPress();});
const field=label=>{const node=renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel===label);assert.ok(node,'Input: '+label);return node;};
const input=(label,value)=>act(async()=>field(label).props.onChangeText(value));
const confirm=async(text='Excluir rascunho')=>act(async()=>{const current=dialogs.dialogSnapshot();assert.ok(current,'Confirmation opened');const chosen=current.buttons.find(item=>item.text===text);assert.ok(chosen,'Dialog button: '+text);dialogs.dismissDialog(chosen,current);});
const text=()=>renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join('')).join('\n');
async function check(name,work){dbRows=[];dbWrites=pdfWrites=authWrites=0;try{await work();console.log('PASS',name);}catch(error){failures.push(name);console.error('FAIL',name,error.stack);}finally{dialogs.clearDialogs();if(renderer){await act(async()=>renderer.unmount());renderer=null;}await settle();assert.equal(gate.isUserOperationBusy(),false);}}
(async()=>{
  await check('Cancel keeps a draft and never invokes deletion',async()=>{let calls=0;await mount(React.createElement(Discard,{onDiscard:()=>calls++}));await press('Excluir rascunho');assert.equal(dialogs.dialogSnapshot().options.inActivity,true);await confirm('Cancelar');assert.equal(calls,0);});
  await check('Async deletion holds the global gate, rejects double confirmation and never replays other clicks',async()=>{
    let resolve,calls=0,other=0;const pending=new Promise(done=>{resolve=done;});
    await mount(React.createElement('View',null,React.createElement(Discard,{onDiscard:()=>{calls++;return pending;}}),React.createElement(Controls.Pressable,{accessibilityLabel:'Other',onPress:()=>other++})));
    await press('Excluir rascunho');const snapshot=dialogs.dialogSnapshot(),chosen=snapshot.buttons[1];await confirm();
    assert.equal(gate.isUserOperationBusy(),true);await act(async()=>{dialogs.dismissDialog(chosen,snapshot);button('Other').props.onPress();});assert.equal(calls,1);assert.equal(other,0);
    await act(async()=>resolve());assert.equal(gate.isUserOperationBusy(),false);assert.equal(other,0);
  });
  await check('Confirmation rejects obsolete owner, disabled state and unmounted editor',async()=>{
    let calls=0;const props={onDiscard:()=>calls++,owner:'a'};await mount(React.createElement(Discard,props));await press('Excluir rascunho');await act(async()=>renderer.update(React.createElement(Discard,{...props,owner:'b'})));await confirm();assert.equal(calls,0);
    await press('Excluir rascunho');await act(async()=>renderer.update(React.createElement(Discard,{...props,owner:'b',disabled:true})));await confirm();assert.equal(calls,0);
    await act(async()=>renderer.update(React.createElement(Discard,props)));await press('Excluir rascunho');await act(async()=>renderer.unmount());renderer=null;await confirm();assert.equal(calls,0);
  });
  await check('Capture offers discard only for filled drafts and calls its local owner without transport',async()=>{
    let deleted=0,sends=0;const props={kind:'purchase',merchant:'',amount:'',date:'',origin:'',destination:'',transcript:'',accounts:[],batchAccounts:[],savedRevision:0,batchRevision:0,busy:false,message:'',error:'',notificationAvailable:false,notificationEnabled:false,onDiscard:()=>deleted++,api:()=>{sends++;},upload:()=>{sends++;},onPdfCpfChanged:async()=>{},onBatchUpdated(){}};
    await mount(React.createElement(Capture,props));assert.equal(renderer.root.findAllByProps({accessibilityLabel:'Excluir rascunho da movimentação'}).length,0);
    await act(async()=>renderer.update(React.createElement(Capture,{...props,merchant:'Synthetic draft'})));await press('Excluir rascunho da movimentação');await confirm();assert.equal(deleted,1);assert.equal(sends,0);
  });
  await check('Transaction discard restores the source and never modifies the saved movement',async()=>{
    let writes=0,cancelled=0;const source={id:'tx',valor:-10,detalhe:'Saved observation',source_token:'token'};
    await mount(React.createElement(Transaction,{item:source,api:async(_route,init={})=>{if(init.method)writes++;return{};},money:String,onSaved(){},onCancel(){cancelled++;}}));await input('Observações','Unsaved observation');await press('Excluir rascunho da edição');await confirm();assert.equal(field('Observações').props.value,'Saved observation');assert.equal(source.detalhe,'Saved observation');assert.equal(cancelled,1);assert.equal(writes,0);
  });
  await check('Commitment creation with uncertain ACK preserves its journal and blocks discard',async()=>{
    const pending={creation_id:'pending-id',name:'Pending commitment',kind:'assinatura',purchase_date:'2026-10-06',next_due_date:'2026-10-06',amount:10,frequency:'mensal',payment_method:'pix',renewal_status:'automatica',status:'ativo'};
    dbRows=[{state:JSON.stringify(pending)}];await mount(hosted(React.createElement(Commitment,{api:async()=>({items:[]}),upload:async()=>{},accounts:[],money:String,onUpdated(){}})));await settle();await press('Retomar cadastro pendente');const discard=button('Excluir rascunho do compromisso');assert.equal(discard.props.disabled,true);await act(async()=>discard.props.onPress());assert.equal(dialogs.dialogSnapshot(),null);assert.equal(dbWrites,0);assert.equal(JSON.parse(dbRows[0].state).creation_id,'pending-id');
  });
  await check('A safe commitment form can be discarded and reopened blank without writes',async()=>{
    await mount(hosted(React.createElement(Commitment,{api:async()=>({items:[]}),upload:async()=>{},accounts:[],money:String,onUpdated(){}})));await settle();await press('+ Adicionar');await input('Nome ou serviço','Unsaved service');await press('Excluir rascunho do compromisso');await confirm();await press('+ Adicionar');assert.equal(field('Nome ou serviço').props.value,'');assert.equal(dbWrites,0);
  });
  await check('Purchase analysis clears fields and image locally',async()=>{
    let writes=0;await mount(React.createElement(Purchase,{api:async(_route,init={})=>{if(init.method)writes++;return{};},upload:async()=>{writes++;},ready:false,onBack(){},sharedImage:{uri:'file:synthetic'}}));await input('Preço total confirmado (R$)','123,45');await input('Nome da meta','Unsaved goal');await press('Excluir rascunho da análise');await confirm();assert.equal(field('Preço total confirmado (R$)').props.value,'');assert.equal(field('Nome da meta').props.value,'');assert.equal(renderer.root.findAllByType('Image').length,0);assert.equal(writes,0);
  });
  await check('Category and subcategory drafts clear without deleting the saved catalog',async()=>{
    let writes=0;const api=async(_route,init={})=>{if(init.method)writes++;return{items:[],details:[{name:'Saved category',usage_count:3}],category:'Saved category'};};
    await mount(React.createElement(Category,{api,money:String,spending:[],onUpdated:async()=>{}}));await settle();await input('Nova categoria','Unsaved category');await press('Excluir rascunho da categoria');await confirm();assert.equal(field('Nova categoria').props.value,'');assert.match(text(),/Saved category/);assert.equal(writes,0);await act(async()=>renderer.unmount());renderer=null;
    await mount(React.createElement(Subcategory,{category:'Saved category',api,disabled:false,onBusyChange(){},onUpdated:async()=>{}}));await settle();await input('Nova subcategoria de Saved category','Unsaved subcategory');await press('Excluir rascunho da subcategoria');await confirm();assert.equal(field('Nova subcategoria de Saved category').props.value,'');assert.equal(writes,0);
  });
  await check('Decision case and planning assumptions discard locally while saved lists remain available',async()=>{
    let writes=0;const api=async(route,init={})=>{if(init.method)writes++;return route.includes('planning-context')?{flows:[],suggested_flows:[],excluded_sources:[],accounts:[]}:route.includes('/closure')?{trail_preview:[],gaps:[]}:route.includes('/cases')?{items:[{id:'saved-case',title:'Saved case',status:'resolved'}]}:{items:[]};};
    await mount(React.createElement(Lab,{api,onBack(){}}));await settle();await press('Guardião');await input('Título do caso','Unsaved case');await press('Excluir rascunho do caso');await confirm();assert.equal(field('Título do caso').props.value,'');assert.match(text(),/Saved case/);await press('Simular');await input('Valor total da compra (R$)','999,00');await press('Excluir rascunho do planejamento');await confirm();assert.equal(field('Valor total da compra (R$)').props.value,'');assert.equal(writes,0);
  });
  await check('Profile, password and CPF fields clear without changing saved account settings',async()=>{
    await mount(React.createElement(Security));await input('Nome da conta','Unsaved name');await press('Limpar perfil não salvo');await confirm('Limpar');assert.equal(field('Nome da conta').props.value,'Synthetic name');await input('Nova senha','synthetic-unsaved');await press('Limpar senhas digitadas');await confirm('Limpar');assert.equal(field('Nova senha').props.value,'');assert.equal(authWrites,0);await act(async()=>renderer.unmount());renderer=null;
    await mount(React.createElement(Pdf,{disabled:false,onChanged:async()=>{}}));await settle();await press('CPF para PDFs com senha');await input('CPF para abrir PDFs','synthetic-input');await press('Limpar CPF não salvo');await confirm('Limpar');assert.equal(field('CPF para abrir PDFs').props.value,'');assert.match(text(),/CPF informado/);assert.equal(pdfWrites,0);
  });
  if(failures.length)throw new Error(`${failures.length} local draft discard regressions failed.`);
  console.log('11 local draft discard regressions passed. Native UI and real storage were not exercised.');
})().catch(error=>{console.error(error);process.exitCode=1;});
