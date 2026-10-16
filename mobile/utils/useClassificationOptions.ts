import {useCallback, useEffect, useRef, useState} from 'react';
import {useAsyncScope} from './useAsyncScope';

// Empty is a successful catalogue response; failure never erases a prior one
// or turns suggestions into selected financial fields.
export function useClassificationOptions(api:(path:string)=>Promise<any>,owner:unknown) {
  const capture=useAsyncScope(owner),generation=useRef(0);
  const [state,setState]=useState<{owner:unknown; data:Record<string,any>; loading:boolean; error:string; loaded:boolean}>({owner,data:{},loading:true,error:'',loaded:false});
  const load=useCallback(async()=>{
    const valid=capture(),current=++generation.current;
    setState(previous=>({owner,data:previous.owner===owner?previous.data:{},loaded:previous.owner===owner&&previous.loaded,loading:true,error:''}));
    try{const data=await api('/v1/transactions/classification-options');if(valid()&&current===generation.current)setState({owner,data:data||{},loaded:true,loading:false,error:''});}
    catch(error:any){if(valid()&&current===generation.current)setState(previous=>({...previous,loading:false,error:error.message||'Não foi possível carregar as sugestões.'}));}
  },[api,capture,owner]);
  useEffect(()=>{const counter=generation;void load();return()=>{counter.current++;};},[load]);
  const visible=state.owner===owner?state:{owner,data:{},loading:true,error:'',loaded:false};
  return {options:visible.data,loading:visible.loading,error:visible.error,stale:visible.loaded&&Boolean(visible.error),retry:load};
}
