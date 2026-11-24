import { Alert } from "../utils/appAlert";
import * as Application from 'expo-application';
import * as FileSystem from 'expo-file-system';
import * as Linking from 'expo-linking';
import NotificationCapture from 'expo-notification-capture';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {Platform, StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';

import appConfig from '../app.json';
import {requirePrivateTransport} from '../utils/authState';
import {bytesToHex, createSha256Hasher, sha256Bytes} from '../utils/biometricSession';
import {withDevicePrompt} from '../utils/devicePrompt';
import {useAsyncScope} from '../utils/useAsyncScope';

const APK_MIME='application/vnd.android.package-archive';

type Props={api:(path:string,init?:RequestInit)=>Promise<any>;baseUrl:string};

const urlOrigin=(value:string)=>{
  const match=/^(https?:\/\/[^/?#]+)/i.exec(String(value||'').trim());
  return match?match[1].toLowerCase():'';
};
const assertPrivateUrl=(value:string)=>{
  if(typeof URL==='function'){
    requirePrivateTransport(value);
    return;
  }
  if(!/^https:\/\/|^http:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(value)){
    throw new Error('Para proteger sua senha e seus dados, a conexão precisa usar HTTPS.');
  }
};

export default function AppUpdate({api,baseUrl}:Props){
  const [info,setInfo]=useState<any>(null);
  const [checking,setChecking]=useState(false);
  const [downloading,setDownloading]=useState(false);
  const generation=useRef(0), captureScope=useAsyncScope();
  const isWeb=Platform.OS==='web';
  const currentCode=isWeb?appConfig.expo.android.versionCode:Number(Application.nativeBuildVersion||0);
  const currentName=isWeb?appConfig.expo.version:Application.nativeApplicationVersion||'desconhecida';
  const check=useCallback(async()=>{const current=++generation.current,valid=captureScope();setChecking(true);try{const result=await api('/v1/app-update');if(valid()&&current===generation.current)setInfo(result)}catch(error:any){if(valid()&&current===generation.current&&error.name!=='AbortError')Alert.alert('Não foi possível verificar atualizações',error.message)}finally{if(valid()&&current===generation.current)setChecking(false)}},[api,captureScope]);
  useEffect(()=>{const task=setTimeout(()=>check(),0);return()=>clearTimeout(task)},[check]);
  const updateAvailable=Boolean(info?.available&&Number(info.version_code)>currentCode);
  const download=async()=>{
    const valid=captureScope();
    setDownloading(true);
    try{
      const fresh=await api('/v1/app-update');
      if(valid())setInfo(fresh);
      if(!fresh?.available||Number(fresh.version_code)<=currentCode){
        throw new Error('Nenhuma atualização mais recente disponível no momento.');
      }
      const expectedHash=String(fresh.sha256||'').trim().toUpperCase();
      const expectedSize=Number(fresh.size_bytes||0);
      if(!/^[0-9A-F]{64}$/.test(expectedHash)||!Number.isFinite(expectedSize)||expectedSize<=0){
        throw new Error('Manifesto de atualização incompleto (SHA-256/tamanho ausentes).');
      }
      const cleanBase=String(baseUrl||'').replace(/\/+$/,'');
      assertPrivateUrl(cleanBase);
      const rawUrl=String(fresh.download_url||'');
      if(!rawUrl)throw new Error('Endereço do pacote ausente.');
      const targetUrl=/^https?:\/\//i.test(rawUrl)?rawUrl:`${cleanBase}/${rawUrl.replace(/^\/+/, '')}`;
      assertPrivateUrl(targetUrl);
      if(urlOrigin(targetUrl)!==urlOrigin(cleanBase)){
        throw new Error('Origem de download divergente bloqueada por segurança.');
      }
      if(isWeb){
        await Linking.openURL(targetUrl);
        return;
      }
      const FileCtor=(FileSystem as any)?.File;
      const cacheDir=(FileSystem as any)?.Paths?.cache;
      if(typeof FileCtor!=='function'||typeof FileCtor?.downloadFileAsync!=='function'||!cacheDir){
        throw new Error('Armazenamento temporário indisponível para validar o APK com segurança.');
      }
      const staged=new FileCtor(cacheDir,`financial-twin-${Number(fresh.version_code)}.apk`);
      try{if(staged.exists)staged.delete();}catch{/* ignore stale cache */}
      const downloaded=await FileCtor.downloadFileAsync(targetUrl,staged,{idempotent:true});
      let actualSize=0;
      let actualHash='';
      if(typeof downloaded?.open==='function'){
        const hasher=createSha256Hasher();
        const handle=downloaded.open((FileSystem as any)?.FileMode?.ReadOnly??'r');
        try{
          while(true){
            const chunk:Uint8Array=await Promise.resolve(handle.readBytes(65536));
            if(!chunk||chunk.byteLength===0)break;
            actualSize+=chunk.byteLength;
            if(actualSize>expectedSize)break;
            hasher.update(chunk);
          }
        }finally{
          try{handle?.close?.();}catch{/* ignore close error */}
        }
        if(actualSize===expectedSize){
          actualHash=bytesToHex(hasher.digest()).toUpperCase();
        }
      }else{
        const bytes:Uint8Array=await downloaded.bytes();
        actualSize=bytes.byteLength;
        actualHash=bytesToHex(sha256Bytes(bytes)).toUpperCase();
      }
      if(actualSize!==expectedSize||actualHash!==expectedHash){
        try{if(downloaded?.exists!==false)downloaded?.delete?.();}catch{/* discard corrupt candidate */}
        throw new Error('A verificação SHA-256 do APK falhou. O arquivo foi descartado.');
      }
      const rawContentUri=
        (typeof downloaded?.contentUri==='string'&&downloaded.contentUri)||
        (typeof (FileSystem as any)?.getContentUriAsync==='function'&&downloaded?.uri
          ?await (FileSystem as any).getContentUriAsync(downloaded.uri)
          :'');
      const contentUri=String(rawContentUri||'').trim();
      if(!contentUri.startsWith('content://')){
        try{if(downloaded?.exists!==false)downloaded?.delete?.();}catch{/* cleanup unsharable staging */}
        throw new Error('URI segura (content://) indisponível para iniciar o instalador Android.');
      }
      const installApk=NotificationCapture?.installApk;
      if(typeof installApk!=='function'){
        throw new Error('Instalador de pacotes do Android indisponível neste dispositivo.');
      }
      await withDevicePrompt(()=>Promise.resolve(installApk(contentUri,APK_MIME)));
    }catch(error:any){
      if(valid()&&error?.name!=='AbortError'){
        Alert.alert('Não foi possível abrir o download',error?.message||'Confira se o Tailscale está conectado.');
      }
    }finally{
      if(valid())setDownloading(false);
    }
  };
  const busy=checking||downloading;
  return <View style={s.card}>
    <Text style={s.title}>Atualizações</Text>
    <Text style={s.detail}>{isWeb?'Versão web':'Versão instalada'}: {currentName} ({currentCode})</Text>
    {info&&<Text style={[s.status,updateAvailable&&s.available]}>{!info.available?'Nenhum pacote verificado disponível':updateAvailable?`Versão ${info.version_name} disponível`:'Aplicativo atualizado'}</Text>}
    <Pressable accessibilityRole="button" onPress={updateAvailable?()=>download():()=>check()} disabled={busy} style={[s.button,busy&&s.disabled]}><Text style={s.buttonText}>{checking?'Verificando…':downloading?'Validando pacote…':updateAvailable?'Baixar atualização':'Verificar novamente'}</Text></Pressable>
    {updateAvailable&&<>
      <Pressable accessibilityRole="button" accessibilityLabel="Verificar novamente" onPress={()=>check()} disabled={busy} style={[{minHeight:44,justifyContent:'center',alignItems:'center',marginTop:8},busy&&s.disabled]}><Text style={{color:'#4FD1B5',fontWeight:'700',fontSize:13}}>Verificar novamente</Text></Pressable>
      <Text style={s.help}>O pacote é assinado e conferido por SHA-256 antes da instalação no Android.</Text>
    </>}
  </View>;
}

const s=StyleSheet.create({card:{backgroundColor:'#142326',borderRadius:14,padding:15,marginTop:20},title:{fontSize:18,fontWeight:'700',color:'#F4F7F5'},detail:{fontSize:12,color:'#92A4A2',marginTop:8},status:{fontSize:13,color:'#AFC1BF',marginTop:6},available:{color:'#4FD1B5',fontWeight:'700'},button:{backgroundColor:'#49C5AA',padding:13,borderRadius:12,alignItems:'center',marginTop:12},buttonText:{color:'#061412',fontWeight:'800'},help:{fontSize:11,lineHeight:17,color:'#92A4A2',marginTop:10},disabled:{opacity:.5}});
