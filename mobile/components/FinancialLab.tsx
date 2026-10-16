import {accountLabel} from '../utils/accountProfile';
import {categoryOptions} from '../utils/movementFilters';
import { Alert } from "../utils/appAlert";
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {ActivityIndicator, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import ControlledChoice from './ControlledChoice';
import {useAsyncScope, useScopedRequest} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';
import {requireMoneyInput} from '../utils/moneyInput';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

type Props={api:(path:string,init?:RequestInit)=>Promise<any>;onBack:()=>void};
type Section='Fechamento'|'Guardião'|'Simular'|'Liquidez'|'Experimentos';
const day=(offset=0)=>{const d=new Date();d.setDate(d.getDate()+offset);return new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(d)};
const newId=()=> 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return(c==='x'?r:(r&3)|8).toString(16)});
const decimal=(value:string)=>requireMoneyInput(value,{signed:true});
const currency=(value:any)=>Number(value||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
function Button({label,onPress,disabled=false}:{label:string;onPress:()=>void;disabled?:boolean}){return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={[s.button,disabled&&{opacity:.5}]}><Text style={s.buttonText}>{label}</Text></Pressable>}
const FormBusy=React.createContext(false);
function Field({label,value,onChange,multiline=false,disabled=false}:{label:string;value:string;onChange:(value:string)=>void;multiline?:boolean;disabled?:boolean}){const busy=React.useContext(FormBusy);return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} editable={!disabled&&!busy} value={value} onChangeText={onChange} style={[s.input,multiline&&{minHeight:90}]} multiline={multiline} autoCapitalize="none"/></View>}
function Notice({children}:{children:React.ReactNode}){return <Text style={s.notice}>{children}</Text>}
const confirmAction=(title:string,message:string,buttons:any[])=>Alert.alert(title,message,buttons);
const statusLabel=(status:string)=>({open:'Aberto',awaiting_refund:'Aguardando reembolso',resolved:'Resolvido',dismissed:'Descartado',active:'Ativo',completed:'Concluído',cancelled:'Cancelado'} as Record<string,string>)[status]||status;
const scenarioNames:Record<string,string>={baseline:'Sem nova compra',buy_now:'Comprar agora',wait:'Esperar',reduce:'Reduzir valor'};

export default function FinancialLab({api:request,onBack}:Props){
  const captureScope=useAsyncScope(), api=useScopedRequest(request,captureScope);
  const [section,setSection]=useState<Section>('Fechamento');const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [start,setStart]=useState(day(-30)),[end,setEnd]=useState(day());const [closure,setClosure]=useState<any>(null),[trail,setTrail]=useState<any>(null);
  const [closurePeriod,setClosurePeriod]=useState(()=>({start,end}));
  const [cases,setCases]=useState<any[]>([]),[caseCursor,setCaseCursor]=useState<string|null>(null),[detections,setDetections]=useState<any[]>([]);const [dossier,setDossier]=useState('');
  const [title,setTitle]=useState(''),[expected,setExpected]=useState(''),[due,setDue]=useState(day(7)),[notes,setNotes]=useState(''),[caseKind,setCaseKind]=useState('missing_refund'),[sourceRef,setSourceRef]=useState(''),[refundRef,setRefundRef]=useState('');
  const [context,setContext]=useState<any>(null),[opening,setOpening]=useState(''),[reserve,setReserve]=useState('0'),[purchase,setPurchase]=useState(''),[reduced,setReduced]=useState(''),[count,setCount]=useState('1'),[first,setFirst]=useState(day()),[wait,setWait]=useState('30'),[flowsText,setFlowsText]=useState(''),[simulationResult,setSimulation]=useState<any>(null),[liquidityResult,setLiquidity]=useState<any>(null);
  const [flowChoices,setFlowChoices]=useState<Record<string,boolean>>({}),[flowAccounts,setFlowAccounts]=useState<Record<string,string>>({});
  const premiseKey=JSON.stringify([day(),opening,reserve,purchase,reduced,count,first,wait,flowsText,flowChoices,flowAccounts,context]);
  const latestPremises=useRef(premiseKey),analysisGeneration=useRef(0);
  useLayoutEffect(()=>{latestPremises.current=premiseKey;analysisGeneration.current++;},[premiseKey]);
  const simulation=simulationResult?.premiseKey===premiseKey?simulationResult.data:null,liquidity=liquidityResult?.premiseKey===premiseKey?liquidityResult.data:null;
  const analyze=async(path:string,body:any,setter:(result:any)=>void)=>{
    const current=++analysisGeneration.current,key=premiseKey,response=await send(path,body);
    if(current===analysisGeneration.current&&latestPremises.current===key)setter({premiseKey:key,data:response});
  };
  const [selectedScenario,setSelectedScenario]=useState('buy_now');const [experiments,setExperiments]=useState<any[]>([]),[experimentCursor,setExperimentCursor]=useState<string|null>(null),[experimentReport,setExperimentReport]=useState<any>(null);
  const [experimentTitle,setExperimentTitle]=useState(''),[category,setCategory]=useState(''),[categories,setCategories]=useState<string[]>([]),[protocol,setProtocol]=useState(''),[baseStart,setBaseStart]=useState(day(-30)),[baseEnd,setBaseEnd]=useState(day(-1)),[experimentStart,setExperimentStart]=useState(day()),[experimentEnd,setExperimentEnd]=useState(day(29)),[target,setTarget]=useState('10'),[satisfaction,setSatisfaction]=useState('3'),[confounders,setConfounders]=useState('');
  const [createCaseId,setCreateCaseId]=useState(newId),[createExperimentId,setCreateExperimentId]=useState(newId);
  const back=()=>protectUnsavedBack(Boolean(title||expected||notes||sourceRef||experimentTitle||category||protocol||confounders||opening||purchase||reduced||flowsText||reserve!=='0'||count!=='1'||first!==day()||wait!=='30'||Object.keys(flowChoices).length||Object.keys(flowAccounts).length),onBack);
  useLocalBack(back,true,10);
  const pendingTasks=useRef(0);
  const closureGeneration=useRef(0);
  const run=useCallback(async(task:()=>Promise<void>)=>{const valid=captureScope();pendingTasks.current++;setBusy(true);setError('');try{await task()}catch(e:any){if(valid())setError(e.message||'Falha ao processar.')}finally{pendingTasks.current--;if(valid()&&!pendingTasks.current)setBusy(false)}},[captureScope]);
  const refreshSaved=async(task:()=>Promise<void>)=>{try{await task()}catch{throw new Error('Alteração salva. Não foi possível atualizar a lista. Abra esta seção novamente para conferir.')}};
  const send=(path:string,body:any,method='POST')=>api('/v1/insights'+path,{method,body:JSON.stringify(body)});
  const loadClosure=useCallback(async()=>{const current=++closureGeneration.current;const response=await api(`/v1/insights/closure?start=${closurePeriod.start}&end=${closurePeriod.end}`);if(current===closureGeneration.current)setClosure({...response,displayedPeriod:closurePeriod})},[api,closurePeriod]);
  const loadCases=async()=>{const result=await api('/v1/insights/cases');setCases(result.items);setCaseCursor(result.next_cursor)};
  const loadExperiments=async()=>{const result=await api('/v1/insights/experiments');setExperiments(result.items);setExperimentCursor(result.next_cursor)};
  useEffect(()=>{const counter=closureGeneration;const timer=setTimeout(()=>run(loadClosure),0);return()=>{clearTimeout(timer);counter.current++}},[loadClosure,run]);
  const select=(next:Section)=>{setSection(next);setError('');return run(async()=>{
    if(next==='Guardião'){await loadCases();const result=await api(`/v1/insights/guardian-detections?start=${start}&end=${end}`);setDetections(result.items)}
    if(next==='Simular'||next==='Liquidez')setContext(await api(`/v1/insights/planning-context?start=${day()}&end=${day(89)}`));
    if(next==='Experimentos'){await loadExperiments();const result=await api('/v1/categories');setCategories(categoryOptions([...(result.items||[]),'Sem categoria']))}
  })};
  const movements=closure?.trail_preview||[];const refunds=movements.filter((m:any)=>m.role==='refund'&&Number(m.amount)>0);
  const discardForm=()=>{
    if(busy)return;
    setError('');
    if(section==='Guardião'){setTitle('');setExpected('');setDue(day(7));setNotes('');setCaseKind('missing_refund');setSourceRef('');setRefundRef('');setDossier('');setCreateCaseId(newId());}
    if(section==='Experimentos'){setExperimentTitle('');setCategory('');setProtocol('');setBaseStart(day(-30));setBaseEnd(day(-1));setExperimentStart(day());setExperimentEnd(day(29));setTarget('10');setSatisfaction('3');setConfounders('');setExperimentReport(null);setCreateExperimentId(newId());}
    if(section==='Simular'||section==='Liquidez'){setOpening('');setReserve('0');setPurchase('');setReduced('');setCount('1');setFirst(day());setWait('30');setFlowsText('');setFlowChoices({});setFlowAccounts({});setSimulation(null);setLiquidity(null);setSelectedScenario('buy_now');}
  };
  const flows=()=>{
    const manual=flowsText.split('\n').filter(line=>line.trim()).map((line,index)=>{const [date,value,label,account_key]=line.split(';').map(part=>part.trim());if(!date||!value||!label)throw new Error(`Fluxo ${index+1}: use data; valor; descrição; conta.`);return {id:`manual:${index}`,date,amount:decimal(value),label,account_key:account_key||null,certainty:'assumed'}});
    const imported=[...(context?.flows||[]),...(context?.suggested_flows||[])].filter((f:any)=>flowChoices[f.id]??!f.id.startsWith('obligation:')).map((f:any)=>({id:f.id,source_ref:f.source_ref,date:f.date,amount:f.amount,label:f.label,certainty:f.certainty,account_key:flowAccounts[f.id]||f.account_key||null}));
    return [...imported,...manual];
  };
  return <FormBusy.Provider value={busy}><View style={s.container}>
    <Button label="‹ Voltar" onPress={back}/><Text style={s.title}>Decisões e evidências</Text>
    <View style={s.tabs}>{(['Fechamento','Guardião','Simular','Liquidez','Experimentos'] as Section[]).map(name=><Pressable accessibilityRole="tab" accessibilityState={{selected:section===name}} aria-selected={section===name} key={name} onPress={()=>select(name)} style={[s.tab,section===name&&s.selected]}><Text style={s.text}>{name}</Text></Pressable>)}</View>
    {busy&&<ActivityIndicator color="#49C5AA"/>}{!!error&&<Text accessibilityRole="alert" style={s.error}>{error}</Text>}
    {(section==='Fechamento'||section==='Guardião')&&<View><Field label="Período inicial (AAAA-MM-DD)" value={start} onChange={setStart}/><Field label="Período final (AAAA-MM-DD)" value={end} onChange={setEnd}/></View>}
    {section==='Fechamento'&&<>
      <Notice>Confira o significado de cada movimento e as evidências que sustentam seus números.</Notice>
      <Button label="Atualizar fechamento" disabled={busy} onPress={()=>setClosurePeriod({start,end})}/>
      {closure?.displayedPeriod&&<Text style={s.notice}>Período exibido: {closure.displayedPeriod.start} a {closure.displayedPeriod.end}</Text>}
      {closure&&<><View style={s.card}><Text style={s.heading}>Receitas {currency(closure.income)}</Text><Text style={s.text}>Consumo líquido {currency(closure.expenses)}</Text><Text style={s.text}>{closure.movement_count} movimentos · {closure.refund_count} estornos</Text><Text style={s.text}>Resumo econômico por competência declarada; sem competência, pela data em Brasília. Saldos das contas usam a data efetiva dos movimentos.</Text><Notice>Cobertura incompleta. Extratos ausentes e contas sem referência impedem certificar o fechamento.</Notice>{closure.gaps.map((gap:string)=><Text key={gap} style={s.text}>• {gap}</Text>)}</View>
      {movements.map((item:any)=><Pressable accessibilityRole="button" key={item.ref} style={s.card} onPress={()=>run(async()=>setTrail(await api(`/v1/insights/trail?reference=${encodeURIComponent(item.ref)}`)))}><Text style={s.heading}>{item.label}</Text><Text style={s.text}>{currency(item.amount)} · {({neutral:'Neutro',expense:'Consumo',refund:'Estorno',income:'Receita',unknown:'A conferir'} as any)[item.role]}</Text><Text style={s.text}>{item.competencia?`Competência ${item.competencia}`:`Competência pela data do movimento`}</Text><Text style={s.link}>Ver evidências e vínculos</Text></Pressable>)}
      {closure.preview_truncated&&<Notice>Prévia dos 100 mais recentes. Consulte Movimentações para percorrer todo o período.</Notice>}</>}
      {trail&&<View style={s.card}><Text style={s.heading}>Trilha do movimento</Text><Notice>{trail.explanation}</Notice>{trail.nodes.map((node:any)=><View key={node.ref} style={s.field}><Text selectable style={s.text}>{node.kind}: {node.data.descricao_original||node.data.merchant_raw||node.data.name||node.data.event_id||node.ref}</Text>{node.kind==='evidence'&&<Button label="Desfazer este vínculo" disabled={busy} onPress={()=>confirmAction('Reabrir evidência?','A captura voltará à revisão. O histórico da decisão será preservado.',[{text:'Voltar'},{text:'Reabrir',onPress:()=>run(async()=>{await send(`/evidence/${node.data.event_id}/undo`,{request_id:newId(),expected_revision:node.data.revision||1,reason:'Usuário reabriu o vínculo pela trilha de evidências.'});setTrail(null);await refreshSaved(loadClosure)})}])}/>}</View>)}{trail.missing.length>0&&<Notice>Há {trail.missing.length} fontes referenciadas não encontradas.</Notice>}</View>}
    </>}
    {section==='Guardião'&&<>
      <Notice>Organize cobranças e reembolsos. Uma suspeita não é uma cobrança indevida comprovada. Nada é enviado a terceiros.</Notice>
      <Button label="Verificar cobranças do período" disabled={busy} onPress={()=>run(async()=>setDetections((await api(`/v1/insights/guardian-detections?start=${start}&end=${end}`)).items))}/>
      {detections.map((item,index)=><View style={s.card} key={index}><Text style={s.heading}>{item.title}</Text><Text style={s.text}>{item.explanation}</Text><Button label="Preparar caso" disabled={busy} onPress={()=>{setTitle(item.title);setCaseKind(item.kind);setSourceRef(item.source_refs?.[0]||'');setExpected(item.expected_amount||'0')}}/></View>)}
      <Field label="Título do caso" value={title} onChange={setTitle}/><ControlledChoice disabled={busy} label="Motivo" value={caseKind} onChange={setCaseKind} options={['missing_refund','unexpected_amount','post_cancellation','extra_installment','other']} display={v=>({missing_refund:'Reembolso não recebido',unexpected_amount:'Valor inesperado',post_cancellation:'Cobrança após cancelamento',extra_installment:'Parcela extra',other:'Outro'} as any)[v]}/>
      <Field label="Valor esperado (R$)" value={expected} onChange={setExpected}/><Field label="Data esperada (AAAA-MM-DD)" value={due} onChange={setDue}/>
      <ControlledChoice disabled={busy} label="Movimento de origem (opcional)" value={sourceRef} onChange={setSourceRef} options={movements.map((m:any)=>m.ref)} display={v=>{const m=movements.find((r:any)=>r.ref===v);return m?`${m.label} · ${currency(m.amount)}`:v}}/>
      <Field label="Documentação, protocolo e observações" value={notes} onChange={setNotes} multiline/>
      <Button label="Abrir caso" disabled={busy} onPress={()=>run(async()=>{await send('/cases',{request_id:createCaseId,title,kind:caseKind,expected_amount:decimal(expected),expected_date:due||null,source_refs:sourceRef?[sourceRef]:[],notes});setCreateCaseId(newId());setTitle('');await refreshSaved(loadCases)})}/>
      <DiscardDraftAction disabled={busy} owner={section} label="Excluir rascunho do caso" onDiscard={discardForm} />
      <ControlledChoice disabled={busy} label="Crédito recebido para vincular ao caso" value={refundRef} onChange={setRefundRef} options={refunds.map((m:any)=>m.ref)} display={v=>{const m=refunds.find((r:any)=>r.ref===v);return m?`${m.label} · ${currency(m.amount)}`:v}}/>
      {cases.map(item=><View style={s.card} key={item.id}><Text style={s.heading}>{item.title}</Text><Text style={s.text}>{statusLabel(item.status)} · esperado {currency(item.expected_amount)} · recuperado {currency(item.recovered_amount)}</Text><Button label="Gerar dossiê" disabled={busy} onPress={()=>run(async()=>{setDossier((await api(`/v1/insights/cases/${item.id}/dossier`)).markdown)})}/>{item.status!=='resolved'&&<><Button label="Aguardar reembolso" disabled={busy} onPress={()=>run(async()=>{await send(`/cases/${item.id}`,{revision:item.revision,status:'awaiting_refund',notes:item.notes||''},'PATCH');await refreshSaved(loadCases)})}/><Button label="Resolver com o crédito selecionado" disabled={busy||!refundRef} onPress={()=>run(async()=>{await send(`/cases/${item.id}`,{revision:item.revision,status:'resolved',refund_ref:refundRef,notes:item.notes||''},'PATCH');await refreshSaved(loadCases)})}/><Button label="Descartar suspeita" disabled={busy} onPress={()=>run(async()=>{await send(`/cases/${item.id}`,{revision:item.revision,status:'dismissed',notes:item.notes||''},'PATCH');await refreshSaved(loadCases)})}/></>}</View>)}
      {caseCursor&&<Button label="Carregar mais casos" disabled={busy} onPress={()=>run(async()=>{const result=await api(`/v1/insights/cases?cursor=${encodeURIComponent(caseCursor)}`);setCases(current=>[...current,...result.items]);setCaseCursor(result.next_cursor)})}/>}
      {!!dossier&&<View style={s.card}><Text style={s.heading}>Dossiê — selecione para copiar</Text><Text selectable style={s.text}>{dossier}</Text></View>}
    </>}
    {(section==='Simular'||section==='Liquidez')&&<>
      <Notice>As projeções dependem das premissas informadas. Confira faturas e obrigações ausentes. Nenhum pagamento ou transferência será executado.</Notice>
      {context&&<><Text style={s.text}>{context.flows.length} cobranças previstas disponíveis; {context.unmapped_obligations} obrigações exigem conferência.</Text>{context.excluded_sources.map((item:any)=><Notice key={item.ref}>{item.reason}</Notice>)}</>}
      {[...(context?.flows||[]),...(context?.suggested_flows||[])].map((flow:any)=>{const included=flowChoices[flow.id]??!flow.id.startsWith('obligation:');return <View style={s.card} key={flow.id}><Text style={s.text}>{flow.label} · {flow.date} · {currency(flow.amount)}</Text>{flow.overdue&&<Notice>Vencida em {flow.original_due_date}; pagamento assumido para hoje.</Notice>}<Button label={included?'Incluído — remover do cenário':'Incluir após conferir que não duplica outra previsão'} onPress={()=>setFlowChoices(current=>({...current,[flow.id]:!included}))}/>{included&&<ControlledChoice disabled={busy} label="Conta de pagamento (obrigatória para liquidez)" value={flowAccounts[flow.id]||flow.account_key||''} onChange={value=>setFlowAccounts(current=>({...current,[flow.id]:value}))} options={(context?.accounts||[]).map((a:any)=>a.key)} display={key=>accountLabel((context?.accounts||[]).find((a:any)=>a.key===key),key)}/>}</View>})}
      <Field label="Reserva mínima por conta / reserva do cenário (R$)" value={reserve} onChange={setReserve}/>
      <Field label="Outros fluxos: data; valor com sinal; descrição; conta (um por linha)" value={flowsText} onChange={setFlowsText} multiline/>
      <Notice>Exemplo: 2026-10-05; -120,00; Conta de luz; itau. Use positivo para entrada. Somente previsões marcadas como incluídas serão somadas.</Notice>
    </>}
    {section==='Simular'&&<>
      <Field label="Saldo inicial assumido (R$)" value={opening} onChange={setOpening}/><Field label="Valor total da compra (R$)" value={purchase} onChange={setPurchase}/><Field label="Número de parcelas" value={count} onChange={setCount}/><Field label="Primeiro pagamento em caixa (AAAA-MM-DD)" value={first} onChange={setFirst}/><Field label="Alternativa: esperar quantos dias?" value={wait} onChange={setWait}/><Field label="Alternativa: valor reduzido (R$; opcional)" value={reduced} onChange={setReduced}/>
      <Button label="Comparar quatro cenários por 90 dias" disabled={busy} onPress={()=>run(()=>analyze('/simulate',{start:day(),days:90,opening_balance:opening?decimal(opening):null,reserve:decimal(reserve),purchase:decimal(purchase),installments:Number(count),first_payment:first,wait_days:Number(wait),reduced_purchase:reduced?decimal(reduced):null,flows:flows(),assumptions:context?.gaps||[]},setSimulation))}/>
      {simulation?.gaps?.map((gap:string)=><Notice key={gap}>{gap}</Notice>)}{simulation?.scenarios?.map((item:any)=><Pressable accessibilityRole="button" key={item.name} style={s.card} onPress={()=>setSelectedScenario(item.name)}><Text style={s.heading}>{scenarioNames[item.name]}</Text><Text style={s.text}>Menor saldo: {currency(item.minimum_balance)} · final: {currency(item.closing_balance)}</Text><Text style={s.text}>Primeiro dia abaixo da reserva: {item.first_reserve_breach||'Nenhum no cenário'}</Text><Text style={s.text}>Parcelas após o horizonte: {currency(item.outside_horizon_total)}</Text><Text style={s.link}>Ver trajetória diária</Text></Pressable>)}
      {simulation?.scenarios?.find((item:any)=>item.name===selectedScenario)?.curve.map((point:any)=><View key={point.date} style={s.row}><Text style={s.text}>{point.date}</Text><Text style={[s.text,point.below_reserve&&s.error]}>{currency(point.closing)}</Text></View>)}
    </>}
    {section==='Liquidez'&&<>
      {(context?.accounts||[]).map((item:any)=><Text key={item.key} style={s.text}>{accountLabel(item)}: {item.balance===null?'sem saldo de referência':currency(item.balance)} · identificador {item.key}</Text>)}
      <Button label="Calcular propostas de liquidez (30 dias)" disabled={busy||!context} onPress={()=>run(()=>analyze('/liquidity',{start:day(),days:30,accounts:context.accounts.map((item:any)=>({key:item.key,balance:item.balance_status!=='calculado'?null:String(item.balance),reserve:decimal(reserve),transferable:item.tipo!=='criptoativos'})),flows:flows()},setLiquidity))}/>
      {liquidity?.gaps?.map((gap:string)=><Notice key={gap}>{gap}</Notice>)}{liquidity?.proposals?.map((item:any,index:number)=><View key={index} style={s.card}><Text style={s.heading}>{currency(item.amount)} · {item.from_account_key} → {item.to_account_key}</Text><Text style={s.text}>{item.date} · {item.reason}</Text></View>)}{liquidity?.uncovered?.map((item:any,index:number)=><Notice key={index}>{item.date}: faltam {currency(item.amount)} em {item.account_key} para preservar a reserva.</Notice>)}
      {liquidity&&!liquidity.proposals.length&&!liquidity.uncovered.length&&<Notice>Nenhuma transferência sugerida para os saldos conhecidos e fluxos informados. Contas desconhecidas não foram usadas como fontes.</Notice>}
    </>}
    {section==='Experimentos'&&<>
      <Notice>Teste uma mudança por vez. A comparação mede diferença observada; não prova que a mudança causou economia.</Notice>
      <Field label="Título do experimento" value={experimentTitle} onChange={setExperimentTitle}/><ControlledChoice disabled={busy} label="Categoria" value={category} onChange={setCategory} options={categories}/><Field label="Mudança que você deseja testar" value={protocol} onChange={setProtocol} multiline/>
      <Field label="Linha de base: início" value={baseStart} onChange={setBaseStart}/><Field label="Linha de base: fim" value={baseEnd} onChange={setBaseEnd}/><Field label="Experimento: início" value={experimentStart} onChange={setExperimentStart}/><Field label="Experimento: fim" value={experimentEnd} onChange={setExperimentEnd}/><Field label="Redução desejada (%)" value={target} onChange={setTarget}/>
      <Button label="Iniciar experimento" disabled={busy} onPress={()=>run(async()=>{await send('/experiments',{request_id:createExperimentId,title:experimentTitle,category,protocol,baseline_start:baseStart,baseline_end:baseEnd,start:experimentStart,end:experimentEnd,target_reduction_pct:decimal(target)});setCreateExperimentId(newId());await refreshSaved(loadExperiments)})}/>
      <DiscardDraftAction disabled={busy} owner={section} label="Excluir rascunho do experimento" onDiscard={discardForm} />
      <Field label="Satisfação com a mudança (1–5)" value={satisfaction} onChange={setSatisfaction}/><Field label="Viagens, mudanças de renda e outros fatores" value={confounders} onChange={setConfounders} multiline/>
      {experiments.map(item=><View key={item.id} style={s.card}><Text style={s.heading}>{item.title}</Text><Text style={s.text}>{statusLabel(item.status)} · {item.category}</Text><Button label="Acompanhar resultado" disabled={busy} onPress={()=>run(async()=>setExperimentReport(await api(`/v1/insights/experiments/${item.id}/report`)))}/>{item.status==='active'&&<><Button label="Concluir e registrar avaliação" disabled={busy} onPress={()=>run(async()=>{await send(`/experiments/${item.id}`,{revision:item.revision,status:'completed',satisfaction:Number(satisfaction),confounders},'PATCH');await refreshSaved(loadExperiments)})}/><Button label="Cancelar experimento" disabled={busy} onPress={()=>run(async()=>{await send(`/experiments/${item.id}`,{revision:item.revision,status:'cancelled',confounders},'PATCH');await refreshSaved(loadExperiments)})}/></>}</View>)}
      {experimentCursor&&<Button label="Carregar mais experimentos" disabled={busy} onPress={()=>run(async()=>{const result=await api(`/v1/insights/experiments?cursor=${encodeURIComponent(experimentCursor)}`);setExperiments(current=>[...current,...result.items]);setExperimentCursor(result.next_cursor)})}/>}
      {experimentReport&&<View style={s.card}><Text style={s.heading}>Comparação até hoje</Text><Text style={s.text}>Base: {currency(experimentReport.baseline_spending)} / {experimentReport.baseline_days} dias</Text><Text style={s.text}>Observado: {currency(experimentReport.observed_spending)} / {experimentReport.observed_days} dias</Text><Text style={s.text}>Diferença comparada à taxa diária da base: {currency(experimentReport.observed_difference)}</Text><Notice>{experimentReport.interpretation}</Notice></View>}
    </>}
    {(section==='Simular'||section==='Liquidez')&&<DiscardDraftAction disabled={busy} owner={section} label="Excluir rascunho do planejamento" onDiscard={discardForm} message="As premissas preenchidas e os resultados desta simulação serão apagados da tela. Seus compromissos e registros salvos continuam preservados." />}
  </View></FormBusy.Provider>;
}
const s=StyleSheet.create({container:{paddingBottom:32},title:{fontSize:25,fontWeight:'700',color:'#F4F7F5',marginVertical:16},heading:{fontSize:16,fontWeight:'700',color:'#F4F7F5'},text:{fontSize:13,color:'#C1D3CE',lineHeight:21},notice:{fontSize:12,color:'#E5C98A',lineHeight:19,marginVertical:9},error:{color:'#FF9B95',fontSize:13,lineHeight:20},tabs:{flexDirection:'row',flexWrap:'wrap',gap:6,marginBottom:14},tab:{backgroundColor:'#15292C',padding:10,borderRadius:10},selected:{borderColor:'#49C5AA',borderWidth:1},field:{marginVertical:7},label:{fontSize:12,color:'#B2C8C1',marginBottom:6},input:{borderWidth:1,borderColor:'#35504C',borderRadius:10,padding:12,color:'#F4F7F5',backgroundColor:'#142326',fontSize:14},button:{padding:12,borderRadius:10,backgroundColor:'#49C5AA',marginVertical:7},buttonText:{color:'#062820',fontWeight:'700',textAlign:'center'},card:{padding:15,borderRadius:12,backgroundColor:'#142326',marginVertical:7},link:{color:'#49C5AA',marginTop:7},row:{flexDirection:'row',justifyContent:'space-between',paddingVertical:6,borderBottomWidth:1,borderBottomColor:'#213A35'}});