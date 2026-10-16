import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {ActivityIndicator, AppState, Platform, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import * as DocumentPicker from 'expo-document-picker';
import { voiceFormData } from '../utils/voiceFile';
import { withDevicePrompt } from '../utils/devicePrompt';
import {useSQLiteContext} from '../utils/queueDb';
import {deleteVoiceDraft, durableVoiceClip, finalizeVoiceEvidence, loadVoiceDraft, markVoiceEvidenceQueued, preserveVoiceDraft, saveVoiceDraft, sealVoiceEvidence, subscribeVoiceDraftChanges, voiceDraftGeneration, type VoiceEvidenceCommand, type VoiceEvidenceStage} from '../utils/voiceDraft';
import {useAsyncScope} from '../utils/useAsyncScope';
import {Alert} from '../utils/appAlert';

type Props = {
  api: (path:string, init?:RequestInit)=>Promise<any>;
  upload: (path:string, body:FormData)=>Promise<any>;
  context: 'purchase'|'movement'|'commitment'|'document';
  onApply: (draft:any, transcript:string, command?:VoiceEvidenceCommand)=>void|Promise<void>;
  onEvidenceApply?: (draft:any, transcript:string, command?:VoiceEvidenceCommand)=>void|Promise<void>;
  disabled?: boolean;
  light?: boolean;
  collapseAfterApply?: boolean;
  onActivityChange?: (active: boolean) => void;
  draftKey?: string;
  resetRevision?: number;
};
type Clip = {uri:string; name:string; mime:string};
let microphoneOwner: object|null = null;
function VoiceButton({label,onPress,disabled=false}:{label:string;onPress:()=>void;disabled?:boolean}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={disabled} onPress={onPress} style={[s.button,disabled&&s.disabled]}><Text style={s.buttonText}>{label}</Text></Pressable>;
}

export default function VoiceCapture({api,upload,context,onApply,onEvidenceApply,disabled=false,light=false,collapseAfterApply=false,onActivityChange,draftKey,resetRevision=0}:Props) {
  const db=useSQLiteContext(), key=draftKey||context, scopeKey=`${key}:${context}:${resetRevision}`;
  const captureScope=useAsyncScope(scopeKey);
  const recorder = useAudioRecorder({...RecordingPresets.HIGH_QUALITY, numberOfChannels:1, directory:'document'});
  const status = useAudioRecorderState(recorder, 250);
  const [clip,setClip] = useState<Clip|null>(null);
  const [text,setText] = useState(''), [error,setError] = useState(''), [message,setMessage] = useState('');
  const [busy,setBusy] = useState(false), [recording,setRecording] = useState(false);
  const [appliedContext,setAppliedContext] = useState<string|null>(null);
  const [hasTranscript,setHasTranscript] = useState(false);
  const [evidence,setEvidence] = useState<VoiceEvidenceCommand|undefined>();
  const [evidenceStage,setEvidenceStage] = useState<VoiceEvidenceStage|undefined>();
  const evidenceOwner=useRef<VoiceEvidenceCommand|undefined>(undefined);
  useLayoutEffect(()=>{evidenceOwner.current=evidence;},[evidence]);
  const [draftGeneration,setDraftGeneration]=useState(()=>voiceDraftGeneration(db,key));
  const recordingGeneration=useRef(draftGeneration);
  const [restoredKey,setRestoredKey]=useState(''),[resettingOwner,setResettingOwner]=useState(''),[restoreRevision,setRestoreRevision]=useState(0);
  const resetting=resettingOwner===scopeKey;
  const restored=restoredKey===key;
  const reset=useRef(resetRevision);
  const lastAutosave=useRef<{key:string;state:string}|null>(null);
  const locked = useRef(false), live = useRef(true), isRecording = useRef(false);
  const [owner] = useState(()=>({}));
  const snapshot={clip,text,hasTranscript,appliedContext,...(evidence?{evidence,evidence_stage:evidenceStage}:{})};
  useLayoutEffect(()=>{
    const valid=captureScope();
    return subscribeVoiceDraftChanges(db,key,change=>{
      if(!valid())return;
      setDraftGeneration(change.generation);
      if(change.kind==='deleted'){
        setClip(null);setText('');setHasTranscript(false);setAppliedContext(null);setEvidence(undefined);setEvidenceStage(undefined);setError('');setMessage('Rascunho de voz excluído.');setRestoredKey(key);
      }
    });
  },[db,key,scopeKey,captureScope]);
  useEffect(()=>{
    let active=true;
    const valid=captureScope(), generation=voiceDraftGeneration(db,key);
    void loadVoiceDraft(db,key).then(saved=>{
      if(!active||!valid())return;
      if(voiceDraftGeneration(db,key)!==generation){setRestoreRevision(value=>value+1);return;}
      setDraftGeneration(generation);
      // The destination form may have been disposed. Restore the text for an
      // explicit application instead of claiming its fields are still filled.
      setClip(saved?.clip||null);setText(saved?.text||'');setHasTranscript(!!saved?.hasTranscript);setAppliedContext(null);setEvidence(saved?.evidence);setEvidenceStage(saved?.evidence_stage);setError('');setMessage(saved?.evidence?(saved.evidence_stage==='queued'?'Evidência salva. A finalização do rascunho permanece pendente; conclua a mesma tentativa.':'Esta evidência já foi preparada. Conclua a mesma tentativa para conferir o envio e finalizar o rascunho.'):'');
      setRestoredKey(key);
    }).catch((failure:any)=>{if(active&&valid())setError('Não foi possível recuperar o rascunho de voz: '+failure.message);});
    return()=>{active=false};
  },[db,key,scopeKey,restoreRevision,captureScope]);
  useEffect(()=>{
    if(!restored)return;
    const state={clip,text,hasTranscript,appliedContext,...(evidence?{evidence,evidence_stage:evidenceStage}:{})}, serialized=JSON.stringify(state);
    if(lastAutosave.current?.key===key&&lastAutosave.current.state===serialized)return;
    lastAutosave.current={key,state:serialized};
    const valid=captureScope();
    void saveVoiceDraft(db,key,state,draftGeneration).catch((failure:any)=>{if(valid()&&voiceDraftGeneration(db,key)===draftGeneration)setError('Não foi possível salvar o rascunho de voz: '+failure.message);});
  },[appliedContext,clip,db,draftGeneration,evidence,evidenceStage,hasTranscript,key,restored,text,captureScope]);
  useEffect(()=>{
    if(reset.current===resetRevision)return;reset.current=resetRevision;
    if(evidence)return;
    const valid=captureScope();
    void preserveVoiceDraft(db,key,{clip,text,hasTranscript,appliedContext},draftGeneration,()=>{if(valid())setResettingOwner(scopeKey);}).then(()=>{
      return saveVoiceDraft(db,key,{clip:null,text:'',hasTranscript:false,appliedContext:null},draftGeneration);
    }).then(()=>{
      if(valid()&&voiceDraftGeneration(db,key)===draftGeneration){setClip(null);setText('');setHasTranscript(false);setAppliedContext(null);}
    }).catch((failure:any)=>{if(valid())setError('A captura foi salva. O rascunho de voz foi mantido: '+failure.message);}).finally(()=>{if(valid())setResettingOwner('');});
  },[appliedContext,clip,db,draftGeneration,evidence,hasTranscript,key,resetRevision,text,captureScope,scopeKey]);
  useEffect(()=>{onActivityChange?.(busy||recording);},[busy,recording,onActivityChange]);
  const stop = useCallback(async () => {
    if (!isRecording.current || locked.current) return;
    const scopeValid=captureScope(), generation=recordingGeneration.current;
    const valid=()=>scopeValid()&&voiceDraftGeneration(db,key)===generation;
    locked.current=true;
    // This task owns stopping and durable finalization. Cleanup must not stop
    // the same native recorder a second time while its first stop is pending.
    isRecording.current=false;
    try {
      await recorder.stop();
      if (!recorder.uri) throw new Error('Nenhum áudio foi gerado. Grave novamente.');
      const savedClip=await durableVoiceClip({uri:recorder.uri,name:Platform.OS==='web'?'capture.webm':'capture.m4a',mime:Platform.OS==='web'?'audio/webm':'audio/mp4'});
      await saveVoiceDraft(db,key,{clip:savedClip,text:'',hasTranscript:false,appliedContext:null},generation);
      if(valid()){setRecording(false);setClip(savedClip);setText('');setHasTranscript(false);setAppliedContext(null);setMessage('Áudio pronto. Toque em Transcrever para enviar à xAI.');}
    } catch(e:any) {if(valid()) {setError(e.message);setRecording(false);} isRecording.current=false;}
    finally {await setAudioModeAsync({allowsRecording:false}).catch(()=>undefined);if(microphoneOwner===owner)microphoneOwner=null;locked.current=false;}
  },[db,key,recorder,owner,captureScope]);
  useEffect(()=>{
    live.current=true;
    const subscription=AppState.addEventListener('change', state=>{if(state!=='active')void stop();});
    return ()=>{live.current=false;subscription.remove();if(isRecording.current){isRecording.current=false;const generation=recordingGeneration.current;void recorder.stop().then(async()=>{
      if(recorder.uri){const savedClip=await durableVoiceClip({uri:recorder.uri,name:Platform.OS==='web'?'capture.webm':'capture.m4a',mime:Platform.OS==='web'?'audio/webm':'audio/mp4'});await saveVoiceDraft(db,key,{clip:savedClip,text:'',hasTranscript:false,appliedContext:null},generation);}
    }).catch(()=>undefined).finally(async()=>{await setAudioModeAsync({allowsRecording:false}).catch(()=>undefined);if(microphoneOwner===owner)microphoneOwner=null;});}};
  },[db,key,recorder,stop,owner]);
  useEffect(()=>{if(!recording)return;const timer=setTimeout(()=>stop(),120000);return()=>clearTimeout(timer);},[recording,stop]);
  const run=async(task:(valid:()=>boolean)=>Promise<void>,allowEvidence=false)=>{
    if(locked.current||evidenceOwner.current&&!allowEvidence)return;
    const scopeValid=captureScope();
    const valid=()=>scopeValid()&&voiceDraftGeneration(db,key)===draftGeneration;
    if(!valid())return;
    locked.current=true;setBusy(true);setError('');setMessage('');
    try{await task(valid)}catch(e:any){if(valid())setError(e.message||'Não foi possível concluir. Tente novamente.');}
    finally{
      if(!isRecording.current&&microphoneOwner===owner){await setAudioModeAsync({allowsRecording:false}).catch(()=>undefined);microphoneOwner=null;}
      locked.current=false;if(live.current)setBusy(false);
    }
  };
  const start=()=>run(async(valid)=>{
    if(microphoneOwner)throw new Error('Pare a outra gravação antes de iniciar uma nova.');
    microphoneOwner=owner;
    if(Platform.OS==='web'&&!globalThis.isSecureContext)throw new Error('O microfone precisa de HTTPS ou localhost. Você também pode escolher um áudio.');
    const permission=await withDevicePrompt(() => AudioModule.requestRecordingPermissionsAsync());
    if(!permission.granted)throw new Error('Permita o microfone nas configurações para gravar, ou escolha um áudio.');
    if(!valid())return;
    await preserveVoiceDraft(db,key,snapshot,draftGeneration);
    if(!valid())return;
    await setAudioModeAsync({allowsRecording:true,playsInSilentMode:true,allowsBackgroundRecording:false});
    await recorder.prepareToRecordAsync();
    if(!valid()||AppState.currentState==='background'){await recorder.stop().catch(()=>undefined);return;}
    recordingGeneration.current=draftGeneration;recorder.record();isRecording.current=true;setRecording(true);setClip(null);setText('');setHasTranscript(false);setAppliedContext(null);
  });
  const choose=()=>run(async(valid)=>{
    const result=await withDevicePrompt(() => DocumentPicker.getDocumentAsync({type:['audio/*','video/webm','video/mp4'],copyToCacheDirectory:true}));
    if(result.canceled||!valid())return;
    const item=result.assets[0];
    if(item.size&&item.size>20*1024*1024)throw new Error('Escolha um áudio de até 20 MiB.');
    await preserveVoiceDraft(db,key,snapshot,draftGeneration);
    if(!valid())return;
    const savedClip=await durableVoiceClip({uri:item.uri,name:item.name,mime:item.mimeType||'audio/mp4'});
    if(valid()){setClip(savedClip);setText('');setHasTranscript(false);setAppliedContext(null);}
    await saveVoiceDraft(db,key,{clip:savedClip,text:'',hasTranscript:false,appliedContext:null},draftGeneration);
    if(!valid())return;
  });
  const transcribe=()=>run(async(valid)=>{
    if(!clip)return;
    const response=await upload('/v1/capture/transcribe',await voiceFormData(clip.uri,clip.name,clip.mime));
    if(typeof response?.text!=='string'||response.persisted===true)throw new Error('A resposta da transcrição não foi reconhecida. O áudio permanece salvo para tentar novamente.');
    if(valid()){setText(response.text);setHasTranscript(true);setMessage('Confira e edite a transcrição antes de usar.');}
  });
  const apply=()=>run(async(valid)=>{
    const transcript=text.trim();if(!transcript)return;
    if(context==='document'||evidenceOwner.current){
      const uiValid=captureScope();
      const command=await sealVoiceEvidence(db,key,snapshot,draftGeneration);
      if(uiValid()){setEvidence(command);setEvidenceStage(evidenceStage||'prepared');}
      await (onEvidenceApply||onApply)({},command.text,command);
      try {
        await markVoiceEvidenceQueued(db,key,command);
        if(uiValid())setEvidenceStage('queued');
        await finalizeVoiceEvidence(db,key,command);
        if(uiValid()){setEvidence(undefined);setEvidenceStage(undefined);setText('');setHasTranscript(false);setClip(null);setMessage('Evidência salva na fila para revisão.');}
      } catch {
        if(uiValid()){setEvidence(command);setMessage('Evidência salva. A finalização do rascunho permanece pendente; conclua a mesma tentativa.');setError('Não foi possível limpar o rascunho enviado. A evidência conserva a mesma identificação.');}
      }
      return;
    }
    const result=await api('/v1/capture/interpret',{method:'POST',body:JSON.stringify({context,text:transcript})});
    if(!result?.draft||typeof result.draft!=='object'||Array.isArray(result.draft)||result.persisted===true)throw new Error('A resposta de preenchimento não foi reconhecida. A transcrição permanece salva para conferir.');
    if(!valid())return;
    await onApply(result.draft,transcript);
    if(valid())setAppliedContext(context);
    if(valid())setMessage('Campos preenchidos. Confira os valores e complete os dados antes de salvar.');
  },true);
  const blocked=busy||disabled||!restored||resetting;
  const editNewEvidence=()=>run(async()=>{
    if(!evidence)return;
    const uiValid=captureScope(),command=await sealVoiceEvidence(db,key,snapshot,draftGeneration);
    if(uiValid())setEvidence(command);
    await (onEvidenceApply||onApply)({},command.text,command);
    await markVoiceEvidenceQueued(db,key,command);
    if(uiValid())setEvidenceStage('queued');
    await preserveVoiceDraft(db,key,{...snapshot,evidence:command,evidence_stage:'queued'},draftGeneration);
    await finalizeVoiceEvidence(db,key,command);
    const next={clip:snapshot.clip,text:command.text,hasTranscript:true,appliedContext:null};
    if(uiValid()){setEvidence(undefined);setEvidenceStage(undefined);setClip(next.clip);setText(next.text);setHasTranscript(true);setAppliedContext(null);setMessage('Novo preenchimento de voz. A evidência anterior continua preservada na fila ou em Revisão.');}
    try{await saveVoiceDraft(db,key,next,voiceDraftGeneration(db,key));}
    catch(error){if(uiValid())setError('A evidência anterior foi salva. O novo preenchimento ainda não pôde ser guardado; mantenha esta tela aberta e tente salvar novamente.');throw error;}
  },true);
  const hasDraft=!!clip||!!text.trim();
  const removeDraft=()=>{
    if(blocked||recording||locked.current||!hasDraft)return;
    const valid=captureScope(), generation=draftGeneration;
    Alert.alert('Excluir rascunho de voz?','O áudio e a transcrição deste rascunho serão descartados. Os campos já preenchidos no formulário continuam disponíveis.',[
      {text:'Cancelar',style:'cancel'},
      {text:'Excluir rascunho',style:'destructive',onPress:()=>{
        if(!valid()||voiceDraftGeneration(db,key)!==generation||locked.current||disabled||isRecording.current)return;
        return run(async()=>{
          try{await deleteVoiceDraft(db,key);}
          catch(failure:any){if(valid())setError('Não foi possível excluir o rascunho de voz: '+failure.message);}
        },true);
      }},
    ]);
  };
  if(collapseAfterApply&&appliedContext===context) return <View style={[s.box,light&&s.light]}>
    <Text style={[s.title,light&&s.darkText]}>Voz aplicada à movimentação</Text>
    <Text style={[s.note,light&&s.darkText]}>Confira os campos abaixo antes de salvar. A transcrição continua disponível para editar.</Text>
    <VoiceButton label="Editar transcrição" onPress={()=>setAppliedContext(null)} disabled={blocked}/>
    <VoiceButton label="Excluir rascunho de voz" onPress={removeDraft} disabled={blocked||recording}/>
  </View>;
  return <View style={[s.box,light&&s.light]}>
    <Text style={[s.title,light&&s.darkText]}>Capturar por voz</Text>
    <Text style={[s.note,light&&s.darkText]}>Descreva {context==='purchase'?'o produto, preço total e parcelas':context==='commitment'?'o compromisso, valor por cobrança e datas':context==='document'?'os detalhes do comprovante':'a movimentação e o valor'}. Você revisa o texto antes de usar.</Text>
    {recording?<><Text style={[s.note,light&&s.darkText]}>● Gravando · {Math.floor(status.durationMillis/1000)} s (máximo 2 min)</Text><VoiceButton label="Parar gravação" onPress={()=>stop()}/></>:<><VoiceButton label="Gravar voz" onPress={start} disabled={blocked||!!evidence}/><VoiceButton label="Escolher áudio" onPress={choose} disabled={blocked||!!evidence}/></>}
    {clip&&!recording&&<><Text style={[s.note,light&&s.darkText]}>{clip.name} · áudio e transcrição ficam salvos neste aparelho para retomar</Text><VoiceButton label={busy?'Processando…':'Transcrever com xAI'} onPress={transcribe} disabled={blocked||!!evidence}/></>}
    {busy&&<ActivityIndicator color="#49C5AA"/>}
    <Text style={[s.note,light&&s.darkText]}>O áudio só é enviado à xAI ao tocar em Transcrever.</Text>
    {!!error&&<Text accessibilityRole="alert" style={s.error}>{error}</Text>}
    {!restored&&!!error&&<VoiceButton label="Recuperar rascunho de voz" onPress={()=>setRestoreRevision(value=>value+1)} disabled={busy||recording}/>} 
    {hasTranscript&&<><Text style={[s.note,light&&s.darkText]}>{evidence?'Transcrição desta evidência preparada':'Transcrição (editável)'}</Text><TextInput accessibilityLabel="Transcrição da voz" multiline value={text} editable={!blocked&&!recording&&!evidence} onChangeText={setText} maxLength={12000} style={[s.input,light&&s.lightInput]}/><VoiceButton label={evidence?'Concluir envio da evidência':context==='document'?'Salvar texto como evidência':'Preencher campos com a voz'} onPress={apply} disabled={blocked||recording||!text.trim()}/></>}
    {!!evidence&&<><Text style={[s.note,light&&s.darkText]}>Para registrar outra ocorrência, inicie um novo preenchimento. A evidência anterior permanece preservada.</Text><VoiceButton label="Editar nova captura" onPress={editNewEvidence} disabled={blocked||recording}/></>}
    {hasDraft&&<VoiceButton label="Excluir rascunho de voz" onPress={removeDraft} disabled={blocked||recording}/>}
    {!!message&&<Text style={[s.note,light&&s.darkText]}>{message}</Text>}
  </View>;
}
const s=StyleSheet.create({box:{backgroundColor:'#142326',borderColor:'#315054',borderWidth:1,borderRadius:12,padding:12,gap:9,marginVertical:10},light:{backgroundColor:'#F1F7F6',borderColor:'#C5D8D4'},title:{fontSize:16,fontWeight:'700',color:'#F4F7F5'},note:{fontSize:12,lineHeight:18,color:'#B6C8C4'},darkText:{color:'#25494C'},button:{backgroundColor:'#136D67',borderRadius:10,padding:12,alignItems:'center'},buttonText:{color:'#FFF',fontWeight:'700'},disabled:{opacity:.5},input:{backgroundColor:'#18292C',color:'#FFF',borderRadius:8,padding:12,minHeight:85,textAlignVertical:'top'},lightInput:{backgroundColor:'#FFF',color:'#173F43',borderWidth:1,borderColor:'#B8CDCE'},error:{color:'#E25454',fontSize:13}});
