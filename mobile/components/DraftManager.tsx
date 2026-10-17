import React, {useCallback, useEffect, useRef, useState} from 'react';
import {ActivityIndicator, StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';
import {Alert} from '../utils/appAlert';
import {useSQLiteContext} from '../utils/queueDb';
import {useAsyncScope} from '../utils/useAsyncScope';
import {deleteLocalDraft, listLocalDrafts, type LocalDraft} from '../utils/localDrafts';

export default function DraftManager({onBack, onPendingCreation}: {onBack: () => void; onPendingCreation: () => void}) {
  const db = useSQLiteContext(), captureScope = useAsyncScope();
  const [drafts, setDrafts] = useState<LocalDraft[]>([]), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const running = useRef(false), read = useRef(0);
  const readDrafts = useCallback(() => {
    const valid = captureScope(), generation = ++read.current;
    return listLocalDrafts(db)
      .then(rows => {if (valid() && generation === read.current) setDrafts(rows);})
      .catch((failure: any) => {if (valid() && generation === read.current) setError(failure.message || 'Não foi possível ler os rascunhos.');})
      .finally(() => {if (valid() && generation === read.current) setLoading(false);});
  }, [captureScope, db]);
  const load = () => {setLoading(true); setError(''); return readDrafts();};
  useEffect(() => {void readDrafts();}, [readDrafts]);
  const remove = async (draft: LocalDraft) => {
    const valid = captureScope();
    if (!valid() || running.current) return;
    running.current = true; read.current++; setBusy(true); setLoading(false); setError(''); setNotice('');
    try {
      await deleteLocalDraft(db, draft);
      if (!valid()) return;
      setDrafts(current => current.filter(row => row.kind !== draft.kind || row.id !== draft.id));
      setNotice('Rascunho excluído.');
    } catch (failure: any) {if (valid()) setError(failure.message || 'Não foi possível excluir. O rascunho foi mantido.');}
    finally {running.current = false; if (valid()) setBusy(false);}
  };
  const confirm = (draft: LocalDraft) => {
    const valid = captureScope();
    Alert.alert('Excluir rascunho?', `As edições locais de “${draft.title}” serão excluídas. Capturas, arquivos originais e movimentações salvas permanecem disponíveis.`,
      [{text: 'Cancelar', style: 'cancel'}, {text: 'Excluir rascunho', style: 'destructive', onPress: () => {if (valid()) return remove(draft);}}]);
  };
  const deletableDrafts = drafts.filter(d => !d.pendingCreation);
  const removeAll = async () => {
    const valid = captureScope();
    if (!valid() || running.current || !deletableDrafts.length) return;
    running.current = true; read.current++; setBusy(true); setLoading(false); setError(''); setNotice('');
    try {
      for (const draft of deletableDrafts) {
        await deleteLocalDraft(db, draft);
        if (!valid()) return;
      }
      setDrafts(current => current.filter(row => row.pendingCreation));
      setNotice('Todos os rascunhos disponíveis foram excluídos.');
    } catch (failure: any) {if (valid()) setError(failure.message || 'Não foi possível excluir todos os rascunhos.');}
    finally {running.current = false; if (valid()) setBusy(false);}
  };
  const confirmAll = () => {
    const valid = captureScope();
    Alert.alert('Excluir todos os rascunhos?', `${deletableDrafts.length} rascunho(s) locais serão excluídos deste aparelho. Cadastros pendentes de confirmação e lançamentos já salvos serão preservados.`,
      [{text: 'Cancelar', style: 'cancel'}, {text: 'Excluir todos', style: 'destructive', onPress: () => {if (valid()) return removeAll();}}]);
  };
  return <View style={s.panel}>
    <Pressable accessibilityRole="button" disabled={busy} onPress={onBack} style={s.action}><Text style={s.link}>‹ Voltar a Mais</Text></Pressable>
    <Text accessibilityRole="header" style={s.heading}>Rascunhos</Text>
    <Text style={s.note}>Digitação, revisões, voz e cópias preservadas neste aparelho. Você pode excluir cada rascunho para recomeçar.</Text>
    <Pressable accessibilityRole="button" disabled={busy || loading} onPress={load} style={s.action}><Text style={s.link}>Atualizar rascunhos</Text></Pressable>
    {deletableDrafts.length > 1 && <Pressable accessibilityRole="button" disabled={busy || loading} onPress={confirmAll} style={s.action}><Text style={s.danger}>Limpar todos os rascunhos ({deletableDrafts.length})</Text></Pressable>}
    {(loading || busy) && <ActivityIndicator color="#4FD1B5" />}
    {!!error && <Text accessibilityRole="alert" style={s.warning}>{error}</Text>}
    {!!notice && <Text accessibilityRole="alert" style={s.link}>{notice}</Text>}
    {!loading && !drafts.length && !error && <Text style={s.note}>Nenhum rascunho salvo neste aparelho.</Text>}
    {drafts.map(draft => <View key={`${draft.kind}:${draft.id}`} style={s.card}>
      <Text style={s.title}>{draft.title}</Text><Text style={s.note}>{draft.description}</Text>
      <Text style={s.date}>{Number.isNaN(Date.parse(draft.updatedAt)) ? 'Data não informada' : new Date(draft.updatedAt).toLocaleString('pt-BR')}</Text>
      {draft.pendingCreation ? <><Text style={s.warning}>Este cadastro foi enviado e ainda precisa de confirmação. Confira a tentativa antes de cadastrar novamente.</Text><Pressable accessibilityRole="button" disabled={busy} onPress={onPendingCreation} style={s.action}><Text style={s.link}>Conferir tentativa pendente</Text></Pressable></> :
        <Pressable accessibilityRole="button" accessibilityLabel={`Excluir rascunho: ${draft.title}`} disabled={busy || loading} onPress={() => confirm(draft)} style={s.action}><Text style={s.danger}>Excluir rascunho</Text></Pressable>}
    </View>)}
  </View>;
}
const s = StyleSheet.create({panel: {gap: 10}, heading: {color: '#F4F7F5', fontSize: 23, fontWeight: '800'}, note: {color: '#B5C7C3', fontSize: 14, lineHeight: 21}, card: {padding: 15, borderRadius: 14, backgroundColor: '#142326', borderWidth: 1, borderColor: '#30474A', gap: 8}, title: {color: '#F4F7F5', fontSize: 17, fontWeight: '700'}, date: {color: '#92A4A2', fontSize: 12}, link: {color: '#67DFC3', fontWeight: '700'}, danger: {color: '#FFB0A8', fontWeight: '700'}, warning: {color: '#F9C978', fontSize: 14, lineHeight: 21}, action: {minHeight: 48, justifyContent: 'center', paddingVertical: 10}});
