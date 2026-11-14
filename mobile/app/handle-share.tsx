import React, { useEffect } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';
import { useIncomingShare } from 'expo-sharing';
import App from '../App';

export default function HandleShare() {
  const {resolvedSharedPayloads,isResolving,error} = useIncomingShare();
  useEffect(() => {
    if (!isResolving) {
      (globalThis as any).__financialTwinPendingShare = false;
    }
  }, [isResolving]);
  if (isResolving) return <View style={{flex:1,justifyContent:'center',alignItems:'center'}}><ActivityIndicator/><Text>Lendo imagem compartilhada…</Text></View>;
  const image=resolvedSharedPayloads.find(item=>item.contentType==='image'&&!!item.contentUri);
  const shareSeq = Number((globalThis as any).__financialTwinShareSeq) || 0;
  const shareKey = `${shareSeq}:${image?.contentUri||error?.message||'empty-share'}:${image?.contentSize??''}:${image?.originalName??''}`;
  return <App key={shareKey} navigationRoute="/handle-share" initialPurchaseImage={image?.contentUri?{uri:image.contentUri,mimeType:image.contentMimeType,fileName:image.originalName,fileSize:image.contentSize}:null}
    initialPurchaseError={error?.message||(image?undefined:'Compartilhe um print de produto em PNG ou JPEG, ou escolha um print da galeria.')}/>;
}
