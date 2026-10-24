import {accountLabel, selectableAccount} from '../utils/accountProfile';
import {useAsyncScope} from '../utils/useAsyncScope';
import {requireMoneyInput} from '../utils/moneyInput';
import {useLocalBack} from '../utils/localBack';
import { Alert } from "../utils/appAlert";
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useState, useRef } from 'react';
import {ActivityIndicator, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import { useSQLiteContext } from '../utils/queueDb';
import { applyVendorDefaults, formatBrasilia, fromBrasiliaInput, rankedOptions, suggestionQuery, toBrasiliaInput } from '../utils/classification';
import ControlledChoice from './ControlledChoice';
import {preserveConflictingDraft, sameReviewSource} from '../utils/reviewDraftGuard';
import {pendingReviewCommand, pendingReviewCommands, reviewDraftWasCompleted, runReviewCommand} from '../utils/reviewCommand';
import {rebaseDraft} from '../utils/draftConflict';
import {deleteLocalDraft, flushDraftWrites, trackDraftWrite} from '../utils/localDrafts';

const FIELDS=[
  ['vendedor','Vendedor'],['plataforma','Plataforma'],['valor','Valor'],['data_evento','Data e hora (Brasília)'],['tipo','Tipo'],
  ['categoria_sugerida','Categoria'],['subcategoria','Subcategoria'],['natureza','Natureza'],
  ['metodo_pagamento','Meio de pagamento'],['fluxo','Fluxo'],
  ['localidade','Localidade'],['detalhe','Observações'],
] as const;
const optionField=(field:string)=>field==='categoria_sugerida'?'categoria':field;
const usesSuggestions=(field:string)=>!['valor','data_evento','detalhe'].includes(field);
const CONTROLLED_FIELDS=new Set(['categoria_sugerida','subcategoria','natureza','metodo_pagamento','fluxo']);
const isIncoming=(item:any)=>item?.fluxo==='entrada'||Number(item?.valor||0)>0;
const optionLabel=(value:string,labels:Record<string,string>={})=>labels[value]||({entrada:'Entrada',saida:'Saída'} as Record<string,string>)[value]||value.replaceAll('_',' ');
const DRAFT_INTENT_VERSION='_classification_intent_version';
const UNCONFIRMED_NATURE='_unconfirmed_legacy_nature';
const draftFor=(item:any):Record<string,string>=>({...Object.fromEntries(FIELDS.map(([field])=>{const current=field==='vendedor'?(item.vendedor||item.fornecedor||item.merchant_name||''):(item[field]||'');if(field==='valor')return[field,Number(item.valor||0).toFixed(2).replace('.',',')];if(field==='data_evento')return[field,toBrasiliaInput(String(item.data_evento||''))];return[field,String(current)]})),from_account_key:item.transfer?.from_account_key||'',to_account_key:item.transfer?.to_account_key||'',account_profile_id:item.account_profile_id||'',status_revisao:item.status_revisao||'pendente',[DRAFT_INTENT_VERSION]:'2'});
const PREFILLABLE_FIELDS=['vendedor','plataforma','tipo','categoria_sugerida','subcategoria','natureza','metodo_pagamento','localidade','account_profile_id'] as const;
const PREFILL_LABELS:Record<string,string>={vendedor:'Vendedor',plataforma:'Plataforma',tipo:'Tipo',categoria_sugerida:'Categoria',subcategoria:'Subcategoria',natureza:'Natureza',metodo_pagamento:'Meio de pagamento',localidade:'Localidade',account_profile_id:'Conta movimentada'};
const CLEARABLE_FIELDS=new Set(['vendedor','plataforma','categoria_sugerida','subcategoria','metodo_pagamento','localidade','detalhe']);
const initialDraftFor=(item:any):Record<string,string>=>{const base=draftFor(item);if(!item||item.status==='confirmed'||base.natureza==='TRANSFERENCIA')return base;if(item.plataforma_ausente)base.plataforma='';const defaults=item.historical_defaults||{};for(const field of PREFILLABLE_FIELDS){if(field==='plataforma'&&item.plataforma_ausente)continue;if(!String(base[field]||'').trim()&&defaults[field])base[field]=String(defaults[field]);}return base;};
const prefilledSummaryFor=(item:any,currentDraft:Record<string,string>):string=>{if(!item||item.status==='confirmed'||currentDraft.natureza==='TRANSFERENCIA')return '';const base=draftFor(item),defaults=item.historical_defaults||{},persisted=Array.isArray(item.historical_prefill_fields)?item.historical_prefill_fields:[];const names:string[]=[];for(const field of PREFILLABLE_FIELDS){const val=String(currentDraft[field]||'').trim();if(!val)continue;const fromDefaults=!String(base[field]||'').trim()&&defaults[field]&&val===String(defaults[field]).trim();const fromPersisted=persisted.includes(field)&&val===String(base[field]||'').trim();if((fromDefaults||fromPersisted)&&PREFILL_LABELS[field]&&!names.includes(PREFILL_LABELS[field]))names.push(PREFILL_LABELS[field]);}return names.length?`Pré-preenchido pelo histórico (${names.join(', ')}). Confira antes de confirmar.`:'';};
type Props={api:(path:string,init?:RequestInit)=>Promise<any>;money:(value:number)=>string;onUpdated:()=>Promise<void>;initialItem?:any;initialAccounts?:any[];onExit?:()=>void;reviewRecord?:boolean};
type DraftRow={provisional_id:string;item:string;draft:string;updated_at:string};
type ProvisionalCacheEntry={items:any[];page:{total:number;next_cursor:string|null};options:Record<string,any>;hasOptions:boolean;at:number};
const provisionalListCache=new WeakMap<Function,ProvisionalCacheEntry>();
export function primeProvisionalReviewCache(api:Function,captures:{items?:any[];total?:number;next_cursor?:string|null},options?:Record<string,any>|null){
  const prev=provisionalListCache.get(api);
  provisionalListCache.set(api,{items:captures.items||[],page:{total:captures.total||0,next_cursor:captures.next_cursor||null},options:options??prev?.options??{},hasOptions:Boolean(options||prev?.hasOptions),at:Date.now()});
}

const parseDraft=(row:DraftRow|null|undefined)=>{try{return row?{provisional_id:row.provisional_id,item:JSON.parse(row.item),draft:JSON.parse(row.draft),updated_at:row.updated_at}:null}catch{return null}};

export default function ProvisionalReview({api,money,onUpdated,initialItem,initialAccounts,onExit,reviewRecord=false}:Props){
  const db=useSQLiteContext();const captureScope=useAsyncScope(initialItem?.provisional_id);
  const cachedEntry=!initialItem&&typeof api==='function'?provisionalListCache.get(api):undefined;
  const [items,setItems]=useState<any[]>(()=>cachedEntry?.items||[]),[options,setOptions]=useState<Record<string,any>>(()=>cachedEntry?.options||(typeof api==='function'?provisionalListCache.get(api)?.options:undefined)||{});
  const [optionsError,setOptionsError]=useState(''),[optionsLoading,setOptionsLoading]=useState(false);
  const optionsGeneration=useRef(0);
  const retryOptions=async()=>{
    const valid=captureScope(),current=++optionsGeneration.current;setOptionsLoading(true);setOptionsError('');
    try{const available=await api('/v1/transactions/classification-options');if(valid()&&current===optionsGeneration.current)setOptions(available||{});}
    catch(error:any){if(valid()&&current===optionsGeneration.current)setOptionsError(error.message||'Não foi possível carregar as sugestões.');}
    finally{if(valid()&&current===optionsGeneration.current)setOptionsLoading(false);}
  };
  const [accountOptions,setAccountOptions]=useState<any[]>(()=>initialAccounts||[]);
  const [selected,setSelected]=useState<any>(initialItem||null),[draft,setDraft]=useState<Record<string,string>>({});
  const [loading,setLoading]=useState(false),[saving,setSaving]=useState(false);
  const [resumable,setResumable]=useState<ReturnType<typeof parseDraft>>(null);
  const [draftState,setDraftState]=useState("Sem alterações locais");
  const [conflict,setConflict]=useState<any>(null),[commandPending,setCommandPending]=useState(false);
  const [needsCurrent,setNeedsCurrent]=useState(false);
  const [pendingCommands,setPendingCommands]=useState<Awaited<ReturnType<typeof pendingReviewCommands>>>([]);
  const running=useRef(false),generation=useRef(0);
  const draftWrites=useRef(Promise.resolve()),latestDraftRow=useRef<any>(null);
  const draftEpoch=useRef(0),editorEpoch=useRef(0),persistedDraft=useRef<string|null>(null);
  const selectedSourceDraft=useMemo(()=>selected?initialDraftFor(selected):{},[selected]);
  const serializedSelected=useMemo(()=>selected?JSON.stringify(selected):'',[selected]);
  const [hasDraft,setHasDraft]=useState(false);
  const back=async()=>{
    const valid=captureScope();await draftWrites.current;
    if(selected?.provisional_id)await flushDraftWrites(db,'review',selected.provisional_id);
    if(valid()){if(initialItem)onExit?.();else setSelected(null);}
  };
  useLocalBack(back,Boolean(selected),20);
  useLayoutEffect(()=>{const owner=editorEpoch;owner.current++;return()=>{owner.current++}},[selected?.provisional_id]);
  const captureEditor=()=>{const valid=captureScope(),epoch=editorEpoch.current;return()=>valid()&&epoch===editorEpoch.current};
  const [page,setPage]=useState<{total:number;next_cursor:string|null}>(()=>cachedEntry?.page||{total:0,next_cursor:null});
  const [moreBusy,setMoreBusy]=useState(false);
  const storedDraft=useCallback(async(provisionalId?:string)=>{
    const where=provisionalId?' WHERE provisional_id=?':'';
    const rows=await db.getAllAsync<DraftRow>(`SELECT * FROM review_drafts${where} ORDER BY updated_at DESC LIMIT 1`,...(provisionalId?[provisionalId]:[]));
    const local=parseDraft(rows[0]);
    if(!local)return null;
    const legacyNature=local.draft[DRAFT_INTENT_VERSION]!=='2'&&!local.item.natureza?String(local.draft.natureza||'').trim():'';
    if(legacyNature)await preserveConflictingDraft(db,'provisional-classification-legacy',local.provisional_id,local);
    return {...local,draft:{...draftFor(local.item),...local.draft,[DRAFT_INTENT_VERSION]:'2',...(legacyNature?{natureza:'',[UNCONFIRMED_NATURE]:legacyNature}:{})}};
  },[db]);
  const forgetDraft=useCallback(async(provisionalId:string,snapshot?:DraftRow|null)=>{
    await draftWrites.current;await flushDraftWrites(db,'review',provisionalId);
    if(snapshot)await deleteLocalDraft(db,{kind:'review',id:provisionalId,title:'',description:'',updatedAt:snapshot.updated_at,row:snapshot,pendingCreation:false});
    else if(snapshot===null){if((await db.getAllAsync<DraftRow>('SELECT * FROM review_drafts WHERE provisional_id=?',provisionalId)).length)throw new Error('O rascunho mudou. Confira antes de excluir.');}
    else await db.runAsync('DELETE FROM review_drafts WHERE provisional_id=?',provisionalId);
    draftEpoch.current++;
    if(persistedDraft.current===provisionalId)persistedDraft.current=null;
  },[db]);
  const hasOptionsRef=useRef(Boolean(cachedEntry?.hasOptions));
  const load=useCallback(async(silent=false)=>{
    const hasWarmCache=!initialItem&&typeof api==='function'&&Boolean(provisionalListCache.get(api));
    const version=++generation.current;if(!silent&&!hasWarmCache)setLoading(true);
    try {
      if(initialItem?.provisional_id)await flushDraftWrites(db,'review',initialItem.provisional_id);
      const [rawLocal,commands]=await Promise.all([storedDraft(initialItem?.provisional_id),pendingReviewCommands(db)]);
      let local=rawLocal;
      if(local&&await reviewDraftWasCompleted(db,local))local=null;
      if(initialItem){
        let available:Record<string,any>|null=null;
        try{available=await api('/v1/transactions/classification-options')||{};if(version===generation.current){hasOptionsRef.current=true;setOptionsError('');}}catch(error:any){if(version===generation.current)setOptionsError(error.message||'Não foi possível carregar as sugestões.');}
        if(version!==generation.current)return;
        persistedDraft.current=local?initialItem.provisional_id:null;setHasDraft(!!local);
        setDraftState(local?'Rascunho salvo neste dispositivo.':'Sem alterações locais');
        if(available)setOptions(available);setNeedsCurrent(false);setSelected(local?.item||initialItem);setDraft(local?.draft||initialDraftFor(initialItem));
        setConflict(local&&!sameReviewSource(local.item,initialItem)?initialItem:null);
        const pending=!!await pendingReviewCommand(db,initialItem.provisional_id);if(version===generation.current)setCommandPending(pending);
      }else{
        const [captures,available]=await Promise.allSettled([api('/v1/provisional?active_only=true'),hasOptionsRef.current?Promise.resolve(null):api('/v1/transactions/classification-options')]);
        if(version!==generation.current)return;
        setResumable(local);
        setPendingCommands(commands);
        if(captures.status==='fulfilled'){const nextItems=captures.value.items||[],nextPage={total:captures.value.total||0,next_cursor:captures.value.next_cursor||null};setItems(nextItems);setPage(nextPage);if(typeof api==='function'){const prev=provisionalListCache.get(api);provisionalListCache.set(api,{items:nextItems,page:nextPage,options:available.status==='fulfilled'&&available.value!==null?(available.value||{}):(prev?.options||options),hasOptions:hasOptionsRef.current||(available.status==='fulfilled'&&available.value!==null),at:Date.now()});}}
        if(available.status==='fulfilled'){if(available.value!==null){hasOptionsRef.current=true;setOptions(available.value||{});}setOptionsError('');}
        else setOptionsError(available.reason?.message||'Não foi possível carregar as sugestões.');
        if(captures.status==='rejected'&&(available.status==='rejected'||hasOptionsRef.current)&&!local)throw captures.reason;
      }
    }catch(error:any){if(version===generation.current)Alert.alert('Não foi possível carregar as capturas',error.message)}
    finally{if(version===generation.current)setLoading(false)}
  },[api,db,initialItem,storedDraft]);
  useEffect(()=>{const lifetime=generation,task=setTimeout(()=>load(),0);return()=>{clearTimeout(task);lifetime.current++}},[load]);
  useEffect(()=>{if(initialAccounts?.length){setAccountOptions(initialAccounts);return;}let active=true;void api('/v1/account-positions').then(result=>{if(active)setAccountOptions(result.items||[])}).catch(()=>{});return()=>{active=false}},[api,initialAccounts]);
  useEffect(()=>{
    if(!selected?.provisional_id||!Object.keys(draft).length||conflict)return;
    if(persistedDraft.current!==selected.provisional_id&&!Object.entries(draft).some(([field,value])=>value!==(selectedSourceDraft[field]||'')))return;
    const epoch=draftEpoch.current,valid=captureScope();
    let active=true;const row={provisional_id:selected.provisional_id,item:selected,itemJson:serializedSelected,draft,draftJson:JSON.stringify(draft),updated_at:new Date().toISOString()};
    latestDraftRow.current=row;
    const timer=setTimeout(()=>{if(active&&valid()&&epoch===draftEpoch.current)setDraftState('Salvando rascunho…')},0);
    draftWrites.current=draftWrites.current.catch(()=>{}).then(async()=>{
      if(epoch!==draftEpoch.current||latestDraftRow.current!==row)return;
      await db.runAsync('INSERT INTO review_drafts(provisional_id,item,draft,updated_at) VALUES(?,?,?,?) ON CONFLICT(provisional_id) DO UPDATE SET item=excluded.item,draft=excluded.draft,updated_at=excluded.updated_at',row.provisional_id,row.itemJson,row.draftJson,row.updated_at);
      if(active&&valid()&&epoch===draftEpoch.current){persistedDraft.current=row.provisional_id;setHasDraft(true);setDraftState('Rascunho salvo neste dispositivo.');setResumable({provisional_id:row.provisional_id,item:row.item,draft:row.draft,updated_at:row.updated_at})}
    }).catch(()=>{if(active&&valid()&&epoch===draftEpoch.current)setDraftState('Falha ao salvar rascunho. Mantenha esta tela aberta e tente novamente.')});
    trackDraftWrite(db,'review',row.provisional_id,draftWrites.current);
    return()=>{active=false;clearTimeout(timer)};
  },[captureScope,conflict,db,draft,selected,selectedSourceDraft,serializedSelected]);
  const open=async(item:any)=>{
    if(running.current)return;running.current=true;setLoading(true);const valid=captureScope();
    try{
      await draftWrites.current;await flushDraftWrites(db,'review',item.provisional_id);
      const [response,local,pendingCmd]=await Promise.all([api(`/v1/provisional/${item.provisional_id}`),storedDraft(item.provisional_id),pendingReviewCommand(db,item.provisional_id)]);
      const current=response?.item;
      if(!current)throw new Error('Não foi possível conferir a versão atual. O rascunho permanece salvo.');
      if(!valid())return;
      persistedDraft.current=local?item.provisional_id:null;setHasDraft(!!local);
      setDraftState(local?'Rascunho salvo neste dispositivo.':'Sem alterações locais');
      setNeedsCurrent(false);setSelected(local?.item||current);setDraft(local?.draft||initialDraftFor(current));
      setConflict(local&&!sameReviewSource(local.item,current)?current:null);
      setCommandPending(!!pendingCmd);
    }catch(error:any){if(!valid())return;Alert.alert('Não foi possível abrir a captura',error.message)}
    finally{running.current=false;if(valid())setLoading(false)}
  };
  const resume=()=>{if(resumable)return open(resumable.item)};
  const confirmForgetDraft=async(localId:string,editing:boolean)=>{
    if(running.current)return;
    const valid=captureEditor(),scopeValid=captureScope();running.current=true;setSaving(true);
    let snapshot:DraftRow|null=null;
    try{await draftWrites.current;await flushDraftWrites(db,'review',localId);snapshot=(await db.getAllAsync<DraftRow>('SELECT * FROM review_drafts WHERE provisional_id=?',localId))[0]||null;}
    catch(error:any){if(valid())Alert.alert('Não foi possível conferir o rascunho',error.message);return;}
    finally{running.current=false;if(valid())setSaving(false);}
    if(!valid())return;
    const pending=editing?commandPending:pendingCommands.some(command=>command.command_key===`provisional:${localId}`);
    Alert.alert('Excluir rascunho?', 'As edições locais desta revisão serão excluídas. A captura e as movimentações já salvas continuam disponíveis.'+(pending?' A tentativa de gravação já enviada continua aguardando confirmação.':''),[
      {text:'Cancelar',style:'cancel'},
      {text:'Excluir rascunho',style:'destructive',onPress:async()=>{
        if(!valid()||running.current)return;running.current=true;setSaving(true);generation.current++;
        try{
          let current=editing?(conflict||initialItem||selected):null,needsRefresh=false;
          if(editing){try{current=(await api(`/v1/provisional/${localId}`)).item||current}catch{needsRefresh=true}}
          if(!valid())return;
          await forgetDraft(localId,snapshot);
          if(!valid())return;editorEpoch.current++;
          setResumable(value=>value?.provisional_id===localId?null:value);setHasDraft(false);
          setDraftState('Rascunho excluído. Edite um campo para iniciar outro.');
          if(editing){setSelected(current);setDraft(initialDraftFor(current));setConflict(null);setNeedsCurrent(needsRefresh);}
        }catch(error:any){if(valid())Alert.alert('Não foi possível excluir o rascunho',error.message)}
        finally{running.current=false;if(scopeValid())setSaving(false)}
      }},
    ]);
  };
  const loadCurrent=async()=>{
    if(running.current||!selected?.provisional_id)return;
    running.current=true;setSaving(true);const valid=captureScope();
    try{
      const current=(await api(`/v1/provisional/${selected.provisional_id}`)).item;
      if(!valid())return;
      if(!current)throw new Error('Não foi possível conferir a versão atual. Seu rascunho permanece salvo.');
      setConflict(current);setNeedsCurrent(false);
    }catch(error:any){if(valid())Alert.alert('Rascunho preservado',error.message)}
    finally{running.current=false;if(valid())setSaving(false)}
  };
  const resolveConflict=async(recover:boolean)=>{
    if(running.current||!conflict||!selected)return;running.current=true;setSaving(true);const valid=captureScope();
    try{await draftWrites.current;await preserveConflictingDraft(db,'provisional',selected.provisional_id,{item:selected,draft});if(!valid())return;setDraft(recover?rebaseDraft(draftFor(selected),draftFor(conflict),draft).draft:initialDraftFor(conflict));setSelected(conflict);setConflict(null);setNeedsCurrent(false);}
    catch(error:any){if(!valid())return;Alert.alert('Rascunho preservado',`Não foi possível guardar a versão anterior: ${error.message}`)}
    finally{running.current=false;if(valid())setSaving(false)}
  };
  const payload=()=>{
    const source=draftFor(selected);
    const values=Object.fromEntries(FIELDS.map(([field])=>[field,draft[field]?.trim()||'']).filter(([field,value])=>value!==(source[field]?.trim()||'')||(CLEARABLE_FIELDS.has(field)&&value===''&&Boolean(selectedSourceDraft[field]?.trim()))).map(([field,value])=>[field,field==='valor'?Number(requireMoneyInput(value,{signed:true,allowZero:false,max:100_000_000})):field==='data_evento'?fromBrasiliaInput(value):value]));
    const accountFields=draft.natureza==='TRANSFERENCIA'?['from_account_key','to_account_key']:['account_profile_id'];
    for(const field of accountFields)if(draft[field]!==source[field]||(draft[field]===''&&Boolean(selectedSourceDraft[field])))values[field]=draft[field]||'';
    return values;
  };
  const failedCommand=async(error:any,provisionalId:string,valid:()=>boolean,title:string)=>{
    const pending=!!await pendingReviewCommand(db,provisionalId);
    if(!valid())return;
    setCommandPending(pending);
    if(!pending&&[409,428].includes(error.status))setNeedsCurrent(true);
    Alert.alert(title,`${error.message}\n\nA tentativa e seus campos permanecem salvos.`);
  };
  const finishConfirmed=async(provisionalId:string,confirmed:boolean,valid:()=>boolean,removedFromActive=confirmed,updatedItem?:any)=>{
    setCommandPending(false);setConflict(null);
    let notice='';
    try{await forgetDraft(provisionalId)}catch{notice=' O rascunho foi preservado porque a limpeza local não terminou.'}
    if(!valid())return;setResumable(current=>current?.provisional_id===provisionalId?null:current);setHasDraft(false);setSelected(null);
    if(removedFromActive){setItems(current=>{const next=current.filter(row=>row.provisional_id!==provisionalId);if(typeof api==='function'){const prev=provisionalListCache.get(api);if(prev)provisionalListCache.set(api,{...prev,items:next,page:{...prev.page,total:Math.max(0,prev.page.total-1)},at:Date.now()});}return next;});setPage(current=>({...current,total:Math.max(0,current.total-1)}));}
    else if(updatedItem){setItems(current=>{const next=current.map(row=>row.provisional_id===provisionalId?{...row,...updatedItem}:row);if(typeof api==='function'){const prev=provisionalListCache.get(api);if(prev)provisionalListCache.set(api,{...prev,items:next,at:Date.now()});}return next;});}
    Alert.alert(confirmed?'Captura confirmada':'Captura atualizada',(confirmed?'A movimentação foi confirmada e entrou nos totais financeiros.':'As correções foram salvas.')+notice);
    void Promise.resolve().then(onUpdated).catch(()=>{if(valid())Alert.alert('Captura salva','A gravação foi confirmada. Atualize a tela para conferir os dados atuais.');});
    if(initialItem)onExit?.();else void load(true);
  };
  const save=async(confirm=false)=>{
    if(running.current||!selected?.provisional_id||conflict||needsCurrent)return;
    const provisionalId=selected.provisional_id;
    let parsed:Record<string,any>;
    try{parsed=payload()}catch(error:any){Alert.alert('Confira o valor',error.message);return;}
    if(!confirm&&!reviewRecord&&!Object.keys(parsed).length){Alert.alert('Nada para salvar','Altere pelo menos um campo antes de salvar.');return}
    if('valor' in parsed&&(!Number.isFinite(parsed.valor)||parsed.valor===0)){Alert.alert('Confira o valor','Informe um valor financeiro válido e diferente de zero.');return}
    if(draft.natureza==='TRANSFERENCIA'&&(!draft.from_account_key||!draft.to_account_key||draft.from_account_key===draft.to_account_key)){Alert.alert('Confira as contas','Escolha contas de origem e destino diferentes.');return}
    running.current=true;setSaving(true);const valid=captureScope();
    try{
      await draftWrites.current;
      const saved=await runReviewCommand(db,api,provisionalId,`/v1/provisional/${provisionalId}`,{...parsed,...(confirm?{action:'confirm'}:{}),...(reviewRecord?{validate_review:(draft.status_revisao||selected.status_revisao)==='validado'}:{}),expected_revision:selected.revision||1});
      if(valid())await finishConfirmed(provisionalId,confirm,valid,confirm,saved?.item);
    }catch(error:any){if(valid())await failedCommand(error,provisionalId,valid,'Não foi possível salvar')}
    finally{running.current=false;if(valid())setSaving(false)}
  };
  const retryPending=async(id=selected?.provisional_id)=>{
    if(running.current||!id)return;
    running.current=true;setSaving(true);const valid=captureScope();const provisionalId=id;
    try{
      await draftWrites.current;if(selected?.provisional_id===id)await preserveConflictingDraft(db,'provisional',provisionalId,{item:selected,draft});
      const saved=await runReviewCommand(db,api,provisionalId);
      const isConfirmed=saved.item?.status==='confirmed'||saved.action==='confirm';
      if(valid())await finishConfirmed(provisionalId,isConfirmed,valid,isConfirmed,saved?.item);
    }catch(error:any){if(valid())await failedCommand(error,provisionalId,valid,'Tentativa preservada')}finally{running.current=false;if(valid())setSaving(false)}
  };
  const reparse=async()=>{
    if(running.current||!selected?.provisional_id||conflict||needsCurrent)return;
    running.current=true;const provisionalId=selected.provisional_id;setSaving(true);const valid=captureScope();
    try{const saved=await runReviewCommand(db,api,provisionalId,`/v1/provisional/${provisionalId}/reparse`,{expected_revision:selected.revision||1});if(valid())await finishConfirmed(provisionalId,false,valid,false,saved?.item);}
    catch(error:any){if(valid())await failedCommand(error,provisionalId,valid,'Não foi possível reler')}
    finally{running.current=false;if(valid())setSaving(false)}
  };
  const decide=(item:any,action:'confirm'|'cancel')=>{const valid=captureScope();const run=async()=>{
    if(!valid()||running.current)return;running.current=true;setSaving(true);
    try{await runReviewCommand(db,api,item.provisional_id,`/v1/provisional/${item.provisional_id}/decision`,{action,expected_revision:item.revision||1});if(valid())await finishConfirmed(item.provisional_id,action==='confirm',valid,true)}
    catch(error:any){if(!valid())return;Alert.alert('Não foi possível concluir',error.message);await load()}finally{running.current=false;if(valid())setSaving(false)}
  };if(action==='cancel')Alert.alert('Descartar captura?','Ela permanecerá no histórico de auditoria, mas não entrará nos totais.',[{text:'Voltar',style:'cancel'},{text:'Descartar',style:'destructive',onPress:()=>run()}]);else return run()};
  const loadMore=async()=>{
    const valid=captureScope(), version=generation.current, cursor=page.next_cursor;
    setMoreBusy(true);
    try{const result=await api(`/v1/provisional?active_only=true&cursor=${encodeURIComponent(cursor!)}`);if(!valid()||version!==generation.current)return;setItems(current=>Array.from(new Map([...current,...result.items].map(item=>[item.provisional_id,item])).values()));setPage({total:result.total,next_cursor:result.next_cursor});}
    catch(error:any){if(valid()&&version===generation.current)Alert.alert('Falha ao carregar',error.message);}
    finally{if(valid())setMoreBusy(false);}
  };

  if(selected)return <View style={s.editor}>
    <Pressable accessibilityRole="button" onPress={back}><Text style={s.back}>{initialItem?(reviewRecord?'‹ Voltar aos registros':'‹ Voltar ao detalhe'):'‹ Voltar às capturas'}</Text></Pressable>
    <Text style={s.title}>{initialItem?'Editar movimentação':'Revisar captura'}</Text><Text style={s.raw}>Texto capturado: {selected.merchant_raw}</Text>{!initialItem&&selected.source_type==='android_notification'&&<Pressable accessibilityRole="button" disabled={saving} onPress={()=>reparse()} style={s.secondary}><Text style={s.secondaryText}>Reler notificação completa</Text></Pressable>}
    <Text accessibilityLiveRegion="polite" style={s.saved}>{draftState}</Text>{!!prefilledSummaryFor(selected,draft)&&<Text style={s.saved}>{prefilledSummaryFor(selected,draft)}</Text>}
    {!!optionsError&&<View><Text accessibilityRole="alert" style={s.resumeText}>{optionsError} Os campos e as sugestões anteriores foram preservados.</Text><Pressable accessibilityRole="button" disabled={saving||loading||optionsLoading} onPress={retryOptions} style={s.secondary}><Text style={s.secondaryText}>{optionsLoading?'Carregando sugestões…':'Tentar carregar sugestões novamente'}</Text></Pressable></View>}
    {(hasDraft||!!conflict||Object.entries(draft).some(([field,value])=>value!==(initialDraftFor(selected)[field]||'')))&&<Pressable accessibilityRole="button" accessibilityLabel="Excluir rascunho desta revisão" disabled={saving||loading} onPress={()=>confirmForgetDraft(selected.provisional_id,true)} style={s.secondary}><Text style={s.cancelText}>Excluir rascunho</Text></Pressable>}
    {needsCurrent&&<View accessibilityRole="alert"><Text style={s.resumeText}>Carregue a versão atual da captura antes de salvar.{hasDraft?' Suas edições permanecem no rascunho.':''}</Text><Pressable accessibilityRole="button" accessibilityLabel="Carregar captura atual e conferir" disabled={saving||commandPending} onPress={loadCurrent} style={s.secondary}><Text style={s.secondaryText}>Carregar captura atual e conferir</Text></Pressable></View>}
    {!!conflict&&<View accessibilityRole="alert"><Text style={s.resumeText}>Esta captura mudou desde o rascunho salvo. Suas edições permanecem preservadas e não podem ser gravadas sobre a versão atual sem conferência.</Text>
      {FIELDS.filter(([field])=>draftFor(selected)[field]!==draftFor(conflict)[field]).map(([field,label])=><Text key={field} style={s.resumeText}>{label}: atual “{draftFor(conflict)[field]||'vazio'}”; seu rascunho “{draft[field]||'vazio'}”.</Text>)}
      <Pressable accessibilityRole="button" accessibilityLabel="Usar versão atual e preservar meu rascunho" disabled={saving||commandPending} onPress={()=>resolveConflict(false)} style={s.secondary}><Text style={s.secondaryText}>Usar versão atual e preservar meu rascunho</Text></Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Conferi a mudança: recuperar minhas correções" disabled={saving||commandPending} onPress={()=>resolveConflict(true)} style={s.secondary}><Text style={s.secondaryText}>Conferi a mudança: recuperar minhas correções</Text></Pressable>
    </View>}
    {commandPending&&<Pressable accessibilityRole="button" accessibilityLabel="Concluir tentativa anterior" disabled={saving} onPress={()=>retryPending()} style={s.secondary}><Text style={s.secondaryText}>Concluir tentativa anterior</Text></Pressable>}
    {draft.natureza==='TRANSFERENCIA'&&<><Text style={s.hint}>Transferências próprias não têm vendedor nem despesa. Indique as duas contas para acompanhar os saldos.</Text><ControlledChoice disabled={saving || loading} label="Conta de origem" value={draft.from_account_key||''} options={accountOptions.filter(item=>selectableAccount(item)||[draft.from_account_key,draft.to_account_key,draft.account_profile_id].includes(item.key)).map(item=>item.key)} display={value=>accountLabel(accountOptions.find(item=>item.key===value),value)} onChange={value=>setDraft(current=>({...current,from_account_key:value}))} allowClear={false}/><ControlledChoice disabled={saving || loading} label="Conta de destino" value={draft.to_account_key||''} options={accountOptions.filter(item=>selectableAccount(item)||[draft.from_account_key,draft.to_account_key,draft.account_profile_id].includes(item.key)).map(item=>item.key)} display={value=>accountLabel(accountOptions.find(item=>item.key===value),value)} onChange={value=>setDraft(current=>({...current,to_account_key:value}))} allowClear={false}/></>}
    {FIELDS.map(([field,label])=>{if(draft.natureza==='TRANSFERENCIA'&&['vendedor','plataforma','categoria_sugerida','subcategoria','localidade'].includes(field))return null;const incoming=field==='vendedor'&&(draft.fluxo==='entrada'||Number(String(draft.valor||'').replace(',','.'))>0||isIncoming(selected));const displayLabel=incoming?'Origem':label;const available=field==='subcategoria'?((options.categoria_subcategorias||{})[draft.categoria_sugerida]||[]):(options[optionField(field)]||[]);const controlled=CONTROLLED_FIELDS.has(field);if(controlled)return <ControlledChoice disabled={saving || loading} key={field} label={displayLabel} value={draft[field]||''} options={available} display={value=>optionLabel(value,options.natureza_labels||{})} onChange={value=>setDraft(current=>({...current,[field]:value,...(field==='natureza'?{[UNCONFIRMED_NATURE]:''}:{}),...(field==='categoria_sugerida'?{subcategoria:''}:{})}))} allowClear={!['natureza','fluxo','categoria_sugerida'].includes(field)} hint={field==='natureza'?`Indica a natureza econômica do lançamento.${!draft.natureza&&!selected.natureza&&draft[UNCONFIRMED_NATURE]?` Rascunho anterior: ${optionLabel(draft[UNCONFIRMED_NATURE],options.natureza_labels)}, ainda não confirmado. Selecione a natureza para aceitar.`:!draft.natureza&&!selected.natureza&&selected.natureza_sugerida?` Sugestão: ${optionLabel(selected.natureza_sugerida,options.natureza_labels)}. Selecione para aceitar.`:''}`:field==='categoria_sugerida'?('Classifique a finalidade do gasto, e não o aplicativo usado.'+(!draft.categoria_sugerida&&selected.categoria_inferida?' Sugestão: '+selected.categoria_inferida+'. Selecione para aceitar.':'')):field==='subcategoria'?'As opções dependem da categoria selecionada.':undefined}/>;const matches=usesSuggestions(field)?rankedOptions(suggestionQuery(field,draft[field]||'',selected),available):[];return <View key={field} style={s.field}><Text style={s.label}>{displayLabel}</Text><TextInput accessibilityLabel={displayLabel} value={draft[field]||''} onChangeText={value=>setDraft(current=>({...current,[field]:value}))} onBlur={()=>{if(field==='vendedor'&&draft.vendedor?.trim())setDraft(current=>applyVendorDefaults(current.vendedor.trim(),current,options,'categoria_sugerida'))}} keyboardType={field==='valor'?'decimal-pad':'default'} autoCapitalize={field==='vendedor'||field==='localidade'?'words':'sentences'} placeholder={`Informe ${displayLabel.toLocaleLowerCase('pt-BR')}`} placeholderTextColor="#92A4A2" style={[s.input,field==='detalhe'&&s.multiline]} multiline={field==='detalhe'}/>{field==='vendedor'&&<Text style={s.hint}>Digite quem vendeu o pedido. Se não souber, deixe vazio.</Text>}{field==='plataforma'&&<Text style={s.hint}>iFood, 99Food ou Rappi ficam aqui, separados do vendedor.{selected.plataforma_sugerida&&!selected.plataforma?` Sugestão: ${selected.plataforma_sugerida}. Preencha somente se conferir.`:''}</Text>}{field==='localidade'&&<Text style={s.hint}>Use a cidade da compra, nunca a sede da plataforma.</Text>}{matches.length>0&&<View style={s.chips}>{matches.map(value=><Pressable accessibilityRole="button" key={value} onPress={()=>setDraft(current=>field==='vendedor'?applyVendorDefaults(value,current,options,'categoria_sugerida'):({...current,[field]:value}))} style={[s.chip,draft[field]===value&&{backgroundColor:'#49C5AA'}]}><Text numberOfLines={1} style={[s.chipText,draft[field]===value&&{color:'#061412',fontWeight:'700'}]}>{value}</Text></Pressable>)}</View>}</View>})}
    {reviewRecord&&<ControlledChoice label="Status de revisão" value={draft.status_revisao||selected.status_revisao||'pendente'} options={['pendente','validado']} display={value=>value==='validado'?'Validado':'Pendente'} onChange={value=>setDraft(current=>({...current,status_revisao:value}))} disabled={saving||loading} allowClear={false} hint="A confirmação do PDF valida as linhas importadas. Escolha Pendente para reabrir a conferência."/>}
    {draft.natureza!=='TRANSFERENCIA'&&<ControlledChoice disabled={saving || loading} label="Conta movimentada" value={draft.account_profile_id||''} options={accountOptions.filter(item=>selectableAccount(item)||[draft.from_account_key,draft.to_account_key,draft.account_profile_id].includes(item.key)).map(item=>item.key)} display={value=>accountLabel(accountOptions.find(item=>item.key===value),value)} onChange={value=>setDraft(current=>({...current,account_profile_id:value}))} hint="Ajuda a atualizar o saldo após você informar uma referência."/>}
    <Pressable accessibilityRole="button" disabled={saving||!!conflict||needsCurrent||commandPending} onPress={()=>save(false)} style={s.secondary}><Text style={s.secondaryText}>Salvar correções</Text></Pressable>
    {!initialItem&&<Pressable accessibilityRole="button" disabled={saving||!!conflict||needsCurrent||commandPending} onPress={()=>save(true)} style={s.primary}><Text style={s.primaryText}>{saving?'Salvando…':'Salvar e confirmar'}</Text></Pressable>}
  </View>;

  return <View><View style={s.headingRow}><Text style={s.heading}>Capturas provisórias</Text><View style={s.count}><Text style={s.countText}>{items.length}</Text></View></View>
    <Text style={s.help}>Valide as notificações recebidas antes de incorporá-las aos seus dados.</Text>
    {pendingCommands.map(command=><View key={command.command_key} style={s.resumeCard}><Text style={s.resumeTitle}>Tentativa aguardando confirmação</Text><Text style={s.resumeText}>A resposta não foi confirmada. Conclua esta tentativa com o mesmo identificador antes de alterar a captura.</Text>
      <Pressable accessibilityRole="button" accessibilityLabel={`Concluir tentativa de ${command.command_key.slice(12)}`} disabled={saving} onPress={()=>retryPending(command.command_key.slice(12))} style={s.primary}><Text style={s.primaryText}>Concluir tentativa anterior</Text></Pressable>
    </View>)}
    {resumable&&<View style={s.resumeCard}><Text style={s.resumeTitle}>Revisão interrompida</Text><Text style={s.resumeText}>As alterações de {resumable.draft.vendedor||resumable.item.merchant_raw||'uma captura'} estão salvas neste dispositivo.</Text><Pressable accessibilityRole="button" disabled={saving} onPress={resume} style={s.primary}><Text style={s.primaryText}>Retomar de onde parei</Text></Pressable><Pressable accessibilityRole="button" accessibilityLabel="Excluir rascunho da revisão interrompida" disabled={saving} onPress={()=>confirmForgetDraft(resumable.provisional_id,false)} style={s.secondary}><Text style={s.cancelText}>Excluir rascunho</Text></Pressable></View>}
    {loading&&<ActivityIndicator color="#4FD1B5" style={s.loader}/>}{!loading&&!resumable&&items.length===0&&<Text style={s.empty}>Nenhuma captura aguardando validação.</Text>}
    <Text style={s.help}>{items.length} de {page.total} capturas</Text>
    {page.next_cursor&&<Pressable accessibilityRole="button" disabled={moreBusy} onPress={loadMore}><Text style={s.confirmText}>{moreBusy?"Carregando…":"Carregar mais capturas"}</Text></Pressable>}
    {items.map(item=><View key={item.provisional_id} style={s.card}><View style={s.cardTop}><View style={s.cardText}><Text numberOfLines={2} style={s.cardTitle}>{item.natureza==='TRANSFERENCIA'?'Transferência entre minhas contas':item.vendedor||item.fornecedor||item.merchant_name||item.historical_defaults?.vendedor||item.merchant_raw||(isIncoming(item)?'Origem não identificada':'Captura sem vendedor')}</Text><Text numberOfLines={2} style={s.raw}>{item.merchant_raw}</Text><Text style={s.meta}>{formatBrasilia(String(item.data_evento||''))} · {item.natureza==='TRANSFERENCIA'?'Transferência neutra':item.categoria_sugerida||item.historical_defaults?.categoria_sugerida||'Sem categoria'}</Text></View><Text style={s.amount}>{money(Number(item.valor||0))}</Text></View><Pressable accessibilityRole="button" onPress={()=>open(item)} style={s.primary}><Text style={s.primaryText}>Revisar e editar</Text></Pressable><View style={s.actions}><Pressable accessibilityRole="button" onPress={()=>decide(item,'confirm')} style={s.action}><Text style={s.confirmText}>Confirmar</Text></Pressable><Pressable accessibilityRole="button" onPress={()=>decide(item,'cancel')} style={s.action}><Text style={s.cancelText}>Descartar</Text></Pressable></View></View>)}
  </View>;
}

const s=StyleSheet.create({headingRow:{flexDirection:'row',alignItems:'center',gap:8,marginTop:18},heading:{fontSize:18,fontWeight:'700',color:'#F4F7F5'},count:{backgroundColor:'#E66F66',borderRadius:12,minWidth:23,height:23,alignItems:'center',justifyContent:'center'},countText:{color:'#fff',fontSize:11,fontWeight:'800'},help:{fontSize:12,lineHeight:18,color:'#92A4A2',marginTop:6,marginBottom:10},saved:{fontSize:11,lineHeight:16,color:'#4FD1B5',marginTop:10},resumeCard:{backgroundColor:'#183034',borderWidth:1,borderColor:'#4FD1B5',borderRadius:14,padding:14,marginVertical:8},resumeTitle:{fontSize:15,fontWeight:'800',color:'#F4F7F5'},resumeText:{fontSize:11,lineHeight:17,color:'#AFC1BF',marginTop:5},loader:{marginVertical:20},empty:{color:'#92A4A2',paddingVertical:14},card:{backgroundColor:'#142326',borderRadius:14,padding:14,marginTop:10},cardTop:{flexDirection:'row',flexWrap:'wrap',gap:10},cardText:{flex:1,minWidth:150},cardTitle:{fontSize:15,lineHeight:20,fontWeight:'700',color:'#F4F7F5'},raw:{fontSize:11,lineHeight:16,color:'#92A4A2',marginTop:5},meta:{fontSize:11,color:'#92A4A2',marginTop:6},amount:{flexShrink:0,maxWidth:'100%',textAlign:'right',fontSize:16,fontWeight:'700',color:'#FF8C83'},primary:{backgroundColor:'#49C5AA',padding:12,borderRadius:10,alignItems:'center',marginTop:12},primaryText:{color:'#061412',fontWeight:'800'},secondary:{backgroundColor:'#1A2A2D',borderWidth:1,borderColor:'#315054',padding:12,borderRadius:10,alignItems:'center',marginTop:12},secondaryText:{color:'#C5D8D4',fontWeight:'700'},actions:{flexDirection:'row',gap:8,marginTop:7},action:{flex:1,padding:10,alignItems:'center'},confirmText:{color:'#4FD1B5',fontWeight:'700'},cancelText:{color:'#FF8C83',fontWeight:'700'},editor:{marginTop:16},back:{color:'#4FD1B5',fontWeight:'700',marginBottom:14},title:{fontSize:21,fontWeight:'700',color:'#F4F7F5'},field:{marginTop:14},label:{fontSize:12,fontWeight:'600',color:'#AFC1BF',marginBottom:6},hint:{fontSize:10,lineHeight:15,color:'#92A4A2',marginTop:5},input:{backgroundColor:'#142326',borderRadius:12,borderWidth:1,borderColor:'#263D40',paddingHorizontal:13,paddingVertical:12,color:'#F5F8F6',fontSize:15},multiline:{minHeight:80,textAlignVertical:'top'},chips:{flexDirection:'row',flexWrap:'wrap',gap:6,marginTop:7},chip:{maxWidth:'48%',backgroundColor:'#17282B',borderRadius:12,paddingHorizontal:9,paddingVertical:6},chipText:{color:'#AFC1BF',fontSize:10}});


