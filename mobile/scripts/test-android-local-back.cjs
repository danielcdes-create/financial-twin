// App + dialog/selector/operation hosts are real; native dispatch and I/O are synthetic.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{createRequire}=require('node:module');
const filename=path.resolve('scripts/test-filter-navigation.cjs');
const prelude=fs.readFileSync(filename,'utf8').split('\n(async()=>{')[0];
const extra=`
const handlers=[];let route='/';
mocks['expo-router'].usePathname=()=>route;
mocks['react-native'].Keyboard={dismiss(){}};
mocks['react-native'].AppState={currentState:'active',addEventListener:()=>({remove(){}})};
mocks['react-native'].BackHandler={addEventListener:(name,callback)=>{const entry={name,callback};handlers.push(entry);return{remove(){const index=handlers.indexOf(entry);if(index>=0)handlers.splice(index,1)}}}};
delete mocks['../utils/appAlert'];delete mocks['./utils/appAlert'];
const Boundary=load('components/UserOperationBoundary.tsx').default;
const Host=load('components/AppDialogHost.tsx').default;
const choices=load('components/ChoiceOverlay.tsx'),gate=load('utils/userOperation.ts'),dialogs=load('utils/appAlert.ts');
function OpenChoice(){const overlay=choices.useChoiceOverlay();return React.createElement('Pressable',{onPress:()=>overlay.open({owner:'fixture',label:'Synthetic choices',value:'',options:[{value:'one',label:'One'}],allowClear:true,onSelect(){}})},React.createElement('Text',null,'Open fixture selector'));}
const tree=()=>React.createElement(React.Fragment,null,React.createElement(Boundary,null,React.createElement(choices.ChoiceOverlayProvider,null,React.createElement(App),React.createElement(OpenChoice))),React.createElement(Host));
const shareTree=()=>React.createElement(React.Fragment,null,React.createElement(Boundary,null,React.createElement(choices.ChoiceOverlayProvider,null,React.createElement(App,{key:'index'}),React.createElement(App,{key:'share',navigationRoute:'/handle-share',initialPurchaseError:'Synthetic share'}))),React.createElement(Host));
const hardwareBack=()=>{for(const entry of handlers.slice().reverse())if(entry.callback())return true;return false;};
const appBackCount=()=>handlers.filter(entry=>entry.callback===load('utils/localBack.ts').dispatchLocalBack).length;
const stub=name=>renderer.root.findAllByType(name+'Stub').length;
(async()=>{
 await act(async()=>{renderer=create(tree(),{createNodeMock:()=>({scrollTo(){}})})});await settle();
 await press('Mais');
 for(const [label,component] of [['Decisões e evidências','FinancialLab'],['Fechamento do mês','MonthClose'],['Cabe na minha vida?','PurchaseCheck'],['Rascunhos','DraftManager'],['Contas','AccountPositions'],['Cartões','CardManager'],['Categorias','CategoryManager'],['Compromissos','CommitmentManager'],['Ajustes','AppUpdate']]){
   await press(label);if(label!=='Ajustes')assert.equal(stub(component),1,label);await act(async()=>assert.equal(hardwareBack(),true,label));assert.ok(button('Decisões e evidências'),label+' returns to More');
 }
 for(const [label,component] of [['Contas','AccountPositions'],['Cartões','CardManager'],['Categorias','CategoryManager'],['Compromissos','CommitmentManager'],['Ajustes','AppUpdate']]){
   await press(label);if(label!=='Ajustes')assert.equal(stub(component),1,label);await press('‹ Voltar a Mais');assert.ok(button('Decisões e evidências'),label+' UI Back button returns to More');
 }
 await press('Decisões e evidências');await press('Open fixture selector');await act(async()=>assert.equal(hardwareBack(),true));assert.equal(stub('FinancialLab'),1,'Overlay closes before leaving its page');
 await act(async()=>dialogs.Alert.alert('Synthetic dialog','Keep page',[{text:'Cancel',style:'cancel'}]));await act(async()=>assert.equal(hardwareBack(),true));assert.equal(dialogs.dialogSnapshot(),null);assert.equal(stub('FinancialLab'),1,'Dialog cancels before navigation');
 let release;await act(async()=>{gate.runUserAction(()=>new Promise(resolve=>{release=resolve;}));});await act(async()=>assert.equal(hardwareBack(),true));assert.equal(stub('FinancialLab'),1);await act(async()=>release());assert.equal(stub('FinancialLab'),1,'Blocked Back is never replayed');
 route='/account';await act(async()=>renderer.update(tree()));await act(async()=>assert.equal(hardwareBack(),false,'Inactive index does not consume another Router route Back'));
 route='/';await act(async()=>renderer.update(tree()));await act(async()=>assert.equal(hardwareBack(),true));await press('Início');await act(async()=>assert.equal(hardwareBack(),false,'Top-level tab delegates native default'));
 await act(async()=>renderer.unmount());renderer=null;assert.equal(handlers.length,0);
 route='/handle-share';await act(async()=>{renderer=create(shareTree(),{createNodeMock:()=>({scrollTo(){}})})});await settle();assert.equal(appBackCount(),1,'Only shared App owns native Back while index remains mounted');assert.equal(stub('PurchaseCheck'),1);await act(async()=>assert.equal(hardwareBack(),true));assert.equal(stub('PurchaseCheck'),0,'Shared App subtab returns to its own More page');
 route='/';await act(async()=>renderer.update(shareTree()));assert.equal(appBackCount(),1,'Only index App owns Back after returning to root');await act(async()=>assert.equal(hardwareBack(),false,'Index default cannot navigate hidden share App'));
 await act(async()=>renderer.unmount());renderer=null;assert.equal(handlers.length,0);console.log('PASS all More subtabs, overlays, dialogs, synchronous mutation lock/no replay, both App routes and native default');
})().catch(async error=>{console.error(error);if(renderer)await act(async()=>renderer.unmount());process.exitCode=1});
`;
vm.runInNewContext(prelude+extra,{require:createRequire(filename),console,process,global,URL,URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval,clearInterval,Promise},{filename});
