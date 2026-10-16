import { financialDateLabel } from "../utils/classification";
import {Alert} from '../utils/appAlert';
import React, { useRef, useState } from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';
import {useAsyncScope} from '../utils/useAsyncScope';

type Props = {
  item: any;
  api: (path: string, init?: RequestInit) => Promise<any>;
  onDeleted: () => void | Promise<void>;
};

export default function DeleteMovement({ item, api, onDeleted }: Props) {
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [deleted,setDeleted]=useState(false);
  const active = useRef(false);
  const source = item.provisional_id ? 'capture' : 'ledger';
  const identity = item.provisional_id || item.id;
  const path = `/v1/movements/${source}/${encodeURIComponent(identity)}`;
  const captureScope=useAsyncScope(path);
  const load = async () => {
    if (active.current) return;
    active.current = true; setBusy(true); setError('');
    const valid=captureScope();
    try { const fresh=await api(`${path}/deletion`);if(valid())setPreview(fresh); }
    catch (failure: any) { if(valid())setError(failure.message || 'Não foi possível conferir o registro.'); }
    finally { active.current = false; if(valid())setBusy(false); }
  };
  const remove = async () => {
    if (active.current || !preview?.can_delete || deleted) return;
    active.current = true; setBusy(true); setError('');
    let acknowledged=false;
    const valid=captureScope();
    try {
      const result = await api(path, { method: 'DELETE', body: JSON.stringify({ preview_token: preview.preview_token }) });
      if (!result?.deleted) throw new Error('O servidor não confirmou a exclusão.');
      acknowledged=true;if(!valid())return;setDeleted(true);setPreview(null);
      Alert.alert('Registro excluído', 'O registro saiu do extrato e dos totais. O histórico foi preservado.');
      void Promise.resolve().then(onDeleted).catch(failure=>{if(valid())setError(`Registro excluído. Não foi possível atualizar a tela: ${failure.message}`);});
    } catch (failure: any) {
      if(!valid())return;
      setError(acknowledged?`Registro excluído. Não foi possível atualizar a tela: ${failure.message}`:failure.message || 'Não foi possível excluir. O registro continua na tela.');
      // A timeout may have happened after commit. Keep the token for an idempotent retry.
      if (failure.status === 409) setPreview(null);
    } finally { active.current = false; if(valid())setBusy(false); }
  };
  const retryRefresh=async()=>{
    if(active.current)return;active.current=true;setBusy(true);
    try{await onDeleted();setError('')}catch(failure:any){setError(`Registro excluído. Não foi possível atualizar a tela: ${failure.message}`)}finally{active.current=false;setBusy(false)}
  };
  return <View style={s.box}>
    {deleted?<View><Text style={s.note}>Registro excluído. Histórico preservado.</Text><Pressable accessibilityRole="button" disabled={busy} onPress={()=>retryRefresh()} style={s.button}><Text style={s.note}>Atualizar tela</Text></Pressable></View>:!preview ? <Pressable accessibilityRole="button" disabled={busy} onPress={() => load()} style={s.button}>
      <Text style={s.danger}>{busy ? 'Conferindo registro…' : 'Excluir registro'}</Text>
    </Pressable> : <>
      <Text style={s.title}>Excluir este registro?</Text>
      {preview.summary && <Text style={s.note}>{preview.summary.name} · {Number(preview.summary.amount).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })} · {financialDateLabel(preview.summary.date)}</Text>}
      <Text style={s.note}>Ele sairá do Extrato e dos totais. O histórico e as evidências serão preservados.</Text>
      {!preview.can_delete && <Text style={s.error}>{preview.blockers.join(' ')} Revise o vínculo antes de excluir.</Text>}
      {preview.can_delete && <Pressable accessibilityRole="button" disabled={busy} onPress={() => remove()} style={[s.confirm, busy && s.disabled]}>
        <Text style={s.confirmText}>{busy ? 'Excluindo…' : 'Confirmar exclusão'}</Text>
      </Pressable>}
      <Pressable accessibilityRole="button" disabled={busy} onPress={() => { setPreview(null); setError(''); }} style={s.button}>
        <Text style={s.note}>Cancelar</Text>
      </Pressable>
    </>}
    {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
  </View>;
}

const s = StyleSheet.create({
  box: { marginVertical: 16, borderColor: '#6E4145', borderWidth: 1, borderRadius: 14, padding: 14 },
  button: { minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  danger: { color: '#FF929A', fontWeight: '700', fontSize: 16 },
  title: { color: '#F4F7F5', fontWeight: '700', fontSize: 18, marginBottom: 8 },
  note: { color: '#B5C4C2', fontSize: 14, lineHeight: 21 },
  error: { color: '#FF929A', fontSize: 14, lineHeight: 21, marginTop: 8 },
  confirm: { minHeight: 48, backgroundColor: '#9B3540', borderRadius: 10, alignItems: 'center', justifyContent: 'center', marginTop: 16 },
  confirmText: { color: '#FFFFFF', fontWeight: '700', fontSize: 16 },
  disabled: { opacity: 0.5 },
});
