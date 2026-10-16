import React, {useRef, useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {Alert} from '../utils/appAlert';
import {useAsyncScope} from '../utils/useAsyncScope';
import {protectUnsavedBack, useLocalBack} from '../utils/localBack';

const newId=()=> 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.random()*16|0;return(c==='x'?r:r&3|8).toString(16)});

export default function InstallmentLabelEditor({identity,name,api,onUpdated}:{identity:string;name:string;api:(path:string,init?:RequestInit)=>Promise<any>;onUpdated:()=>void|Promise<void>}){
  const path=`/v1/future-commitments/installment/${encodeURIComponent(identity)}`;
  const [opened,setOpened]=useState(false),[draft,setDraft]=useState(name),[base,setBase]=useState(name),[token,setToken]=useState(''),[busy,setBusy]=useState(false);
  const lock=useRef(false),captureScope=useAsyncScope(path);
  const back=()=>protectUnsavedBack(opened&&draft!==base,()=>setOpened(false));
  useLocalBack(back,opened,35);
  const open=async()=>{
    if(lock.current)return;lock.current=true;setBusy(true);const valid=captureScope();
    try{const fresh=await api(`${path}?refresh=${Date.now()}`);if(!valid())return;setDraft(fresh.name||name);setBase(fresh.name||name);setToken(fresh.preview_token);setOpened(true);}
    catch(error:any){if(valid())Alert.alert('Não foi possível abrir',error.message||'Confira a conexão e tente novamente.');}
    finally{lock.current=false;if(valid())setBusy(false)}
  };
  const save=async()=>{
    const value=draft.trim();if(!value||value.length>160){Alert.alert('Confira o nome','Informe um nome com até 160 caracteres.');return;}
    if(lock.current||!token||!captureScope()())return;lock.current=true;setBusy(true);const valid=captureScope();let acknowledged=false;
    try{const result=await api(`${path}/label`,{method:'PATCH',body:JSON.stringify({request_id:newId(),preview_token:token,name:value})});if(result.persisted!==true||result.identity!==identity)throw new Error('O servidor não confirmou o nome do parcelamento.');acknowledged=true;if(!valid())return;setBase(value);setDraft(value);setOpened(false);Alert.alert('Nome atualizado','O nome aparece no detalhe da movimentação vinculada, na projeção mensal e em Compromissos.');void Promise.resolve().then(onUpdated).catch(error=>{if(valid())Alert.alert('Alteração salva',`O nome foi salvo; a lista não atualizou: ${error.message}`)});}
    catch(error:any){if(valid())Alert.alert(acknowledged?'Alteração salva':'Não foi possível salvar',error.message||'Tente novamente.');}
    finally{lock.current=false;if(valid())setBusy(false)}
  };
  return <View style={s.box}>
    {!opened?<Pressable accessibilityRole="button" disabled={busy} onPress={open}><Text style={s.action}>{busy?'Carregando…':'Editar nome do parcelamento ›'}</Text></Pressable>:<>
      <Text style={s.label}>Nome compartilhado com a movimentação vinculada</Text>
      <TextInput accessibilityLabel="Nome do parcelamento" editable={!busy} value={draft} onChangeText={setDraft} maxLength={160} style={s.input}/>
      <View style={s.actions}><Pressable accessibilityRole="button" disabled={busy} onPress={save} style={s.button}><Text style={s.buttonText}>{busy?'Salvando…':'Salvar nome'}</Text></Pressable><Pressable accessibilityRole="button" disabled={busy} onPress={back} style={s.cancel}><Text style={s.cancelText}>Cancelar</Text></Pressable></View>
    </>}
  </View>;
}

const s=StyleSheet.create({box:{marginTop:8},action:{color:'#4FD1B5',fontSize:12,fontWeight:'700',paddingVertical:8},label:{color:'#AFC1BF',fontSize:11,marginBottom:5},input:{backgroundColor:'#18292C',borderColor:'#315054',borderWidth:1,borderRadius:9,paddingHorizontal:11,paddingVertical:10,color:'#F5F8F6'},actions:{flexDirection:'row',gap:8,flexWrap:'wrap',marginTop:7,alignItems:'center'},button:{backgroundColor:'#49C5AA',borderRadius:8,paddingHorizontal:12,paddingVertical:10},buttonText:{color:'#061412',fontWeight:'800',fontSize:12},cancel:{padding:8},cancelText:{color:'#C5D8D4',fontSize:12}});
