import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Text, View} from 'react-native';
import {Pressable} from './InteractionControls';
import type {MutationCommand} from '../utils/mutationJournal';
import {useAsyncScope} from '../utils/useAsyncScope';
type Props={client:{pendingMutations:()=>Promise<MutationCommand[]>;resumeMutation:(command:MutationCommand)=>Promise<any>;subscribeMutations:(listener:()=>void)=>()=>void};onUpdated:()=>Promise<void>;revision:number;enabled:boolean};
export default function PendingMutations({client,onUpdated,revision,enabled}:Props){
  const [commands,setCommands]=useState<MutationCommand[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const active=useRef(false),generation=useRef(0);
  const captureScope=useAsyncScope();
  const load=useCallback(async()=>{const current=++generation.current,valid=captureScope();try{const rows=await client.pendingMutations();if(valid()&&current===generation.current)setCommands(rows)}catch(failure:any){if(valid()&&current===generation.current)setError(failure.message)}},[client,captureScope]);
  useEffect(()=>{if(!enabled)return;const counter=generation;const task=setTimeout(()=>load(),0);return()=>{clearTimeout(task);counter.current++}},[load,revision,enabled]);
  useEffect(()=>{if(!enabled)return;return client.subscribeMutations(()=>load());},[client,load,enabled]);
  const resume=async()=>{if(active.current||!enabled)return;const valid=captureScope();active.current=true;setBusy(true);setError('');let confirmed=0;try{for(const command of commands){if(!valid())break;await client.resumeMutation(command);confirmed++;}if(!valid())return;await load();void Promise.resolve().then(onUpdated).catch(failure=>{if(valid())setError(`As gravações confirmadas foram preservadas. Não foi possível atualizar o resumo: ${failure.message}`)})}catch(failure:any){if(valid()){setError(`${confirmed?'As gravações confirmadas foram preservadas. ':''}${failure.message}`);await load()}}finally{active.current=false;if(valid())setBusy(false)}};
  if(!enabled||(!commands.length&&!error))return null;
  return <View accessibilityRole="alert" style={{backgroundColor:'#30251A',padding:14,borderRadius:12,margin:10,gap:8}}>
    {!!commands.length&&<><Text style={{color:'#F9C978',fontSize:14}}>Há {commands.length} gravação(ões) sem confirmação local. A conexão ou o app pode ter sido interrompido. Seus dados foram preservados.</Text><Pressable accessibilityRole="button" disabled={busy} onPress={()=>resume()} style={{minHeight:48,justifyContent:'center'}}><Text style={{color:'#67DFC3',fontWeight:'700'}}>{busy?'Conferindo tentativas…':'Concluir tentativas pendentes'}</Text></Pressable></>}
    {!!error&&<Text style={{color:'#F9C978',fontSize:13}}>{error}</Text>}
  </View>;
}
