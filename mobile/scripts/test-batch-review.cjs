// Exercise the real review component, local drafts and request bodies.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('typescript'),React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const cache=new Map(),drafts=new Map(),requests=[];
let renderer,dialog,failDelete=false,failCommit=false,pendingPreview=null,holdPreview=false,failDraftCleanup=false;
const resolved=[];
const sha='a'.repeat(64),sourceRows=[1,2,3].map(row_id=>({row_id,date:'2026-10-01',amount:'-12.59',description:`Loja ${row_id}`,kind:'expense',bank_ref:null}));
let batches=['first','second'].map(batch_id=>({batch_id,sha256:sha,filename:'extrato.pdf',row_count:3,created_at:'2026-10-02T12:00:00Z'}));
const db={getAllAsync:async(_sql,id)=>drafts.has(id)?[{state:drafts.get(id)}]:[],runAsync:async(sql,id,state)=>{if(sql.startsWith('DELETE')){if(failDraftCleanup)throw Error('Falha na limpeza local');drafts.delete(id);}else drafts.set(id,state);}};
const native={StyleSheet:{create:value=>value},Platform:{OS:'android'},Keyboard:{dismiss(){}},
 AppState:{addEventListener:()=>({remove(){}})},BackHandler:{addEventListener:()=>({remove(){}})},
 FlatList:({data,renderItem,ListEmptyComponent})=>React.createElement('View',null,data.length?data.map(item=>React.createElement(React.Fragment,{key:item.value},renderItem({item}))):ListEmptyComponent),
 ...Object.fromEntries(['Pressable','Text','TextInput','View'].map(name=>[name,name]))};
const mocks={'react':React,'react-native':native,'react-native-safe-area-context':{SafeAreaView:'SafeAreaView'},'../utils/queueDb':{useSQLiteContext:()=>db},'../utils/appAlert':{Alert:{alert:(...args)=>{dialog=args;}}}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};
 const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
 vm.runInNewContext(code,{module:m,exports:m.exports,console,setTimeout,clearTimeout,AbortController,Date,Promise,require:name=>{
  if(mocks[name])return mocks[name];if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}throw Error(name);
 }},{filename:file});cache.set(file,m.exports);return m.exports;}
function report(body){const rows=body.rows.map(row=>({row_id:row.row_id,status:row.row_id===1?'duplicate':row.row_id===2?'possible_duplicate':'new',confidence:row.row_id===2?'high':'confirmed',reason:'Mesma conta, data e valor.',selected:row.selected,will_insert:row.selected&&(row.row_id===3||(row.row_id===2&&row.confirmed_distinct)),matches:row.row_id<3?[{source:'ledger',date:'2026-10-01',amount:'-12.59',description:'Loja existente',account_known:true}]:[]}));
 return {rows,new_count:1,duplicate_count:1,possible_duplicate_count:1,insert_count:rows.filter(row=>row.will_insert).length,scan_complete:true,preview_token:'b'.repeat(64)};}
async function api(url,init={}){const method=init.method||'GET',body=init.body?JSON.parse(init.body):null;requests.push({url,method,body,signal:init.signal});
 if(url.includes('?'))return {items:batches,has_more:false};
 if(url.endsWith('/preview')){if(holdPreview){holdPreview=false;return new Promise(resolve=>{pendingPreview=()=>resolve(report(body));});}return report(body);}
 if(method==='DELETE'){if(failDelete)throw Error('Falha simulada');assert.equal(body.expected_sha256,sha);const batch_id=url.split('/').at(-1);batches=batches.filter(row=>row.batch_id!==batch_id);return {batch_id,status:'discarded',persisted:true};}
 if(url.endsWith('/commit')){if(failCommit)throw Error('Falha ao confirmar');return {batch_id:url.split('/').at(-2),persisted:true,inserted_count:1,skipped_count:2,rows:[]};}
 return {...batches.find(row=>row.batch_id===url.split('/').at(-1)),status:'draft',complete:true,rows:sourceRows,warnings:[]};}
const Screen=load('components/StatementBatchReview.tsx').default;
const {ChoiceOverlayProvider}=load('components/ChoiceOverlay.tsx');
const props={api,visible:true,revision:0,accounts:[{id:'account',tipo:'conta_corrente',instituicao:'Conta teste'},{id:'other',tipo:'conta_corrente',instituicao:'Outra conta'}],profiles:[],onUpdated:async()=>{},onResolved:id=>resolved.push(id)};
const settle=(ms=20)=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,ms));});
function button(label,index=0){const matches=renderer.root.findAllByType('Pressable').filter(node=>node.props.accessibilityLabel===label);assert.ok(matches[index],label);return matches[index];}
const press=(label,index=0)=>act(async()=>{const node=button(label,index);assert.ok(!node.props.disabled,label+' enabled');node.props.onPress();});
const choose=async value=>{await press('Selecionar Conta ou cartão do extrato');await press(`Escolher ${value==='ledger:account'?'Conta teste':'Outra conta'} · Conta`);};
const text=()=>renderer.root.findAllByType('Text').map(node=>[].concat(node.props.children).join('')).join('\n');
const previewCount=()=>requests.filter(row=>row.url.endsWith('/preview')).length;
const deletes=()=>requests.filter(row=>row.method==='DELETE').length;
function assertResultsBelowEditors(){
 const hosts=renderer.root.findAll(node=>typeof node.type==='string');
 const lastInput=Math.max(...hosts.map((node,index)=>node.type==='TextInput'?index:-1));
 const summary=hosts.findIndex(node=>node.type==='Pressable'&&node.props.accessibilityLabel==='Selecionar só as novas');
 const match=hosts.findIndex(node=>node.type==='Text'&&[].concat(node.props.children).join('').includes('Loja existente'));
 assert.ok(summary>lastInput&&match>lastInput,'Async summaries and matching evidence must stay below every row editor');
}
(async()=>{
 await act(async()=>{renderer=create(React.createElement(ChoiceOverlayProvider,null,React.createElement(Screen,props)));});await settle();
 assert.match(text(),/Há outro lote deste mesmo arquivo/);
 await press('Remover lote extrato.pdf');assert.equal(dialog[0],'Remover lote pendente?');assert.equal(deletes(),0,'Opening confirmation never removes a batch');
 await press('Revisar extrato.pdf');await settle();assert.equal(previewCount(),0,'Account selection is required');
 await choose('ledger:account');await settle(510);assert.equal(previewCount(),1,'Comparison runs automatically');
 assertResultsBelowEditors();
 const rowEditor=renderer.root.findByProps({accessibilityLabel:'Descrição da linha 3'});
 assert.match(text(),/Já está no app/);assert.match(text(),/Provavelmente já está no app/);assert.match(text(),/Loja existente/);assert.match(text(),/01\/10\/2026/);
 assert.equal(button('Incluir linha 1').props.disabled,true);assert.equal(button('Incluir linha 1').props.accessibilityState.checked,false);
 await press('Confirmar linha 2 como diferente');await settle(510);assert.equal(requests.filter(row=>row.url.endsWith('/preview')).at(-1).body.rows[1].confirmed_distinct,true);
 await choose('ledger:other');await settle(510);assert.ok(requests.filter(row=>row.url.endsWith('/preview')).at(-1).body.rows.every(row=>!row.confirmed_distinct),'Changing account invalidates distinct confirmations');
 await press('Selecionar só as novas');await settle(510);const fresh=requests.filter(row=>row.url.endsWith('/preview')).at(-1).body;
 assert.deepEqual(fresh.rows.map(row=>row.selected),[false,false,true]);assert.ok(fresh.rows.every(row=>!row.confirmed_distinct));assert.equal(sourceRows[0].selected,undefined,'Source extraction is preserved');
 holdPreview=true;await act(async()=>renderer.root.findByProps({accessibilityLabel:'Descrição da linha 3'}).props.onChangeText('Alteração anterior'));await settle(510);assert.ok(pendingPreview);
 assert.equal(renderer.root.findByProps({accessibilityLabel:'Descrição da linha 3'}),rowEditor,'Recomparison retains the mounted editor and keyboard focus');
 await act(async()=>renderer.root.findByProps({accessibilityLabel:'Descrição da linha 3'}).props.onChangeText('Alteração atual'));await act(async()=>pendingPreview());assert.doesNotMatch(text(),/1 novas · 1 já existentes/,'Late response cannot restore stale preview');
 await settle(510);assert.equal(requests.filter(row=>row.url.endsWith('/preview')).at(-1).body.rows[2].description,'Alteração atual');
 await press('Fechar e retomar depois');await settle();await press('Revisar extrato.pdf');await settle(510);assert.equal(renderer.root.findByProps({accessibilityLabel:'Descrição da linha 3'}).props.value,'Alteração atual','Review edits survive close and reopen');
 await press('Fechar e retomar depois');await settle();drafts.set('second','preserved-second');const originalFirst=drafts.get('first');failDelete=true;
 await press('Remover lote extrato.pdf');await act(async()=>dialog[2].find(row=>row.text==='Remover lote').onPress());await settle();assert.equal(batches.length,2);assert.equal(drafts.get('first'),originalFirst,'Failed removal retains local draft');assert.equal(drafts.get('second'),'preserved-second');
 failDelete=false;await press('Remover lote extrato.pdf');await act(async()=>dialog[2].find(row=>row.text==='Remover lote').onPress());await settle();assert.equal(batches.length,1);assert.equal(batches[0].batch_id,'second');assert.equal(drafts.has('first'),false);assert.equal(drafts.get('second'),'preserved-second','Only the acknowledged batch draft is deleted');
 assert.equal(deletes(),2);assert.deepEqual(resolved,['first'],'Only acknowledged removal resolves its notice');
 drafts.delete('second');await press('Revisar extrato.pdf');await settle();await choose('ledger:account');await settle(510);
 assert.equal(button('Inserir 1 movimentações').props.disabled,true,'Selected unresolved coincidences must be reviewed before committing');
 await press('Selecionar só as novas');await settle(510);
 failCommit=true;await press('Inserir 1 movimentações');await settle();assert.deepEqual(resolved,['first'],'A failed commit keeps the pending notice');
 failCommit=false;failDraftCleanup=true;await press('Inserir 1 movimentações');await settle();assert.deepEqual(resolved,['first','second'],'Persisted commit resolves the matching notice');
 assert.match(text(),/Lote confirmado no servidor/,'Local cleanup failure cannot turn a persisted import into a failed write');
 assert.doesNotMatch(text(),/Falha na limpeza local/,'Cleanup failure is surfaced as confirmed outcome with retained local draft');
 assert.equal(drafts.has('second'),true,'Cleanup failure preserves the completed review');
 failDraftCleanup=false;
 batches.push({batch_id:'currency-draft',sha256:sha,filename:'sem-moeda.csv',row_count:3});
 for(const row of sourceRows)row.currency=null;
 await press('Voltar aos lotes');await press('Atualizar lotes');await settle();
 await press('Revisar sem-moeda.csv');await settle();await choose('ledger:account');await settle(510);
 assert.equal(requests.filter(row=>row.url.endsWith('/preview')).at(-1).body.acknowledge_brl,false,'Unknown currency starts unacknowledged');
 assert.equal(button('Confirmar moeda BRL das linhas sem moeda').props.accessibilityState.checked,false);
 await press('Confirmar moeda BRL das linhas sem moeda');await settle(510);
 const currencyReview=requests.filter(row=>row.url.endsWith('/preview')).at(-1).body;
 assert.equal(currencyReview.acknowledge_brl,true);assert.ok(currencyReview.rows.every(row=>row.currency===null),'BRL acknowledgement retains unknown source evidence');
 await press('Fechar e retomar depois');await press('Revisar sem-moeda.csv');await settle(510);
 assert.equal(button('Confirmar moeda BRL das linhas sem moeda').props.accessibilityState.checked,true,'Currency acknowledgement survives close/reopen');
 await act(async()=>renderer.unmount());
 console.log('Batch review UI: automatic comparison, evidence, account-scoped confirmation, select new, stale response, retained edits, removal confirmation and ACK-only cleanup — passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
