import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Platform, Text, View} from 'react-native';
import {TextInput} from './InteractionControls';
import {api} from '../utils/sessionClient';
import {AuthButton, authStyles as s, deviceLabel, useAuth} from './AuthProvider';
import {Alert} from '../utils/appAlert';
import {useAsyncScope} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';

type DeviceSession = {id: string; label: string; created_at: number; expires_at: number; current: boolean};
export default function AccountSecurity() {
  const auth = useAuth();
  const [name, setName] = useState(auth.account?.display_name || '');
  const [username, setUsername] = useState(auth.account?.username || '');
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [sessions, setSessions] = useState<DeviceSession[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const captureScope=useAsyncScope(), generation=useRef(0), running=useRef(false);
  const load = useCallback(async () => {
    const valid=captureScope(), current=++generation.current;
    try {const body=await api.json('/v1/auth/sessions');if(valid()&&current===generation.current)setSessions(body.items);}
    catch(e) {if(valid()&&current===generation.current)throw e;}
  },[captureScope]);
  useEffect(() => {const valid=captureScope();void Promise.resolve().then(()=>{if(valid())return load();}).catch(e=>{if(valid())setError(e.message);});},[load,captureScope]);
  const refreshSaved = (message: string, valid: () => boolean) => {if(!valid())return;setNotice(message);void load().catch(()=>{if(valid())setError(`${message} Não foi possível atualizar as sessões. Use Atualizar sessões.`);});};
  const run = async (action: (valid: () => boolean) => Promise<void>) => {
    if (running.current) return;
    running.current=true;const valid=captureScope();
    setBusy(true); setError(''); setNotice('');
    try {await action(valid);} catch (e) {if(valid())setError(e instanceof Error ? e.message : 'Operação indisponível.');}
    finally {running.current=false;if(valid()){setBusy(false); setCurrent(''); setPassword(''); setConfirmation('');}}
  };
  const exit = (all: boolean) => Alert.alert(all ? 'Sair em todos os aparelhos?' : 'Encerrar esta sessão?',
    'O histórico no servidor e os itens locais pendentes serão preservados. A biometria deste aparelho será removida.',
    [{text: 'Cancelar', style: 'cancel'}, {text: 'Sair', style: 'destructive', onPress: () => run(() => auth.logout(all))}]);

  return <View style={{gap: 14, width: '100%', maxWidth: 600, alignSelf: 'center'}}>
    <Text style={s.title}>Minha conta</Text><Text style={s.body}>Gerencie seu acesso e a privacidade deste aparelho.</Text>
    {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}{!!notice && <Text accessibilityRole="alert" style={s.body}>{notice}</Text>}
    <Text style={s.label}>Nome</Text><TextInput accessibilityLabel="Nome da conta" style={s.input} value={name} onChangeText={setName} maxLength={80}/>
    <Text style={s.label}>Usuário</Text><TextInput accessibilityLabel="Usuário da conta" style={s.input} value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} maxLength={64}/>
    <AuthButton title="Salvar perfil" disabled={busy} onPress={() => run(async valid => {const account = await api.json('/v1/auth/account', {method: 'PATCH', body: JSON.stringify({username, display_name: name})}); if(!valid())return; auth.setAccount(account); setUsername(account.username); setName(account.display_name); setNotice('Perfil atualizado.');})}/>
    {(name!==(auth.account?.display_name||'')||username!==(auth.account?.username||''))&&<DiscardDraftAction disabled={busy} owner={auth.account?.username} label="Limpar perfil não salvo" title="Limpar alterações do perfil?" confirmLabel="Limpar" message="Os campos voltarão ao perfil salvo da sua conta." onDiscard={()=>{setName(auth.account?.display_name||'');setUsername(auth.account?.username||'');setError('');setNotice('');}} />}
    <View style={s.privacy}><Text style={s.label}>Desbloqueio por digital ou biometria</Text>
      <Text style={s.hint}>{Platform.OS === 'web' ? 'No navegador, a sessão fica apenas na memória e você entra com sua senha.' : 'A digital ou biometria forte é validada pelo sistema. O app não coleta sua impressão digital. A sessão protegida expira em 12 horas; depois, entre com a senha.'}</Text>
      {Platform.OS !== 'web' && <AuthButton secondary title={auth.biometricOn ? 'Desativar biometria' : 'Ativar biometria neste aparelho'} disabled={busy || (!auth.biometricSupported && !auth.biometricOn)} onPress={() => run(async () => {await auth.toggleBiometric();})}/>} 
      {Platform.OS !== 'web' && !auth.biometricSupported && <Text style={s.hint}>Para ativar, cadastre uma digital ou biometria forte nos ajustes do aparelho.</Text>}
      <AuthButton secondary title="Bloquear agora" onPress={auth.lock}/>
    </View>
    <View style={s.privacy}><Text style={s.label}>Trocar senha</Text><Text style={s.hint}>A troca encerra as outras sessões imediatamente.</Text>
      <Text style={s.label}>Senha atual</Text><TextInput accessibilityLabel="Senha atual" style={s.input} value={current} onChangeText={setCurrent} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="current-password" maxLength={256}/>
      <Text style={s.label}>Nova senha · mínimo de 12 caracteres</Text><TextInput accessibilityLabel="Nova senha" style={s.input} value={password} onChangeText={setPassword} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="new-password" maxLength={256}/>
      <Text style={s.label}>Repita a nova senha</Text><TextInput accessibilityLabel="Repita a nova senha" style={s.input} value={confirmation} onChangeText={setConfirmation} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="new-password" maxLength={256}/>
      <AuthButton title="Atualizar senha" disabled={busy} onPress={() => run(async valid => {
        if (password.length < 12 || password !== confirmation) throw new Error('Use pelo menos 12 caracteres e repita a mesma senha.');
        const body = await api.json('/v1/auth/password', {method: 'POST', body: JSON.stringify({current_password: current, new_password: password, device_label: deviceLabel})});
        if(!valid())return;
        await auth.replaceSession(body); refreshSaved('Senha atualizada. As outras sessões foram encerradas.', valid);
      })}/>
      {!!(current||password||confirmation)&&<DiscardDraftAction disabled={busy} owner={auth.account?.username} label="Limpar senhas digitadas" title="Limpar senhas digitadas?" confirmLabel="Limpar" message="Os campos da troca de senha serão esvaziados. Sua senha de acesso permanece a mesma." onDiscard={()=>{setCurrent('');setPassword('');setConfirmation('');setError('');setNotice('');}} />}
    </View>
    <View style={s.privacy}><Text style={s.label}>Sessões ativas</Text><Text style={s.hint}>Os nomes identificam o tipo de aparelho; não coletamos sua localização.</Text>
      <AuthButton secondary title="Atualizar sessões" disabled={busy} onPress={() => run(load)}/>
      {sessions.map(item => <View key={item.id} style={{gap: 8, paddingVertical: 10}}><Text style={s.body}>{item.label}{item.current ? ' · esta sessão' : ''}</Text>
        <Text style={s.hint}>Entrada: {new Date(item.created_at * 1000).toLocaleString('pt-BR')}</Text>
        {!item.current && <AuthButton secondary title="Encerrar sessão" disabled={busy} onPress={() => run(async valid => {await api.json('/v1/auth/sessions/' + item.id, {method: 'DELETE'}); refreshSaved('Sessão encerrada.', valid);})}/>}
      </View>)}
      <AuthButton secondary title="Sair deste aparelho" disabled={busy} onPress={() => exit(false)}/>
      <AuthButton secondary title="Sair de todos os aparelhos" disabled={busy} onPress={() => exit(true)}/>
    </View>
    <View style={s.privacy}><Text style={s.label}>Seus dados</Text>
      <Text style={s.hint}>Ao minimizar, a tela fica oculta e o envio é pausado. Você pode voltar por até 15 minutos sem novo desbloqueio. Bloquear agora encerra essa tolerância imediatamente. Capturas pendentes ficam salvas no aparelho.</Text>
      <Text style={s.hint}>Você pode tirar prints das telas do aplicativo. Ao minimizar, o conteúdo fica oculto.</Text>
      <Text style={s.hint}>Fotos, voz e documentos enviados para análise usam o processamento de IA já configurado no servidor. Seu acesso não exige CPF, telefone ou e-mail.</Text>
    </View>
  </View>;
}
