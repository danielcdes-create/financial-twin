import React, { useEffect, useRef, useState } from 'react';
import {ActivityIndicator, Image, Platform, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import * as ImagePicker from 'expo-image-picker';
import * as Sharing from 'expo-sharing';
import { captureRef } from 'react-native-view-shot';
import { purchaseImageFormData, type PurchaseImage } from '../utils/purchaseImage';
import { purchaseCardLines } from '../utils/purchaseCard';
import { captureDateIso } from '../utils/captureDate';
import {maskCivilDate} from '../utils/formMask';
import {civilDateLabel, formatPurchaseFlowLabel, purchaseHorizonSubtitle, purchaseResultCopy, summarizePlanningContext} from '../utils/purchaseCopy';
import VoiceCapture from './VoiceCapture';
import { withDevicePrompt } from '../utils/devicePrompt';
import {useAsyncScope, useScopedRequest} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';
import {requireMoneyInput} from '../utils/moneyInput';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

type Props = {
  api: (path: string, init?: RequestInit) => Promise<any>;
  upload: (path: string, body: FormData) => Promise<any>;
  onBack: () => void;
  sharedImage?: PurchaseImage | null;
  sharedError?: string;
  ready: boolean;
};
const civilTodayIso = () => { const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'America/Sao_Paulo', year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date()); const map = Object.fromEntries(parts.map(p => [p.type, p.value])); return `${map.year}-${map.month}-${map.day}`; };
const decimal = (value: string) => requireMoneyInput(value);
const money = (value: string | null | undefined) => value == null ? '—' : Number(value).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
function Action({label,onPress,disabled=false}:{label:string;onPress:()=>void;disabled?:boolean}){return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={[s.action,disabled&&s.disabled]}><Text style={s.actionText}>{label}</Text></Pressable>}
const FormBusy=React.createContext(false);
function Field({label,value,onChange,placeholder,keyboardType,disabled=false}:{label:string;value:string;onChange:(text:string)=>void;placeholder?:string;keyboardType?:'default'|'number-pad';disabled?:boolean}){const busy=React.useContext(FormBusy);return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} editable={!disabled&&!busy} value={value} onChangeText={onChange} placeholder={placeholder} keyboardType={keyboardType} style={s.input} autoCapitalize="none"/></View>}

export default function PurchaseCheck({api:request,upload:uploadRequest,onBack,sharedImage,sharedError,ready}:Props){
  const captureScope=useAsyncScope(), api=useScopedRequest(request,captureScope), upload=useScopedRequest(uploadRequest,captureScope);
  const [image,setImage]=useState<PurchaseImage|null>(sharedImage||null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(sharedError||'');
  const [scan,setScan]=useState<any>(null),[context,setContext]=useState<any>(null),[selected,setSelected]=useState<Record<string,boolean>>({});
  const [product,setProduct]=useState(''),[price,setPrice]=useState(''),[installments,setInstallments]=useState('1'),[first,setFirst]=useState(() => civilDateLabel(civilTodayIso()));
  const [opening,setOpening]=useState(''),[reserve,setReserve]=useState('0'),[income,setIncome]=useState(''),[hours,setHours]=useState('');
  const [goalName,setGoalName]=useState(''),[goalTarget,setGoalTarget]=useState(''),[goalSaved,setGoalSaved]=useState('0'),[goalMonthly,setGoalMonthly]=useState('');
  const [result,setResult]=useState<any>(null),[shareVisible,setShareVisible]=useState(false),[copiedSummary,setCopiedSummary]=useState(false),[pickingImage,setPickingImage]=useState(false);
  const [contextRetry,setContextRetry]=useState(0);
  const start=civilTodayIso(), count=Number(installments), firstIso=captureDateIso(first)||'';
  const back=()=>protectUnsavedBack(Boolean(image||product||price||opening||income||hours||goalName||goalTarget||goalMonthly||reserve!=='0'||goalSaved!=='0'||installments!=='1'||firstIso!==start||Object.keys(selected).length),onBack);
  useLocalBack(back,true,10);
  const cardRef=useRef<View>(null);
  const inputRevision=useRef(0);
  const validWindow=/^\d+$/.test(installments)&&Number.isInteger(count)&&count>=1&&count<=60&&Boolean(firstIso)&&firstIso>=start;
  const contextKey=`${start}|${count}|${firstIso}`;
  const visibleContext=context?.request_key===contextKey&&!context.error?context:null;
  const contextError=context?.request_key===contextKey?context.error:'';
  const contextLoading=ready&&validWindow&&!visibleContext&&!contextError;
  useEffect(()=>{
    if(!ready||!validWindow)return;
    let active=true;
    const timer=setTimeout(()=>{
      return api(`/v1/insights/purchase-context?start=${start}&installments=${count}&first_payment=${firstIso}`)
        .then(data=>{if(active)setContext({...data,request_key:contextKey})})
        .catch(e=>{if(active)setContext({request_key:contextKey,error:e?.message||'Não foi possível carregar os compromissos.'})});
    },300);
    return ()=>{active=false;clearTimeout(timer)};
  },[api,ready,validWindow,start,count,firstIso,contextKey,contextRetry]);
  const edit=(setter:(text:string)=>void)=>(text:string)=>{inputRevision.current++;setter(text);setResult(null);setShareVisible(false)};
  const run=async(task:(valid:()=>boolean)=>Promise<void>)=>{const valid=captureScope();setBusy(true);setError('');try{await task(valid)}catch(e:any){if(valid())setError(e?.message||'Não foi possível concluir a análise.')}finally{if(valid())setBusy(false)}};
  const pick=()=>run(async(valid)=>{
    setPickingImage(true);
    try {
      const chosen=await withDevicePrompt(() => ImagePicker.launchImageLibraryAsync({mediaTypes:['images'],quality:0.85}));
      if(!valid()||chosen.canceled)return;
      const asset=chosen.assets[0];inputRevision.current++;setImage({uri:asset.uri,mimeType:asset.mimeType,fileName:asset.fileName,fileSize:asset.fileSize});setScan(null);setResult(null);setShareVisible(false);
    } finally { if(valid())setPickingImage(false); }
  });
  const readImage=()=>run(async()=>{
    if(!image)throw new Error('Escolha um print primeiro.');
    const offer=await upload('/v1/insights/purchase-scan',await purchaseImageFormData(image));
    inputRevision.current++;setScan(offer);if(offer.product_label!=null)setProduct(offer.product_label);if(offer.total_price!=null)setPrice(String(offer.total_price));if(offer.installment_count!=null)setInstallments(String(offer.installment_count));setResult(null);setShareVisible(false);
  });
  const applyVoice=(offer:any)=>{
    inputRevision.current++;
    setScan(offer);
    if(offer.product_label!=null)setProduct(offer.product_label);
    if(offer.total_price!=null)setPrice(String(offer.total_price));
    if(offer.installment_count!=null)setInstallments(String(offer.installment_count));
    setResult(null);setShareVisible(false);
  };
  const discardForm=()=>{
    if(busy)return;
    inputRevision.current++;
    setImage(null);setScan(null);setSelected({});setProduct('');setPrice('');setInstallments('1');setFirst(civilDateLabel(civilTodayIso()));
    setOpening('');setReserve('0');setIncome('');setHours('');setGoalName('');setGoalTarget('');setGoalSaved('0');setGoalMonthly('');
    setResult(null);setShareVisible(false);setError('');
  };
  const assess=()=>run(async()=>{
    const count=Number(installments);
    if(!Number.isInteger(count)||count<1||count>60)throw new Error('Informe de 1 a 60 parcelas.');
    if(!validWindow)throw new Error('Informe o primeiro pagamento como dd/mm/aaaa, a partir de hoje.');
    if(!ready||!visibleContext)throw new Error('Aguarde o carregamento dos compromissos deste período ou tente novamente.');
    if(Boolean(income)!==Boolean(hours))throw new Error('Para horas de trabalho, informe renda líquida e horas mensais.');
    if(Boolean(goalTarget)!==Boolean(goalMonthly))throw new Error('Para avaliar a meta, informe valor alvo e aporte mensal.');
    const selectedFlows=[...(visibleContext.flows||[]),...(visibleContext.suggested_flows||[])].filter((row:any)=>selected[row.id]??!row.id.startsWith('obligation:')).map((row:any)=>({id:row.id,source_ref:row.source_ref,date:row.date,amount:row.amount,label:row.label,account_key:row.account_key||null,certainty:row.certainty||'assumed'}));
    const body={start,price:decimal(price),installments:count,first_payment:firstIso,
      opening_balance:opening?decimal(opening):null,reserve:decimal(reserve),flows:selectedFlows,
      monthly_net_income:income?decimal(income):null,monthly_work_hours:hours?decimal(hours):null,
      goal_target:goalTarget?decimal(goalTarget):null,goal_saved:decimal(goalSaved),goal_monthly_contribution:goalMonthly?decimal(goalMonthly):null};
    const revision=inputRevision.current;
    const response=await api('/v1/insights/purchase-check',{method:'POST',body:JSON.stringify(body)});
    if(revision===inputRevision.current){setResult(response);setShareVisible(false)}
  });
  const share=()=>run(async(valid)=>{
    if(!result||!cardRef.current)throw new Error('Gere uma análise antes de compartilhar.');
    const uri=await captureRef(cardRef.current,{format:'png',quality:1,result:Platform.OS==='web'?'data-uri':'tmpfile'});
    if(!valid())return;
    if(Platform.OS==='web'){
      const link=document.createElement('a');link.href=uri;link.download='financial-twin-compra.png';link.click();return;
    }
    const available=await Sharing.isAvailableAsync();
    if(!valid())return;
    if(!available)throw new Error('Compartilhamento indisponível neste aparelho.');
    await withDevicePrompt(() => Sharing.shareAsync(uri,{mimeType:'image/png',dialogTitle:'Compartilhar análise da compra'}));
  });
  const copy=result?purchaseResultCopy(result,money,goalName):null;
  const lines=result?purchaseCardLines({price:result.price,workHours:result.work_hours,delayDays:result.goal?.delay_days??null}):[];
  const contextCopy=visibleContext?summarizePlanningContext(visibleContext,{includeProfiles:false}):null;
  return <FormBusy.Provider value={busy}><View style={s.container}>
    <Action label="‹ Voltar" onPress={back}/><Text style={s.title}>Cabe na minha vida?</Text>
    <Text style={s.note}>Confira o impacto antes de comprar. Um print ajuda a preencher os dados; a análise só usa o preço que você confirmar.</Text>
    {!!error&&<Text accessibilityRole="alert" style={s.error}>{error}</Text>}{busy&&<ActivityIndicator color="#136D67"/>}
    <View style={s.card}><Text style={s.heading}>1 · Preço e parcelas</Text>
      <VoiceCapture api={api} upload={upload} context="purchase" onApply={applyVoice} disabled={busy} light/>
      <Action label={pickingImage ? "Preparando imagem…" : "Escolher print da galeria"} disabled={busy} onPress={pick}/>
      {pickingImage&&<Text style={s.note}>Preparando o print selecionado…</Text>}
      {image&&<><Image source={{uri:image.uri}} resizeMode="contain" style={s.preview}/><Text style={s.note}>O print será enviado à extração xAI apenas se você tocar em “Ler print”. Confira se não contém dados pessoais que queira ocultar.</Text><Action label="Ler print com xAI" disabled={busy} onPress={readImage}/></>}
      {scan?.ambiguity&&<Text style={s.note}>A conferir: {scan.ambiguity}</Text>}
      {scan?.installment_amount&&<Text style={s.note}>Parcela identificada: {money(scan.installment_amount)}. Confira o preço total abaixo.</Text>}
      <Field label="Produto (só para sua tela)" value={product} onChange={edit(setProduct)} placeholder="Opcional"/>
      <Field label="Preço total confirmado (R$)" value={price} onChange={edit(setPrice)} placeholder="Ex.: 4800,00"/>
      <Field label="Número de parcelas" value={installments} onChange={edit(setInstallments)}/>
      <Field label="Data do primeiro pagamento" value={first} onChange={edit(text => setFirst(maskCivilDate(text)))} placeholder="dd/mm/aaaa" keyboardType="number-pad"/>
    </View>
    <View style={s.card}><Text style={s.heading}>2 · Sua situação</Text><Text style={s.note}>Estes campos são opcionais. Sem o saldo inicial, a análise mostra as horas e a meta, mas não calcula se o dinheiro cobre a compra.</Text>
      <Field label="Saldo inicial (R$)" value={opening} onChange={edit(setOpening)}/><Field label="Reserva mínima (R$)" value={reserve} onChange={edit(setReserve)}/>
      <Field label="Renda líquida mensal (R$)" value={income} onChange={edit(setIncome)}/><Field label="Horas trabalhadas por mês" value={hours} onChange={edit(setHours)}/>
      <Text style={s.note}>Se você informar a renda líquida e as horas do mês, a análise mostra quantas horas de trabalho equivalem a este preço.</Text>
    </View>
    <View style={s.card}><Text style={s.heading}>3 · Uma meta sua</Text><Text style={s.note}>A compra e os aportes saem do mesmo dinheiro. O nome da meta fica só nesta tela.</Text>
      <Field label="Nome da meta" value={goalName} onChange={edit(setGoalName)} placeholder="Ex.: viagem"/>
      <Field label="Valor alvo da meta (R$)" value={goalTarget} onChange={edit(setGoalTarget)}/><Field label="Já reservado para a meta (R$)" value={goalSaved} onChange={edit(setGoalSaved)}/><Field label="Aporte mensal (R$)" value={goalMonthly} onChange={edit(setGoalMonthly)}/>
    </View>
    <View style={s.card}><Text style={s.heading}>4 · Compromissos previstos</Text>
      <Text style={s.note}>O período cobre as parcelas e mais um mês, e nunca fica menor que 90 dias. A data do primeiro pagamento entra nessa conta.</Text>
      {contextLoading&&<><ActivityIndicator color="#136D67"/><Text style={s.note}>Carregando compromissos do período…</Text></>}
      {!validWindow&&<Text style={s.note}>Informe de 1 a 60 parcelas e o primeiro pagamento, de hoje em diante, como dd/mm/aaaa.</Text>}
      {!ready&&<Text style={s.note}>Conecte ao servidor para carregar os compromissos.</Text>}
      {!!contextError&&<><Text accessibilityRole="alert" style={s.error}>{contextError}</Text><Action label="Tentar carregar compromissos novamente" disabled={busy} onPress={()=>{setContext(null);setContextRetry(value=>value+1)}}/></>}
      {visibleContext&&contextCopy&&<><Text style={s.label}>{purchaseHorizonSubtitle(visibleContext.horizon)}</Text><Text style={s.note}>{contextCopy.summary}</Text>{contextCopy.notices.map((notice:string,index:number)=><Text key={`notice:${index}`} style={s.note}>{notice}</Text>)}{[...(visibleContext.flows||[]),...(visibleContext.suggested_flows||[])].sort((a:any,b:any)=>a.date.localeCompare(b.date)||a.label.localeCompare(b.label)).map((row:any)=>{const included=selected[row.id]??!row.id.startsWith('obligation:');const due=civilDateLabel(row.date);const displayLabel=formatPurchaseFlowLabel(row.label);return <Pressable accessibilityRole="checkbox" accessibilityLabel={`${displayLabel}, ${due}`} aria-checked={included} accessibilityState={{checked:included}} key={row.id} style={s.flow} onPress={()=>{inputRevision.current++;setSelected(previous=>({...previous,[row.id]:!included}));setResult(null);setShareVisible(false)}}><Text style={s.label}>{included?'☑':'☐'} {displayLabel}</Text><Text style={s.note}>{due} · {money(row.amount)}</Text></Pressable>})}</>}
    </View>
    <Action label="Ver impacto da compra" disabled={busy||!price||!ready||!validWindow||!visibleContext} onPress={assess}/>
    <DiscardDraftAction disabled={busy} light label="Excluir rascunho da análise" onDiscard={discardForm} message="Os dados preenchidos, o print selecionado e a análise desta compra serão removidos da tela. Seus registros salvos continuam preservados." />
    {copy&&<View style={s.card}><Text style={s.heading}>Sua análise</Text>
      <View style={s.priceBlock}><Text style={s.result}>{copy.price}</Text><Text style={s.result}>{copy.installments}</Text></View>
      {copy.hours&&<Text style={s.result}>{copy.hours}</Text>}
      {copy.goalHeadline&&<Text style={s.result}>{copy.goalHeadline}</Text>}
      {copy.alternatives.map(item=><View key={item.title} style={s.alternative}><Text style={s.alternativeTitle}>{item.title}</Text><Text style={s.alternativeBody}>{item.body}</Text></View>)}
      <Text style={s.note}>{copy.window}</Text>
      {copy.goalDetail&&<Text style={s.note}>{copy.goalDetail}</Text>}
      {copy.reserve&&<Text style={s.note}>{copy.reserve}</Text>}
      {copy.notes.map((note,index)=><Text key={index} style={s.note}>{note}</Text>)}
      <Text style={s.note}>Previsão baseada nas premissas acima. Nenhuma compra ou obrigação foi criada.</Text>
      <Action label={shareVisible?'Ocultar cartão':'Prévia do cartão compartilhável'} disabled={busy} onPress={()=>setShareVisible(!shareVisible)}/>
      <Action label={copiedSummary?'Ocultar resumo em texto':'Copiar resumo em texto'} disabled={busy} onPress={()=>{const summaryText=[copy.price,copy.installments,copy.hours,copy.goalHeadline,copy.window].filter(Boolean).join(' · ');try{if(typeof navigator!=='undefined'&&navigator.clipboard?.writeText)void navigator.clipboard.writeText(summaryText);}catch{}setCopiedSummary(v=>!v);}}/>
      {copiedSummary&&<Text selectable style={s.note}>{[copy.price,copy.installments,copy.hours,copy.goalHeadline,copy.window].filter(Boolean).join(' · ')}</Text>}
    </View>}
    {result&&shareVisible&&<><View ref={cardRef} collapsable={false} style={s.shareCard}><Text style={s.shareBrand}>FINANCIAL TWIN</Text><Text style={s.shareTitle}>Quanto custa na minha vida?</Text>{lines.map((line,index)=><Text key={index} style={index===0?s.sharePrice:index===lines.length-1?s.shareFoot:s.shareFact}>{line}</Text>)}</View><Text style={s.note}>O cartão mostra preço, horas e eventual adiamento. Ele não inclui salário, saldo, dívida, estabelecimento ou nome da meta.</Text><Action label={Platform.OS==='web'?'Salvar cartão como imagem':'Compartilhar cartão como imagem'} disabled={busy} onPress={share}/></>}
  </View></FormBusy.Provider>;
}

const s=StyleSheet.create({container:{gap:12,padding:16,paddingBottom:28,borderRadius:18,backgroundColor:'#EDF5F4'},title:{fontSize:25,fontWeight:'800',color:'#133E43'},note:{fontSize:13,lineHeight:19,color:'#54666C'},error:{color:'#B42318',fontSize:14},card:{backgroundColor:'#FFFFFF',borderRadius:16,padding:16,gap:9,borderWidth:1,borderColor:'#D9E7E7'},heading:{fontSize:17,fontWeight:'700',color:'#173F43'},field:{gap:5},label:{fontSize:14,fontWeight:'600',color:'#25494C'},input:{borderWidth:1,borderColor:'#B8CDCE',borderRadius:9,paddingHorizontal:12,paddingVertical:10,color:'#173F43',backgroundColor:'#FFF',fontSize:15},action:{backgroundColor:'#136D67',borderRadius:11,paddingVertical:12,paddingHorizontal:16,alignItems:'center'},actionText:{color:'#FFFFFF',fontSize:14,fontWeight:'700'},disabled:{opacity:.5},preview:{width:'100%',height:190,backgroundColor:'#F3F6F6',borderRadius:9},flow:{borderTopWidth:1,borderColor:'#E4EEEE',paddingVertical:8,gap:2},result:{fontSize:18,fontWeight:'700',color:'#124D4A'},priceBlock:{backgroundColor:'#F4FBFA',borderRadius:12,padding:12,gap:2},alternative:{backgroundColor:'#F7FBFB',borderRadius:12,padding:12,gap:4,borderWidth:1,borderColor:'#D5E6E4'},alternativeTitle:{fontSize:16,fontWeight:'700',color:'#124D4A'},alternativeBody:{fontSize:15,lineHeight:22,color:'#173F43'},shareCard:{backgroundColor:'#143F46',borderRadius:20,padding:24,gap:10,minHeight:250,justifyContent:'center'},shareBrand:{color:'#71D8C6',fontWeight:'800',letterSpacing:2,fontSize:12},shareTitle:{color:'#FFF',fontSize:21,fontWeight:'800'},sharePrice:{color:'#FFF',fontSize:27,fontWeight:'800'},shareFact:{color:'#D1F1EA',fontSize:19,fontWeight:'700'},shareFoot:{color:'#B7C9C9',fontSize:11,marginTop:14}});