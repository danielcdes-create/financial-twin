import { Alert } from "../utils/appAlert";
import React, { useEffect, useRef, useState } from 'react';
import { rebaseDraft } from '../utils/draftConflict';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import { applyVendorDefaults, financialDateLabel, rankedOptions } from '../utils/classification';
import {creationId} from '../utils/commitmentCreate';
import ControlledChoice from './ControlledChoice';
import {useAsyncScope} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';
import {useClassificationOptions} from '../utils/useClassificationOptions';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

const FIELDS = [
  ['vendedor', 'Vendedor'], ['plataforma', 'Plataforma'], ['natureza', 'Natureza'],
  ['categoria', 'Categoria'], ['subcategoria', 'Subcategoria'],
  ['recorrencia_tipo', 'Recorrência'],
  ['recorrencia_periodicidade', 'Periodicidade'],
  ['metodo_pagamento', 'Meio de pagamento'], ['fluxo', 'Fluxo'],
  ['competencia', 'Competência'], ['localidade', 'Localidade'],
  ['localidade_contexto', 'Contexto da localidade'],
  ['status_conciliacao', 'Status da conciliação'], ['detalhe', 'Observações'],
] as const;

const usesSuggestions=(field:string)=>field!=='detalhe';
const CONTROLLED_FIELDS=new Set(['natureza','categoria','subcategoria','recorrencia_tipo','recorrencia_periodicidade','metodo_pagamento','fluxo','localidade_contexto','status_conciliacao']);
const REQUIRED_CHOICES=new Set(['natureza','categoria','recorrencia_tipo','recorrencia_periodicidade','fluxo','localidade_contexto','status_conciliacao']);
const isIncoming=(item:any)=>item?.fluxo==='entrada'||Number(item?.valor||0)>0;
const currentFieldValue=(item:any,field:string)=>field==='recorrencia_tipo'?item?.recorrencia?.tipo:field==='recorrencia_periodicidade'?item?.recorrencia?.periodicidade:field==='vendedor'?(item?.vendedor||item?.fornecedor):item?.[field];
const optionLabel=(value:string,labels:Record<string,string>={})=>labels[value]||({recorrente_fixa:'Recorrente fixa',recorrente_variavel:'Recorrente variável',eventual:'Eventual',nao_aplicavel:'Não aplicável',entrada:'Entrada',saida:'Saída'} as Record<string,string>)[value]||value.replaceAll('_',' ');
const itemFields=(value:any):Record<string,string>=>Object.fromEntries(FIELDS.map(([field])=>[field,String(currentFieldValue(value,field)||'')]));

type Props={
  item:any;
  api:(path:string,init?:RequestInit)=>Promise<any>;
  money:(value:number)=>string;
  onSaved:(item:any,result:any)=>void|Promise<void>;
  onCancel:()=>void;
};

export default function TransactionEditor({item:initialItem,api,money,onSaved,onCancel}:Props){
  const [item,setItem]=useState(initialItem);
  const [conflict,setConflict]=useState(false);
  const [conflictFields,setConflictFields]=useState<string[]>([]);
  const savingLock=useRef(false);
  const captureScope=useAsyncScope(initialItem.id);
  const {options,loading:loadingOptions,error:optionsError,stale:optionsStale,retry:retryOptions}=useClassificationOptions(api,initialItem.id);
  const [draft,setDraft]=useState<Record<string,string>>(()=>itemFields(initialItem));
  const [focusedField,setFocusedField]=useState<string|null>(null);
  const [saving,setSaving]=useState(false);
  const [matchPreview,setMatchPreview]=useState<any>(null),[previewLoading,setPreviewLoading]=useState(false);
  const [applySimilarSupplier,setApplySimilarSupplier]=useState(false);
  const [classificationPreview,setClassificationPreview]=useState<any>(null);
  const [applySimilarClassifications,setApplySimilarClassifications]=useState(false);
  const back=()=>protectUnsavedBack(JSON.stringify(draft)!==JSON.stringify(itemFields(item)),onCancel);
  useLocalBack(back,true,20);

  useEffect(()=>{
    let active=true;
    if(item?.id)void api(`/v1/transactions/${item.id}/classification-match-preview`).then(preview=>{if(active)setClassificationPreview(preview)}).catch(()=>{});
    return()=>{active=false};
  },[api,item]);

  useEffect(()=>{
    const supplier=draft.vendedor?.trim();
    const reset=setTimeout(()=>{setApplySimilarSupplier(false);setMatchPreview(null);setPreviewLoading(false)},0);
    if(!item?.id||supplier.length<3||supplier===item.vendedor)return()=>clearTimeout(reset);
    let active=true;
    const task=setTimeout(()=>{if(!active)return;setPreviewLoading(true);return api(`/v1/transactions/${item.id}/supplier-match-preview?supplier=${encodeURIComponent(supplier)}`).then(result=>{if(active)setMatchPreview(result)}).catch(()=>{if(active)setMatchPreview(null)}).finally(()=>{if(active)setPreviewLoading(false)})},500);
    return()=>{active=false;clearTimeout(reset);clearTimeout(task)};
  },[api,draft.vendedor,item]);

  const save=async(validate:boolean)=>{
    if(!item?.id||savingLock.current||conflict||!captureScope()())return;
    const body:Record<string,any>=Object.fromEntries(FIELDS.map(([field])=>[field,draft[field]?.trim()||'']).filter(([field,value])=>value!==String(currentFieldValue(item,field)||'').trim()));
    if(applySimilarClassifications&&classificationPreview?.total>0){
      for(const field of ['natureza','categoria','subcategoria','recorrencia_tipo','recorrencia_periodicidade','metodo_pagamento','fluxo','localidade','localidade_contexto','status_conciliacao']){
        const value=draft[field]?.trim();if(value)body[field]=value;
      }
      body.apply_similar_classification=true;body.classification_preview_token=classificationPreview.preview_token;
    }
    if(applySimilarSupplier&&matchPreview?.total>0&&draft.vendedor?.trim()){body.vendedor=draft.vendedor.trim();body.apply_similar_supplier=true;body.supplier_preview_token=matchPreview.preview_token}
    if(!validate&&!Object.keys(body).some(field=>!field.startsWith('apply_similar_'))){Alert.alert('Nada para salvar','Altere pelo menos uma classificação ou marque como validado.');return}
    savingLock.current=true;setSaving(true);
    const valid=captureScope();
    let saved=false;
    try{
      const hasChanges=Object.keys(body).length>0;
      if(validate&&hasChanges)body.validate_review=true;
      const result=hasChanges?await api(`/v1/transactions/${item.id}/classification`,{method:'PATCH',body:JSON.stringify({...body,expected_token:item.source_token})}):await api(`/v1/transactions/${item.id}/review`,{method:'PATCH',body:JSON.stringify({status_revisao:'validado',expected_token:item.source_token})});
      saved=true;
      if(!valid())return;
      if(result.item){setItem(result.item);setDraft(itemFields(result.item));}
      const linked = Boolean(item.commitment || item.installment_plan || item.obligation_series);
      if(linked && hasChanges){
        try {
          await api('/v1/commitment-profile/from-statement', {method:'POST', body: JSON.stringify({
            request_id: creationId(), transaction_id: String(item.id),
            vendedor: draft.vendedor || '', natureza: draft.natureza || '', categoria: draft.categoria || '',
            subcategoria: draft.subcategoria || '', plataforma: draft.plataforma || '', metodo_pagamento: draft.metodo_pagamento || '',
          })});
        } catch (syncError: any) {
          if(valid()) Alert.alert('Alteração salva', `O lançamento foi salvo. O compromisso vinculado não sincronizou: ${syncError.message}`);
        }
      }
      const count=Number(result.propagated_count||0),validatedPeers=Number(result.batch_validated_count||0);
      const message=validate?(validatedPeers?`${validatedPeers} registro${validatedPeers===1?' semelhante foi atualizado e validado':'s semelhantes foram atualizados e validados'}.`:'A revisão foi registrada.'):(count?`${count} registro${count===1?' semelhante foi atualizado':'s semelhantes foram atualizados'}.`:'A classificação foi salva.');
      Alert.alert(validate?'Registro validado':'Registro atualizado',message);
      void Promise.resolve().then(()=>onSaved(result.item,result)).catch(error=>{if(valid())Alert.alert('Alteração salva',`A gravação foi confirmada. Não foi possível atualizar a tela: ${error.message}`)});
    }catch(error:any){if(!valid())return;if(!saved&&(error.status===409||error.status===428))setConflict(true);Alert.alert(saved?'Alteração salva':'Não foi possível salvar',saved?`A gravação foi confirmada. Não foi possível atualizar a tela: ${error.message}`:error.message)}finally{savingLock.current=false;if(valid())setSaving(false)}
  };

  const reopen=async()=>{if(!item?.id||savingLock.current||conflict)return;savingLock.current=true;setSaving(true);const valid=captureScope();let saved=false;try{const result=await api(`/v1/transactions/${item.id}/review`,{method:'PATCH',body:JSON.stringify({status_revisao:'pendente',expected_token:item.source_token})});saved=true;if(!valid())return;if(result.item)setItem(result.item);Alert.alert('Revisão reaberta','O registro voltou à revisão pendente.');void Promise.resolve().then(()=>onSaved(result.item,result)).catch(error=>{if(valid())Alert.alert('Revisão reaberta',`A gravação foi confirmada. A atualização falhou: ${error.message}`)})}catch(error:any){if(!valid())return;if(!saved&&(error.status===409||error.status===428))setConflict(true);Alert.alert(saved?'Revisão reaberta':'Não foi possível reabrir a revisão',saved?`A gravação foi confirmada. A atualização falhou: ${error.message}`:error.message)}finally{savingLock.current=false;if(valid())setSaving(false)}};

  const loadCurrent=async()=>{
    if(savingLock.current)return;
    savingLock.current=true;setSaving(true);const valid=captureScope();
    try{
      const result=await api(`/v1/transactions/${item.id}`),fresh=result.item||result.transaction||result;
      if(!valid())return;
      const rebased=rebaseDraft(itemFields(item),itemFields(fresh),draft);
      setItem(fresh);setDraft(rebased.draft);setConflictFields(rebased.conflicts);setConflict(rebased.conflicts.length>0);
      setApplySimilarClassifications(false);setApplySimilarSupplier(false);
    }catch(error:any){if(!valid())return;Alert.alert('Não foi possível atualizar',error.message)}finally{savingLock.current=false;if(valid())setSaving(false)}
  };

  return <View>{conflict&&<View><Text style={s.matchText}>O registro mudou. Seus campos digitados foram preservados. Confira a fonte atual antes de reaplicar suas alterações.</Text>{conflictFields.map(field=><Text key={field} style={s.matchText}>{FIELDS.find(row=>row[0]===field)?.[1]}: atual “{currentFieldValue(item,field)||'vazio'}”; seu rascunho “{draft[field]||'vazio'}”.</Text>)}<Pressable accessibilityRole="button" disabled={saving} onPress={()=>loadCurrent()}><Text style={s.back}>Carregar registro atual e conferir</Text></Pressable>{conflictFields.length>0&&<Pressable accessibilityRole="button" disabled={saving} onPress={()=>{setConflict(false);setConflictFields([])}}><Text style={s.back}>Conferi as diferenças e quero reaplicar meus campos</Text></Pressable>}</View>}
    <Pressable accessibilityRole="button" onPress={back}><Text style={s.back}>‹ Voltar</Text></Pressable>
    <Text style={s.editorTitle}>{item.vendedor||item.fornecedor||item.estabelecimento_original||item.descricao_original||'Movimentação'}</Text>
    <Text style={s.amount}>{money(Number(item.valor||0))}</Text>
    <Text style={s.sourceText}>{financialDateLabel(String(item.data_evento||''))} · o texto original e a origem documental serão preservados</Text>
    <Text style={s.reviewStatus}>Revisão: {item.status_revisao==='validado'?'validado':'pendente'}{item.editado_em?' · editado manualmente':''}</Text>
    {loadingOptions&&<Text style={s.hint}>Carregando sugestões… Você já pode editar os campos.</Text>}
    {!!optionsError&&<View><Text accessibilityRole="alert" style={s.matchText}>{optionsError}{optionsStale?' As sugestões anteriores podem estar desatualizadas.':''} Seus campos foram preservados.</Text><Pressable accessibilityRole="button" disabled={saving||loadingOptions} onPress={retryOptions}><Text style={s.back}>Tentar carregar sugestões novamente</Text></Pressable></View>}
    {((classificationPreview?.total||0)>0||(classificationPreview?.same_description_count||0)>0)&&<View style={s.matchBox}><Text style={s.matchTitle}>{classificationPreview.total>0?`${classificationPreview.total} lançamento${classificationPreview.total===1?'':'s'} compatível${classificationPreview.total===1?'':'is'} com o lote`:'Descrição compartilhada, sem lote seguro'}</Text><Text style={s.matchText}>{classificationPreview.total>0?'A sugestão exige descrição específica e o mesmo sentido (entrada ou saída). O lote só será aplicado se você ativar abaixo.':`${classificationPreview.same_description_count} registro(s) têm a mesma descrição, mas faltam critérios suficientes para sugerir validação em lote.`}</Text>{classificationPreview.total>0&&<Pressable accessibilityRole="checkbox" accessibilityState={{checked:applySimilarClassifications}} aria-checked={applySimilarClassifications} onPress={()=>setApplySimilarClassifications(value=>!value)} style={[s.bulkToggle,applySimilarClassifications&&s.bulkToggleActive]}><Text style={[s.bulkToggleText,applySimilarClassifications&&s.bulkToggleTextActive]}>{applySimilarClassifications?'✓ ':''}Atualizar e validar o lote</Text></Pressable>}</View>}
    {FIELDS.map(([field,label])=>{
      const displayLabel=field==='vendedor'&&isIncoming(item)?'Origem':label;
      const typed=(draft[field]||'').trim();
      const available=field==='subcategoria'?((options.categoria_subcategorias as any)?.[draft.categoria]||[]):(options[field]||[]);
      const controlled=CONTROLLED_FIELDS.has(field);
      const showSuggestions=focusedField===field&&usesSuggestions(field)&&(typed.length>=2||available.length<=8);
      const matches=showSuggestions?rankedOptions(typed,available,4):[];
      if(controlled)return <ControlledChoice disabled={saving || loadingOptions} key={field} label={displayLabel} value={draft[field]||''} options={available} display={value=>optionLabel(value,(options.natureza_labels||{}) as any)} onChange={value=>setDraft(current=>({...current,[field]:value,...(field==='categoria'?{subcategoria:''}:{})}))} allowClear={!REQUIRED_CHOICES.has(field)} hint={field==='natureza'?`Indica a natureza econômica do lançamento e alimenta as visões de tributos e custos financeiros.${draft.natureza==='TRANSFERENCIA'?' Ela sai dos gastos. No Extrato, o vendedor e a categoria continuam visíveis até existirem conta de origem e conta de destino.':''}${!draft.natureza&&!item.natureza&&item.natureza_sugerida?` Sugestão: ${optionLabel(item.natureza_sugerida,options.natureza_labels)}. Selecione para aceitar.`:''}`:field==='categoria'?'Centro de custo ou finalidade do lançamento.':field==='subcategoria'?'As opções dependem da categoria selecionada.':undefined}/>;
      return <View key={field} style={s.field}>
        <Text style={s.label}>{displayLabel}</Text>
        <TextInput accessibilityLabel={displayLabel} value={draft[field]||''} onFocus={()=>setFocusedField(field)} onChangeText={value=>{setDraft(current=>({...current,[field]:value}));if(field==='vendedor')setMatchPreview(null)}} autoCapitalize={field==='vendedor'||field==='localidade'?'words':'sentences'} autoCorrect={field==='detalhe'} placeholder={`Informe ${displayLabel.toLocaleLowerCase('pt-BR')}`} placeholderTextColor="#92A4A2" style={s.input}/>
        {field==='vendedor'&&<Text style={s.hint}>Informe quem vendeu o pedido. Se não souber, deixe vazio.</Text>}
        {field==='plataforma'&&<Text style={s.hint}>iFood, 99Food ou Rappi ficam aqui, separados do vendedor.{item.plataforma_sugerida&&!item.plataforma?` Sugestão: ${item.plataforma_sugerida}.`:''}</Text>}
        {field==='localidade'&&<Text style={s.hint}>Use a cidade da compra, nunca a sede da plataforma.</Text>}
        {field==='recorrencia_tipo'&&<Text style={s.hint}>Classifica o padrão do lançamento sem alterar sua categoria.</Text>}
        {field==='recorrencia_periodicidade'&&<Text style={s.hint}>Em lançamentos eventuais, será salva como “não aplicável”.</Text>}
        {matches.length>0&&<View style={s.suggestions}>{matches.map(value=><Pressable accessibilityRole="button" key={value} onPress={()=>{setDraft(current=>field==='vendedor'?applyVendorDefaults(value,current,options,'categoria'):({...current,[field]:value}));setFocusedField(null)}} style={[s.chip,draft[field]===value&&s.chipActive]}><Text numberOfLines={1} style={[s.chipText,draft[field]===value&&s.chipTextActive]}>{optionLabel(value,(options.natureza_labels||{}) as any)}</Text></Pressable>)}</View>}
        {field==='vendedor'&&previewLoading&&<Text style={s.previewStatus}>Buscando registros semelhantes…</Text>}
        {field==='vendedor'&&!previewLoading&&matchPreview?.total>0&&<View style={s.matchBox}><Text style={s.matchTitle}>{matchPreview.total} correspondência{matchPreview.total===1?'':'s'} com confiança alta</Text><Text style={s.matchText}>Registros sem vendedor serão atualizados. Ao salvar e validar com o lote ativado, essas movimentações também serão validadas; capturas ainda exigem confirmação.</Text>{(matchPreview.samples||[]).slice(0,3).map((sample:any)=><Text key={`${sample.kind}-${sample.id}`} numberOfLines={1} style={s.matchSample}>• {sample.description} · {Math.round(sample.score*100)}%</Text>)}<Pressable accessibilityRole="checkbox" accessibilityState={{checked:applySimilarSupplier}} aria-checked={applySimilarSupplier} onPress={()=>setApplySimilarSupplier(value=>!value)} style={[s.bulkToggle,applySimilarSupplier&&s.bulkToggleActive]}><Text style={[s.bulkToggleText,applySimilarSupplier&&s.bulkToggleTextActive]}>{applySimilarSupplier?'✓ ':''}Atualizar e validar o lote de vendedores</Text></Pressable></View>}
      </View>;
    })}
    <Pressable accessibilityRole="button" disabled={saving||conflict} onPress={()=>save(true)} style={[s.primaryButton,saving&&s.disabled]}><Text style={s.primaryButtonText}>{saving?'Salvando…':'Salvar e validar'}</Text></Pressable>
    <Pressable accessibilityRole="button" disabled={saving||conflict} onPress={()=>save(false)} style={[s.secondaryButton,saving&&s.disabled]}><Text style={s.secondaryButtonText}>Salvar sem validar</Text></Pressable>
    <DiscardDraftAction disabled={saving} owner={item.id} label="Excluir rascunho da edição" onDiscard={()=>{setDraft(itemFields(item));setConflict(false);setConflictFields([]);setApplySimilarClassifications(false);setApplySimilarSupplier(false);setMatchPreview(null);setFocusedField(null);onCancel();}} />
    {item.status_revisao==='validado'&&<Pressable accessibilityRole="button" disabled={saving||conflict} onPress={()=>reopen()} style={[s.secondaryButton,saving&&s.disabled]}><Text style={s.secondaryButtonText}>Reabrir revisão</Text></Pressable>}
  </View>;
}

const s=StyleSheet.create({back:{color:'#4FD1B5',fontWeight:'700',marginTop:8,marginBottom:18},editorTitle:{color:'#F4F7F5',fontSize:20,fontWeight:'700',lineHeight:27},amount:{color:'#F4F7F5',fontSize:28,fontWeight:'700',marginTop:8},sourceText:{color:'#92A4A2',fontSize:11,lineHeight:17,marginTop:6,marginBottom:8},reviewStatus:{color:'#4FD1B5',fontSize:12,fontWeight:'700',marginBottom:16},field:{marginBottom:16},label:{color:'#AFC1BF',fontSize:12,fontWeight:'600',marginBottom:6},input:{backgroundColor:'#142326',borderRadius:12,borderWidth:1,borderColor:'#263D40',paddingHorizontal:13,paddingVertical:12,color:'#F5F8F6',fontSize:15},hint:{fontSize:10,lineHeight:15,color:'#92A4A2',marginTop:5},suggestions:{flexDirection:'row',flexWrap:'wrap',gap:6,marginTop:8},chip:{maxWidth:'48%',backgroundColor:'#17282B',borderRadius:12,paddingHorizontal:9,paddingVertical:6},chipActive:{backgroundColor:'#49C5AA'},chipText:{color:'#AFC1BF',fontSize:10},chipTextActive:{color:'#061412',fontWeight:'700'},previewStatus:{color:'#92A4A2',fontSize:11,marginTop:9},matchBox:{backgroundColor:'#10282A',borderWidth:1,borderColor:'#2B6B61',borderRadius:12,padding:12,marginTop:10},matchTitle:{color:'#67DFC3',fontSize:13,fontWeight:'700'},matchText:{color:'#AFC1BF',fontSize:11,lineHeight:16,marginTop:4},matchSample:{color:'#92A4A2',fontSize:10,lineHeight:15,marginTop:3},bulkToggle:{alignSelf:'flex-start',borderWidth:1,borderColor:'#3D625D',borderRadius:10,paddingHorizontal:10,paddingVertical:8,marginTop:10},bulkToggleActive:{backgroundColor:'#49C5AA',borderColor:'#49C5AA'},bulkToggleText:{color:'#C5D8D4',fontSize:11,fontWeight:'700'},bulkToggleTextActive:{color:'#061412'},primaryButton:{backgroundColor:'#49C5AA',padding:15,borderRadius:12,alignItems:'center',marginTop:4,marginBottom:8},primaryButtonText:{color:'#061412',fontWeight:'800'},secondaryButton:{borderWidth:1,borderColor:'#315054',padding:13,borderRadius:12,alignItems:'center',marginBottom:12},secondaryButtonText:{color:'#C5D8D4',fontWeight:'700'},disabled:{opacity:.4}});


