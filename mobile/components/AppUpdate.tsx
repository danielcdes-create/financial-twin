import { Alert } from "../utils/appAlert";
import * as Application from 'expo-application';
import * as Linking from 'expo-linking';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {Platform, StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';

import appConfig from '../app.json';
import {useAsyncScope} from '../utils/useAsyncScope';

type Props={api:(path:string,init?:RequestInit)=>Promise<any>;baseUrl:string};

export default function AppUpdate({api,baseUrl}:Props){
  const [info,setInfo]=useState<any>(null);
  const [checking,setChecking]=useState(false);
  const generation=useRef(0), captureScope=useAsyncScope();
  const isWeb=Platform.OS==='web';
  const currentCode=isWeb?appConfig.expo.android.versionCode:Number(Application.nativeBuildVersion||0);
  const currentName=isWeb?appConfig.expo.version:Application.nativeApplicationVersion||'desconhecida';
  const check=useCallback(async()=>{const current=++generation.current,valid=captureScope();setChecking(true);try{const result=await api('/v1/app-update');if(valid()&&current===generation.current)setInfo(result)}catch(error:any){if(valid()&&current===generation.current&&error.name!=='AbortError')Alert.alert('Não foi possível verificar atualizações',error.message)}finally{if(valid()&&current===generation.current)setChecking(false)}},[api,captureScope]);
  useEffect(()=>{const task=setTimeout(()=>check(),0);return()=>clearTimeout(task)},[check]);
  const updateAvailable=Boolean(info?.available&&Number(info.version_code)>currentCode);
  const download=async()=>{try{const rawUrl=String(info?.download_url||'');const targetUrl=/^https?:\/\//i.test(rawUrl)?rawUrl:`${String(baseUrl||'').replace(/\/+$/,'')}/${rawUrl.replace(/^\/+/, '')}`;await Linking.openURL(targetUrl)}catch{Alert.alert('Não foi possível abrir o download','Confira se o Tailscale está conectado.')}};
  return <View style={s.card}>
    <Text style={s.title}>Atualizações</Text>
    <Text style={s.detail}>{isWeb?'Versão web':'Versão instalada'}: {currentName} ({currentCode})</Text>
    {info&&<Text style={[s.status,updateAvailable&&s.available]}>{!info.available?'Nenhum pacote verificado disponível':updateAvailable?`Versão ${info.version_name} disponível`:'Aplicativo atualizado'}</Text>}
    <Pressable accessibilityRole="button" onPress={updateAvailable?()=>download():()=>check()} disabled={checking} style={[s.button,checking&&s.disabled]}><Text style={s.buttonText}>{checking?'Verificando…':updateAvailable?'Baixar atualização':'Verificar novamente'}</Text></Pressable>
    {updateAvailable&&<>
      <Pressable accessibilityRole="button" accessibilityLabel="Verificar novamente" onPress={()=>check()} disabled={checking} style={[{minHeight:44,justifyContent:'center',alignItems:'center',marginTop:8},checking&&s.disabled]}><Text style={{color:'#4FD1B5',fontWeight:'700',fontSize:13}}>Verificar novamente</Text></Pressable>
      <Text style={s.help}>O navegador fará o download. Depois, toque no APK baixado e confirme a atualização do Android.</Text>
    </>}
  </View>;
}

const s=StyleSheet.create({card:{backgroundColor:'#142326',borderRadius:14,padding:15,marginTop:20},title:{fontSize:18,fontWeight:'700',color:'#F4F7F5'},detail:{fontSize:12,color:'#92A4A2',marginTop:8},status:{fontSize:13,color:'#AFC1BF',marginTop:6},available:{color:'#4FD1B5',fontWeight:'700'},button:{backgroundColor:'#49C5AA',padding:13,borderRadius:12,alignItems:'center',marginTop:12},buttonText:{color:'#061412',fontWeight:'800'},help:{fontSize:11,lineHeight:17,color:'#92A4A2',marginTop:10},disabled:{opacity:.5}});