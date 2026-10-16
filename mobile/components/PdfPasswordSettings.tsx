import React, {useEffect, useRef, useState} from 'react';
import {Platform, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {clearPdfCpf, getPdfCpf, setPdfCpf} from '../utils/pdfCpf';
import {useAsyncScope} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';

type Props = {disabled: boolean; onChanged: () => Promise<void>};

export default function PdfPasswordSettings({disabled, onChanged}: Props) {
  const [expanded, setExpanded] = useState(false), [configured, setConfigured] = useState(false);
  const [value, setValue] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const generation = useRef(0), captureScope = useAsyncScope();
  useEffect(() => {
    const current = ++generation.current, valid = captureScope();
    void getPdfCpf().then(cpf => {if (valid() && current === generation.current) setConfigured(!!cpf);})
      .catch(() => {if (valid() && current === generation.current) setError('Não foi possível consultar o CPF salvo. Tente novamente.');});
  }, [captureScope]);
  const save = async (remove = false) => {
    if (busy || disabled) return;
    const valid = captureScope(); generation.current++;
    setBusy(true);setError('');setMessage('');
    try {
      if (remove) await clearPdfCpf(); else await setPdfCpf(value);
      if (!valid()) return;
      setConfigured(!remove);setValue('');
      setMessage(remove ? 'CPF removido.' : 'CPF salvo. PDFs que aguardavam senha serão tentados novamente.');
      if (!remove) void Promise.resolve().then(onChanged).catch(() => {if (valid()) setError('CPF salvo. Não foi possível retomar a fila. Tente sincronizar novamente.');});
    } catch (failure) {if (valid()) setError(failure instanceof Error ? failure.message : 'Não foi possível salvar o CPF.');}
    finally {if (valid()) setBusy(false);}
  };
  return <View style={s.card}>
    <Pressable accessibilityRole="button" accessibilityLabel="CPF para PDFs com senha" accessibilityState={{expanded, disabled: busy || disabled}} aria-expanded={expanded} aria-disabled={busy || disabled}
      disabled={busy || disabled} onPress={() => setExpanded(current => !current)} style={s.toggle}>
      <Text style={s.title}>CPF para PDFs com senha</Text>
      <Text style={s.note}>{configured ? 'CPF informado' : 'Opcional · informe se o banco pedir CPF'} · {expanded ? 'Recolher' : 'Configurar'}</Text>
    </Pressable>
    {expanded && <View style={s.fields}>
      <Text style={s.note}>{Platform.OS === 'web' ? 'No navegador, o CPF fica apenas nesta sessão.' : 'O CPF fica no armazenamento protegido deste aparelho.'} O app tenta usá-lo, sem pontos ou traço, quando o PDF pede senha.</Text>
      <TextInput accessibilityLabel="CPF para abrir PDFs" value={value} onChangeText={setValue} secureTextEntry
        keyboardType="number-pad" maxLength={14} autoCorrect={false} autoComplete="off" editable={!busy && !disabled}
        placeholder={configured ? 'Novo CPF, se quiser alterar' : 'CPF completo (11 dígitos)'} placeholderTextColor="#8BA3A0" style={s.input} />
      <Pressable accessibilityRole="button" accessibilityLabel="Salvar CPF para PDFs" disabled={busy || disabled} onPress={() => save()} style={[s.button, (busy || disabled) && s.disabled]}>
        <Text style={s.buttonText}>{busy ? 'Salvando…' : 'Salvar CPF para PDFs'}</Text>
      </Pressable>
      {configured && <Pressable accessibilityRole="button" accessibilityLabel="Remover CPF salvo" disabled={busy || disabled} onPress={() => save(true)} style={s.remove}><Text style={s.note}>Remover CPF salvo</Text></Pressable>}
      {!!value && <DiscardDraftAction disabled={busy || disabled} label="Limpar CPF não salvo" title="Limpar CPF não salvo?" confirmLabel="Limpar" message="O CPF que você digitou será apagado do campo. O CPF já salvo permanece configurado." onDiscard={()=>{setValue('');setError('');setMessage('');}} />}
    </View>}
    {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
    {!!message && <Text accessibilityLiveRegion="polite" style={s.note}>{message}</Text>}
  </View>;
}

const s = StyleSheet.create({
  card: {marginTop: 16, borderWidth: 1, borderColor: '#315054', borderRadius: 12, backgroundColor: '#142326', padding: 12},
  toggle: {minHeight: 44, justifyContent: 'center'}, title: {fontSize: 14, fontWeight: '700', color: '#D5E2DF'},
  note: {fontSize: 12, lineHeight: 18, color: '#AFC1BF', marginTop: 5}, fields: {gap: 9, marginTop: 8},
  input: {minHeight: 48, borderWidth: 1, borderColor: '#315054', borderRadius: 10, padding: 12, color: '#F4F7F5', fontSize: 16},
  button: {minHeight: 48, borderRadius: 10, backgroundColor: '#49C5AA', alignItems: 'center', justifyContent: 'center', padding: 10},
  buttonText: {fontSize: 14, fontWeight: '700', color: '#061412'}, remove: {minHeight: 44, justifyContent: 'center'},
  disabled: {opacity: .5}, error: {fontSize: 12, lineHeight: 18, color: '#FFAAA0', marginTop: 8},
});
