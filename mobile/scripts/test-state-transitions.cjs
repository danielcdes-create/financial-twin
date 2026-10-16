// Real components and queue implementation, synthetic storage/transport/native
// surfaces. Baseline override proves failures before this audit's corrections.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cleanups=[];
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});cleanups.push(()=>resolve());return{promise,resolve,reject};};
const cache=new Map(),dialogs=[],failures=[];
let renderer,pdfRead=deferred(),pdfSaved=false,securityRequest;
const native={Platform:{OS:'android'},Application:{},BackHandler:{addEventListener:()=>({remove(){}})},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},Keyboard:{dismiss(){}},StyleSheet:{create:x=>x},
  ...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator','Image','ScrollView','Modal'].map(x=>[x,x]))};
const mocks={'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'View'},
  '../utils/appAlert':{Alert:{alert:(...args)=>dialogs.push(args)}},
  '../utils/pdfCpf':{getPdfCpf:()=>pdfRead.promise,setPdfCpf:async()=>{pdfSaved=true;},clearPdfCpf:async()=>{pdfSaved=false;}},
  '../utils/sessionClient':{api:{json:(...args)=>securityRequest(...args)}},
  './AuthProvider':{useAuth:()=>({account:{username:'synthetic',display_name:'Synthetic'},setAccount(){},replaceSession:async()=>{}}),AuthButton:({title,onPress,disabled})=>React.createElement('Pressable',{accessibilityLabel:title,onPress,disabled},React.createElement('Text',null,title)),authStyles:{},deviceLabel:'synthetic'},
  'expo-application':{nativeBuildVersion:'68',nativeApplicationVersion:'1.20.22'},'expo-linking':{openURL:async()=>{}}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};cache.set(file,m.exports);
  const candidate=process.env.STATE_TRANSITIONS_ORIGINAL&&path.join(process.env.STATE_TRANSITIONS_ORIGINAL,path.relative(process.cwd(),file));
  const source=candidate&&fs.existsSync(candidate)?candidate:file;
  const code=ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  vm.runInNewContext(code,{module:m,exports:m.exports,console,Error,Date,Intl,Math,URLSearchParams,AbortController,setTimeout,clearTimeout,
    require:name=>{if(mocks[name])return mocks[name];const target=path.resolve(path.dirname(file),name);if(name.endsWith('.json'))return JSON.parse(fs.readFileSync(target,'utf8'));return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:source});cache.set(file,m.exports);return m.exports;}
const Pdf=load('components/PdfPasswordSettings.tsx').default,Update=load('components/AppUpdate.tsx').default,Card=load('components/CardManager.tsx').default,Transaction=load('components/TransactionEditor.tsx').default,Delete=load('components/DeleteMovement.tsx').default,Future=load('components/FutureCommitmentEditor.tsx').default,Security=load('components/AccountSecurity.tsx').default;
const queue=load('utils/syncQueue.ts');
const ChoiceProvider=load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider;
const settle=(ms=15)=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
const mount=async element=>act(async()=>{renderer=create(React.createElement(ChoiceProvider,null,element));});
const button=label=>{const node=renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label||node.findAllByType('Text').some(text=>text.props.children===label));assert.ok(node,'Button: '+label);return node;};
const press=label=>act(async()=>{const node=button(label);assert.ok(!node.props.disabled,label+' enabled');node.props.onPress();});
const input=(label,value)=>act(async()=>renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel===label).props.onChangeText(value));
const text=()=>renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join('')).join('\n');
async function check(name,work){dialogs.length=0;try{await work();console.log('PASS',name);}catch(error){failures.push(name);console.error('FAIL',name,error.message);}finally{if(renderer){await act(async()=>renderer.unmount());renderer=null;}await act(async()=>{cleanups.splice(0).forEach(cleanup=>cleanup());});await settle();assert.equal(load('utils/userOperation.ts').isUserOperationBusy(),false,'Every case releases its gate before the next case');}}
(async()=>{
  await check('Suspension stops a queue cycle without backoff or replay, preserving the same event',async()=>{
    const row={id:1,event_id:'synthetic-event',kind:'capture',payload:'{}',local_uri:null,filename:null,mime_type:null};let sends=0,recorded=0,removed=0,changed,abort=true,rows=[row];
    const send=deferred();const sync=queue.createQueueSync({prepare:async()=>true,list:async()=>rows,send:async()=>{sends++;if(abort)return send.promise;return{persisted:true,event_id:row.event_id}},remove:async()=>{removed++;rows=[];},recordFailure:async()=>{recorded++;},done(){},changed:(count,error)=>{changed={count,error};}});
    const task=sync();await settle();const duplicate=sync();send.reject(Object.assign(new Error('Consulta cancelada'),{name:'AbortError'}));await task;await duplicate;
    assert.equal(recorded,0);assert.equal(sends,1);assert.equal(changed.count,1);assert.equal(changed.error,'');assert.equal(removed,0);
    abort=false;await sync();assert.equal(sends,2);assert.equal(removed,1);
  });
  await check('Suspension after ACK preserves the receipt and never repeats transport',async()=>{
    const row={id:2,event_id:'synthetic-receipt',kind:'capture',payload:'{}',local_uri:null,filename:null,mime_type:null};let rows=[row],sends=0,aborted=true,failures=0;
    const sync=queue.createQueueSync({prepare:async()=>true,list:async()=>rows,send:async()=>{sends++;return{persisted:true,event_id:row.event_id}},remove:async()=>{rows=[];},recordFailure:async()=>{failures++;},done:()=>{if(aborted)throw Object.assign(new Error('Consulta cancelada'),{name:'AbortError'});},changed(){}});
    await sync();assert.equal(sends,1);assert.equal(failures,0);aborted=false;await sync();assert.equal(sends,1);assert.equal(rows.length,0);
  });
  await check('CPF ACK releases the local lock and an older read cannot reverse saved settings',async()=>{
    pdfRead=deferred();pdfSaved=false;const refresh=deferred();await mount(React.createElement(Pdf,{disabled:false,onChanged:()=>refresh.promise}));await press('CPF para PDFs com senha');await input('CPF para abrir PDFs','00000000000');await press('Salvar CPF para PDFs');await settle();
    assert.equal(pdfSaved,true);assert.equal(button('Salvar CPF para PDFs').props.disabled,false);await act(async()=>pdfRead.resolve(null));assert.match(text(),/CPF informado/);
    await act(async()=>refresh.reject(new Error('Synthetic refresh failed')));assert.match(text(),/CPF salvo. Não foi possível retomar a fila/);assert.equal(pdfSaved,true);
  });
  await check('Update reads are discarded after unmount without late dialogs',async()=>{
    const read=deferred();await mount(React.createElement(Update,{api:()=>read.promise,baseUrl:'https://synthetic.invalid'}));await settle();await act(async()=>renderer.unmount());renderer=null;await act(async()=>read.reject(new Error('Synthetic stale read')));assert.equal(dialogs.length,0);
  });
  await check('Card conflicts preserve drafts, rebase untouched fields and require overlapping differences to be accepted',async()=>{
    let card={id:'card',instituicao:'Banco anterior',apelido:'Nome anterior',cartao_final:'1234',bill_count:0,deleted:false,token:'v1'},conflict=true,body;
    const api=async(route,init={})=>{if(init.method==='PATCH'){body=JSON.parse(init.body);if(conflict){conflict=false;card={...card,instituicao:'Banco atual',apelido:'Nome remoto',token:'v2'};throw Object.assign(new Error('Fonte mudou'),{status:409});}assert.equal(body.expected_token,'v2');card={...card,...body,token:'v3'};return{item:card};}return{items:[card]};};
    const refresh=deferred();await mount(React.createElement(Card,{api,cards:[card],money:String,onUpdated:()=>refresh.promise}));await settle();await press('Editar cartão Nome anterior');await input('Nome do cartão','Meu nome');await press('Salvar alterações');await settle();assert.equal(button('Salvar alterações').props.disabled,true);assert.equal(renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel==='Nome do cartão').props.value,'Meu nome');
    await press('Carregar cadastro atual e conferir');await settle();assert.equal(renderer.root.findAllByType('TextInput').find(node=>node.props.accessibilityLabel==='Instituição').props.value,'Banco atual');assert.equal(button('Salvar alterações').props.disabled,true);
    await press('Conferi as diferenças e quero reaplicar meus campos');await press('Salvar alterações');await settle();assert.equal(body.apelido,'Meu nome');assert.equal(body.instituicao,'Banco atual');assert.equal(dialogs.at(-1)[0],'Alteração salva');assert.equal(button('Mostrar excluídos').props.disabled,false,'Aggregate refresh never keeps the gate');
  });
  await check('Card settings validate, roundtrip and explicitly clear optional fields',async()=>{
    let card={id:'settings',instituicao:'Banco',apelido:'Meu cartao',cartao_final:'4242',bill_count:0,deleted:false,token:'v1',validade:'2030-12',dia_vencimento:13,dia_fechamento:3,melhor_dia_compra:4,observacoes:'Original'},body,sends=0;
    const api=async(route,init={})=>{if(init.method==='PATCH'){sends++;body=JSON.parse(init.body);card={...card,...body,token:'v'+(sends+1)};return{item:card};}return{items:[card]};};
    await mount(React.createElement(Card,{api,cards:[card],money:String,onUpdated:async()=>{}}));await settle();
    await press('Editar cartão Meu cartao');
    const value=label=>renderer.root.findAllByType('TextInput').find(n=>n.props.accessibilityLabel===label).props.value;
    assert.equal(value('Validade do cartão (MM/AAAA)'),'12/2030');
    await input('Dia de vencimento da fatura (1 a 31)','32');await press('Salvar alterações');assert.equal(sends,0);
    await input('Dia de vencimento da fatura (1 a 31)','13');await input('Validade do cartão (MM/AAAA)','13/2030');await press('Salvar alterações');assert.equal(sends,0);
    await input('Validade do cartão (MM/AAAA)','12/2031');await press('Salvar alterações');await settle();
    assert.equal(body.validade,'2031-12');assert.equal(body.dia_vencimento,13);assert.equal(body.melhor_dia_compra,4);
    await press('Editar cartão Meu cartao');assert.equal(value('Validade do cartão (MM/AAAA)'),'12/2031');
    for(const label of ['Validade do cartão (MM/AAAA)','Dia de vencimento da fatura (1 a 31)','Dia de fechamento da fatura (1 a 31)','Melhor dia para compras (1 a 31)','Observações do cartão'])await input(label,'');
    await press('Salvar alterações');await settle();
    for(const key of ['validade','dia_vencimento','dia_fechamento','melhor_dia_compra','observacoes'])assert.equal(body[key],null);
    await press('Editar cartão Meu cartao');assert.equal(value('Validade do cartão (MM/AAAA)'),'');
  });
  await check('Clearing a vendor while a preview is in flight resets loading and discards its result',async()=>{
    const read=deferred(),api=route=>route.includes('supplier-match-preview')?read.promise:Promise.resolve(route.endsWith('classification-options')?{}:{total:0});
    await mount(React.createElement(Transaction,{item:{id:'tx',valor:-10,vendedor:'Original'},api,money:String,onSaved(){},onCancel(){}}));await input('Vendedor','Loja nova');await settle(540);assert.match(text(),/Buscando registros semelhantes/);await input('Vendedor','');await settle();assert.ok(!text().includes('Buscando registros semelhantes'));
    await act(async()=>read.resolve({total:100,preview_token:'old'}));assert.ok(!text().includes('100 registro'));
  });
  await check('An acknowledged deletion cannot notify a screen that has already unmounted',async()=>{
    const ack=deferred();let callbacks=0;await mount(React.createElement(Delete,{item:{id:'tx'},api:(_route,init={})=>init.method==='DELETE'?ack.promise:Promise.resolve({can_delete:true,preview_token:'token'}),onDeleted:()=>{callbacks++;}}));await press('Excluir registro');await press('Confirmar exclusão');await act(async()=>renderer.unmount());renderer=null;await act(async()=>ack.resolve({deleted:true}));assert.equal(callbacks,0);assert.equal(dialogs.length,0);
  });
  await check('A future schedule ACK releases the lock while the parent refresh is delayed',async()=>{
    const api=async(_route,init={})=>init.method==='DELETE'?{persisted:true,source:'obligation',identity:'future'}:{rows:[{editable:true,row_id:1,amount:10,due_date:'2026-10-06'}],editable_count:1,preview_token:'token'};
    const refresh=deferred();await mount(React.createElement(Future,{source:'obligation',identity:'future',name:'Synthetic future',api,onUpdated:()=>refresh.promise}));await press('Remover parcelas futuras de Synthetic future');await press('Confirmar remoção');await settle();assert.equal(button('Remover parcelas futuras de Synthetic future').props.disabled,false);assert.equal(dialogs.at(-1)[0],'Compromissos futuros excluídos');
  });
  await check('Password ACK is not reported as a failed change when the sessions refresh fails',async()=>{
    let reads=0;const refresh=deferred();securityRequest=async(route,init={})=>{if(init.method==='POST')return{access_token:'synthetic'};if(route.endsWith('/sessions')){reads++;if(reads===1)return{items:[]};return refresh.promise;}throw new Error(route);};
    await mount(React.createElement(Security));await input('Senha atual','synthetic current');await input('Nova senha','synthetic password');await input('Repita a nova senha','synthetic password');await press('Atualizar senha');await settle();assert.equal(button('Atualizar senha').props.disabled,false);assert.match(text(),/Senha atualizada/);await act(async()=>refresh.reject(new Error('Synthetic sessions failed')));assert.match(text(),/Senha atualizada.*Não foi possível atualizar as sessões/);
  });
  if(failures.length){console.error(`${failures.length} state-transition regressions failed`);process.exitCode=1;}else console.log('9 state-transition regressions passed; no real data, credentials or native device used.');
})().catch(error=>{console.error(error);process.exitCode=1;});
