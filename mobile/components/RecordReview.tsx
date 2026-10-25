import { financialDateLabel } from "../utils/classification";
import {recordCategory} from '../utils/movementFilters';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {ActivityIndicator, StyleSheet, Text, View, useWindowDimensions} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {compactLayout} from '../utils/responsiveLayout';
import TransactionEditor from './TransactionEditor';
import ProvisionalReview from './ProvisionalReview';
import SingleLineValue from './SingleLineValue';

const PAGE_SIZE=50;
const isIncoming=(item:any)=>item?.fluxo==='entrada'||Number(item?.valor||0)>0;
type Props={api:(path:string,init?:RequestInit)=>Promise<any>;money:(value:number)=>string;onUpdated:()=>Promise<void>;lazy?:boolean};
type RecordPageEntry={items:any[];total:number;at:number};
const recordReviewCache=new WeakMap<Function,Map<string,RecordPageEntry>>();
const getRecordCache=(api:Function)=>{
  let map=recordReviewCache.get(api);
  if(!map){map=new Map();recordReviewCache.set(api,map);}
  return map;
};
export function primeRecordReviewCache(api:Function,reviewStatus:'pendente'|'validado'|'todos',search:string,offset:number,page:{items?:any[];total?:number}){
  getRecordCache(api).set(JSON.stringify([reviewStatus,search,offset]),{items:page.items||[],total:page.total||0,at:Date.now()});
}
export function invalidateRecordReviewCache(api:Function){
  recordReviewCache.delete(api);
}

export default function RecordReview({api,money,onUpdated,lazy=false}:Props){
  const {width,fontScale}=useWindowDimensions(),compact=compactLayout(width,fontScale);
  const initialKey=JSON.stringify(['pendente','',0]);
  const initialCached=typeof api==='function'?getRecordCache(api).get(initialKey):undefined;
  const [expanded,setExpanded]=useState(()=>!lazy||Boolean(initialCached));
  const [items,setItems]=useState<any[]>(()=>initialCached?.items||[]),[selected,setSelected]=useState<any>(null);
  const [search,setSearch]=useState(''),[appliedSearch,setAppliedSearch]=useState('');
  const [reviewStatus,setReviewStatus]=useState<'pendente'|'validado'|'todos'>('pendente');
  const [offset,setOffset]=useState(0),[total,setTotal]=useState(()=>initialCached?.total||0),[loading,setLoading]=useState(false);
  const [resultKey,setResultKey]=useState(()=>initialCached?initialKey:'');
  const [readError,setReadError]=useState(''),[refreshVersion,setRefreshVersion]=useState(0);
  const queryKey=JSON.stringify([reviewStatus,appliedSearch,offset]);
  const cachedPage=typeof api==='function'?getRecordCache(api).get(queryKey):undefined;
  const generation=useRef(0);
  const loadController=useRef<AbortController|null>(null);
  const load=useCallback(async(silent=false)=>{
    loadController.current?.abort();
    const controller=typeof AbortController!=='undefined'?new AbortController():null;
    loadController.current=controller;
    const warm=typeof api==='function'?getRecordCache(api).get(JSON.stringify([reviewStatus,appliedSearch,offset])):undefined;
    const current=++generation.current;if(!silent&&!warm)setLoading(true);setReadError('');
    try{
      const query=new URLSearchParams({limit:String(PAGE_SIZE),offset:String(offset),review_status:reviewStatus});
      if(appliedSearch)query.set('search',appliedSearch);
      const page=await api(`/v1/review-records?${query}`,controller?{signal:controller.signal}:undefined);
      if(current!==generation.current)return;
      const key=JSON.stringify([reviewStatus,appliedSearch,offset]);
      const nextItems=page.items||[],nextTotal=page.total||0;
      if(typeof api==='function')getRecordCache(api).set(key,{items:nextItems,total:nextTotal,at:Date.now()});
      setItems(nextItems);setTotal(nextTotal);setResultKey(key);
    }catch(error:any){if(current===generation.current)setReadError(error.message||'Não foi possível carregar os registros. Tente novamente.')}
    finally{if(loadController.current===controller)loadController.current=null;if(current===generation.current)setLoading(false)}
  },[api,appliedSearch,offset,reviewStatus]);
  useEffect(()=>{if(!expanded)return;const counter=generation;const task=setTimeout(()=>load(),0);return()=>{clearTimeout(task);counter.current++;loadController.current?.abort();loadController.current=null;}},[expanded,load,refreshVersion]);
  if(selected?.provisional_id)return <ProvisionalReview key={selected.provisional_id} initialItem={selected} api={api} money={money} reviewRecord onExit={()=>setSelected(null)} onUpdated={async()=>{if(typeof api==='function')invalidateRecordReviewCache(api);await Promise.all([load(true),onUpdated()])}}/>;
  if(selected)return <TransactionEditor key={selected.id} item={selected} api={api} money={money} onCancel={()=>setSelected(null)} onSaved={async(updatedItem)=>{if(typeof api==='function')invalidateRecordReviewCache(api);if(updatedItem){if(reviewStatus==='pendente'&&updatedItem.status_revisao==='validado'){setItems(current=>current.filter(row=>row.id!==updatedItem.id));setTotal(current=>Math.max(0,current-1));}else{setItems(current=>current.map(row=>row.id===updatedItem.id?{...row,...updatedItem}:row));}}setSelected(null);await Promise.all([load(true),onUpdated()])}}/>;
  const visibleItems=resultKey===queryKey?items:(cachedPage?.items||[]),visibleTotal=resultKey===queryKey?total:(cachedPage?.total||0);
  const hasVisibleResult=resultKey===queryKey||Boolean(cachedPage);
  const applySearch=()=>{setExpanded(true);setOffset(0);setAppliedSearch(search.trim());setRefreshVersion(value=>value+1)},first=visibleTotal?offset+1:0,last=Math.min(offset+PAGE_SIZE,visibleTotal);
  return <View>
    <Text style={s.heading}>Revisar registros</Text><Text style={s.helper}>Movimentações confirmadas de PDF também aparecem aqui como validadas. Confira “Vendedor”, “Plataforma” e a classificação; você pode editar ou reabrir a revisão. Alterações são sincronizadas com Extrato, Detalhe e Dashboard.</Text>
    <View style={s.filters}>{(['pendente','validado','todos'] as const).map(status=><Pressable accessibilityRole="button" key={status} onPress={()=>{setExpanded(true);setOffset(0);setReviewStatus(status)}} style={[s.filter,reviewStatus===status&&s.filterActive]}><Text style={[s.filterText,reviewStatus===status&&s.filterTextActive]}>{status==='pendente'?'Pendentes':status==='validado'?'Validados':'Todos'}</Text></Pressable>)}</View>
    <View style={[s.searchRow,compact&&{flexDirection:'column'}]}><TextInput accessibilityLabel="Buscar descrição, categoria ou tipo" value={search} onChangeText={setSearch} onSubmitEditing={applySearch} placeholder="Buscar descrição, categoria ou tipo" placeholderTextColor="#92A4A2" style={[s.searchInput,{minWidth:0}]}/><Pressable accessibilityRole="button" onPress={applySearch} style={[s.searchButton,{minHeight:44,paddingVertical:12}]}><Text style={s.searchButtonText}>Buscar</Text></Pressable></View>
    {!expanded?(<Pressable accessibilityRole="button" accessibilityLabel="Carregar registros para revisão" onPress={()=>setExpanded(true)} style={[s.searchButton,{minHeight:44,paddingVertical:12,alignItems:'center',marginBottom:12}]}><Text style={s.searchButtonText}>Carregar registros para revisão</Text></Pressable>):(<>
    {!!readError&&<View><Text accessibilityRole="alert" style={s.helper}>{readError}{visibleItems.length?' Os registros anteriores podem estar desatualizados.':''}</Text><Pressable accessibilityRole="button" disabled={loading} onPress={()=>setRefreshVersion(value=>value+1)} style={s.searchButton}><Text style={s.searchButtonText}>Tentar carregar registros novamente</Text></Pressable></View>}
    {loading&&<ActivityIndicator color="#4FD1B5" style={s.loader}/>}{!loading&&!readError&&hasVisibleResult&&visibleItems.length===0&&<Text style={s.empty}>Nenhum registro encontrado.</Text>}
    {visibleItems.map(item=><Pressable accessibilityRole="button" key={`${item.movement_source || 'ledger'}:${item.provisional_id || item.id}`} onPress={()=>setSelected(item)} style={[s.record,compact&&{flexDirection:'column'}]}><View style={s.recordMain}><Text style={s.knownName}>{item.vendedor||item.fornecedor||(isIncoming(item)?'Origem não identificada':'Vendedor não identificado')}</Text><Text style={s.recordTitle}>{item.estabelecimento_original||item.descricao_original||'Movimentação'}</Text><Text style={s.recordMeta}>{recordCategory(item)}{item.subcategoria?` › ${item.subcategoria}`:''} · {financialDateLabel(String(item.data_evento||''))} · {item.status_revisao==='validado'?'Validado':'Pendente'}</Text></View><View style={[s.recordRight,compact&&{width:'100%',alignItems:'flex-start'}]}><SingleLineValue style={[s.recordAmount,Number(item.valor)<0?s.expense:s.income]}>{money(Number(item.valor||0))}</SingleLineValue><Text style={s.edit}>Editar ›</Text></View></Pressable>)}
    <View style={s.pagination}><Pressable accessibilityRole="button" disabled={offset===0} onPress={()=>setOffset(Math.max(0,offset-PAGE_SIZE))} style={[s.pageButton,offset===0&&s.disabled]}><Text style={s.pageText}>Anterior</Text></Pressable><Text style={s.pageStatus}>{first}–{last} de {visibleTotal}</Text><Pressable accessibilityRole="button" disabled={offset+PAGE_SIZE>=visibleTotal} onPress={()=>setOffset(offset+PAGE_SIZE)} style={[s.pageButton,offset+PAGE_SIZE>=visibleTotal&&s.disabled]}><Text style={s.pageText}>Próxima</Text></Pressable></View>
    </>)}
  </View>;
}

const s=StyleSheet.create({heading:{fontSize:18,fontWeight:'700',color:'#F4F7F5',marginTop:20},helper:{fontSize:12,lineHeight:18,color:'#92A4A2',marginTop:6,marginBottom:12},filters:{flexDirection:'row',flexWrap:'wrap',gap:8,marginBottom:12},filter:{backgroundColor:'#142326',borderRadius:10,paddingHorizontal:12,paddingVertical:9,borderWidth:1,borderColor:'#315054'},filterActive:{backgroundColor:'#49C5AA',borderColor:'#49C5AA'},filterText:{color:'#C5D8D4',fontSize:12,fontWeight:'700'},filterTextActive:{color:'#061412'},searchRow:{flexDirection:'row',gap:8,marginBottom:12},searchInput:{flex:1,backgroundColor:'#142326',borderRadius:12,paddingHorizontal:12,paddingVertical:11,color:'#F5F8F6',fontSize:14},searchButton:{backgroundColor:'#49C5AA',borderRadius:12,paddingHorizontal:14,justifyContent:'center'},searchButtonText:{color:'#061412',fontWeight:'700'},loader:{marginVertical:24},empty:{color:'#92A4A2',paddingVertical:24,textAlign:'center'},record:{flexDirection:'row',gap:12,paddingVertical:14,borderBottomWidth:1,borderColor:'#213235'},recordMain:{flex:1,minWidth:0},knownName:{color:'#4FD1B5',fontSize:14,fontWeight:'700',lineHeight:20},recordTitle:{color:'#E7EFEC',fontSize:14,fontWeight:'600',lineHeight:19},recordMeta:{color:'#92A4A2',fontSize:11,lineHeight:16,marginTop:4},recordRight:{width:112,alignItems:'flex-end',justifyContent:'center'},recordAmount:{fontSize:14,fontWeight:'700',maxWidth:'100%'},expense:{color:'#FF8C83'},income:{color:'#4FD1B5'},edit:{color:'#4FD1B5',fontSize:11,marginTop:6},pagination:{flexDirection:'row',flexWrap:'wrap',gap:10,alignItems:'center',justifyContent:'space-between',marginTop:18},pageButton:{backgroundColor:'#1A2A2D',borderWidth:1,borderColor:'#315054',borderRadius:10,paddingHorizontal:14,paddingVertical:10},pageText:{color:'#C5D8D4',fontWeight:'700'},pageStatus:{color:'#92A4A2',fontSize:12},disabled:{opacity:.4}});
