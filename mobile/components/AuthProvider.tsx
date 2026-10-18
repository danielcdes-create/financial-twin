import React, {createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore} from 'react';
import {ActivityIndicator, AppState, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {SafeAreaView} from 'react-native-safe-area-context';
import {API, api, authState} from '../utils/sessionClient';
import {createApiClient, isAbort, isTransientFailure} from '../utils/apiClient';
import {requirePrivateTransport} from '../utils/authState';
import * as biometrics from '../utils/biometricSession';
import {useScreenPrivacy} from '../utils/useScreenPrivacy';
import {devicePromptActive, subscribeDevicePrompt} from '../utils/devicePrompt';
import {clearDialogs} from '../utils/appAlert';
import appConfig from '../app.json';
import {ChoiceOverlayProvider} from './ChoiceOverlay';
import {ScheduleOverlayProvider} from './ScheduleOverlay';
import FingerprintMark from './FingerprintMark';

export type Identity = {username: string; display_name: string};
type SessionResponse = {access_token: string; expires_in: number; account: Identity};
type AuthContextValue = {authenticated: boolean; loginScreen: React.ReactNode; account: Identity | null; biometricOn: boolean; biometricSupported: boolean;
  setAccount: (account: Identity) => void; toggleBiometric: () => Promise<void>;
  replaceSession: (body: SessionResponse) => Promise<void>; lock: () => void; logout: (all?: boolean) => Promise<void>};
const AuthContext = createContext<AuthContextValue | null>(null);
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('Conta indisponível');
  return value;
}
export const deviceLabel = Platform.OS === 'web' ? 'Navegador' : Platform.OS === 'ios' ? 'iPhone / iPad' : 'Android';
export const BACKGROUND_LOCK_DELAY_MS = 15 * 60 * 1000;

export default function AuthProvider({children}: {children: React.ReactNode}) {
  const revision = useSyncExternalStore(authState.subscribe, authState.snapshot, authState.snapshot);
  const [account, setAccount] = useState<Identity | null>(null);
  const [visible, setVisible] = useState(Platform.OS === 'web' ? typeof document !== 'undefined' && document.visibilityState === 'visible' : AppState.currentState !== 'background');
  const [biometricOn, setBiometricOn] = useState(false);
  const [biometricSupported, setBiometricSupported] = useState(false);
  const [passwordMode, setPasswordMode] = useState(false);
  const [status, setStatus] = useState<{configured: boolean; setup_required: boolean; setup_available: boolean; legacy_available: boolean} | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0);
  const mounted = useRef(true);
  const checkedGeneration = useRef(0);
  const lastSessionIdentity = useRef(authState.sessionIdentity());
  const visibleRef = useRef(visible);
  const windowBlurred = useRef(false);
  const statusRef = useRef(status);
  const checkingRef = useRef(checking);
  const focusWaiters = useRef(new Set<() => void>());
  const validationControllers = useRef(new Set<AbortController>());
  const unlockedBiometric = useRef<{attempt: number; saved: {token: string; expiresAt: number}} | null>(null);
  const working = useRef(false);
  const prompting = useRef(false);
  const automaticBiometricAttempt = useRef<number | null>(null);
  const hiddenSince = useRef<number | null>(null);
  const hiddenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unlocked = authState.unlocked();
  useScreenPrivacy();
  useEffect(() => {statusRef.current = status; checkingRef.current = checking;}, [status, checking]);
  const resetLockCycle = useCallback(() => {
    if (hiddenTimer.current !== null) clearTimeout(hiddenTimer.current);
    hiddenTimer.current = null; hiddenSince.current = null;
    unlockedBiometric.current = null;
    generation.current++;
    validationControllers.current.forEach(controller => controller.abort());
    if (mounted.current) {setAccount(null); setPassword(''); setConfirmation(''); setSetupToken(''); setPasswordMode(false);}
    focusWaiters.current.forEach(resolve => resolve());
  }, []);
  // Expiration, HTTP 401 and queue invalidation call AuthState.lock directly.
  // They must invalidate the same callbacks and identity as the manual button.
  useEffect(() => authState.subscribe(() => {
    const identity = authState.sessionIdentity();
    if (!authState.unlocked()) {
      clearDialogs();
      if (identity !== lastSessionIdentity.current) resetLockCycle();
    }
    lastSessionIdentity.current = identity;
  }), [resetLockCycle]);
  const lock = useCallback(() => {authState.lock();}, []);
  const awaitForeground = useCallback(async (attempt: number) => {
    if (!visibleRef.current && attempt === generation.current) {
      await new Promise<void>((resolve, reject) => {
        const finish = () => {clearTimeout(timer); focusWaiters.current.delete(finish); resolve();};
        const timer = setTimeout(() => {focusWaiters.current.delete(finish); reject(new Error('Volte ao aplicativo e tente entrar novamente.'));}, 30_000);
        focusWaiters.current.add(finish);
      });
    }
    if (!mounted.current || attempt !== generation.current || !visibleRef.current) throw new Error('O aplicativo foi bloqueado. Entre novamente.');
  }, []);
  const check = useCallback(async () => {
    const attempt = ++checkedGeneration.current;
    setChecking(true); setError('');
    const valid = () => mounted.current && attempt === checkedGeneration.current;
    try {requirePrivateTransport(API); const body = await api.json('/v1/auth/status'); if (valid()) setStatus(body);}
    catch (e) {if (valid() && !isAbort(e)) setError(e instanceof Error ? e.message : 'Não foi possível conectar.');}
    finally {if (valid()) setChecking(false);}
  }, []);
  useEffect(() => {
    mounted.current = true;
    const initialCheck = setTimeout(() => check(), 0);
    void biometrics.removeLegacySession().catch(() => {});
    void Promise.all([biometrics.biometricAvailable(), biometrics.biometricEnabled()]).then(([supported, enabled]) => {
      if (mounted.current) {setBiometricSupported(supported); setBiometricOn(enabled);}
    }).catch(() => {});
    const attempts = generation;
    const checks = checkedGeneration;
    const waiters = focusWaiters.current;
    const validations = validationControllers.current;
    return () => {mounted.current = false; clearTimeout(initialCheck); checks.current++; attempts.current++; authState.lock(); validations.forEach(controller => controller.abort()); waiters.forEach(resolve => resolve());};
  }, [check]);
  useEffect(() => {
    // Android window blur can be a credential/keyboard/system overlay without
    // leaving this Activity. Cover immediately, retaining the accepted session
    // for fifteen minutes in the background. Never unlock beneath an overlay.
    const cover = () => {visibleRef.current = false; setVisible(false); validationControllers.current.forEach(controller => controller.abort()); clearDialogs({preserveNative: Platform.OS !== 'web' && AppState.currentState === 'active'}); if (authState.unlocked()) authState.suspend();};
    const hide = () => {
      cover();
      if (!authState.unlocked()) {
        // Only invalidate when an authentication request is actually in flight.
        // External autofill/credential overlays (e.g. Samsung Pass) while editing
        // login fields must not wipe typed credentials or restart biometric mode.
        if (!prompting.current && !devicePromptActive() && working.current) lock();
        return;
      }
      // Repeated inactive/background/picker events must not extend the deadline.
      hiddenSince.current ??= Date.now();
      if (hiddenTimer.current !== null) clearTimeout(hiddenTimer.current);
      const remaining = BACKGROUND_LOCK_DELAY_MS - (Date.now() - hiddenSince.current);
      if (remaining <= 0) lock();
      else hiddenTimer.current = setTimeout(lock, remaining);
    };
    const show = () => {
      // Timers can be frozen by Android. Check elapsed time before exposing data.
      if (hiddenSince.current !== null && Date.now() - hiddenSince.current >= BACKGROUND_LOCK_DELAY_MS) lock();
      if (!authState.unlocked() && authState.expiry() > 0) lock();
      // Android activity and window events are independent. An active Activity
      // still under a system overlay must remain covered until its focus event.
      if (Platform.OS === 'android' && windowBlurred.current) return;
      if (hiddenTimer.current !== null) clearTimeout(hiddenTimer.current);
      hiddenTimer.current = null; hiddenSince.current = null;
      visibleRef.current = true; setVisible(true); authState.resume(); focusWaiters.current.forEach(resolve => resolve());
      if (!statusRef.current && !checkingRef.current) void check();
      return biometrics.biometricAvailable().then(supported => {if (mounted.current) setBiometricSupported(supported);}).catch(() => {});
    };
    const prompts = subscribeDevicePrompt(() => {
      if (!devicePromptActive() && (Platform.OS === 'web' ? document.visibilityState !== 'visible' : AppState.currentState !== 'active')) hide();
    });
    const cleanup = () => {prompts(); if (hiddenTimer.current !== null) clearTimeout(hiddenTimer.current); hiddenTimer.current = null;};
    if (Platform.OS === 'web') {
      const change = () => {if (document.visibilityState !== 'visible') hide(); else show();};
      const focus = () => {if (document.visibilityState === 'visible') show();};
      document.addEventListener('visibilitychange', change); window.addEventListener('blur', hide); window.addEventListener('focus', focus);
      return () => {cleanup(); document.removeEventListener('visibilitychange', change); window.removeEventListener('blur', hide); window.removeEventListener('focus', focus);};
    }
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active') show(); else hide();
    });
    const blur = AppState.addEventListener('blur', () => {windowBlurred.current = true; cover();});
    const focus = AppState.addEventListener('focus', () => {windowBlurred.current = false; if (AppState.currentState === 'active') show();});
    return () => {cleanup(); subscription.remove(); blur.remove(); focus.remove();};
  }, [lock, check]);
  useEffect(() => {
    if (!authState.unlocked()) return;
    const timer = setTimeout(lock, Math.max(0, authState.expiry() - Date.now()));
    return () => clearTimeout(timer);
  }, [revision, lock]);

  const replaceSession = useCallback(async (body: SessionResponse) => {
    const attempt = generation.current;
    const expiresAt = Date.now() + body.expires_in * 1000;
    await awaitForeground(attempt);
    if (biometricOn) {
      prompting.current = true;
      try {await biometrics.updateBiometricSession(body.access_token, expiresAt);}
      catch {/* Keep biometric setting enabled if OS overlay/prompt was dismissed during password login */}
      finally {prompting.current = false;}
    }
    await awaitForeground(attempt);
    unlockedBiometric.current = null;
    authState.activate(body.access_token, expiresAt); setAccount(body.account);
    if (body.account?.username) setUsername(prev => prev || body.account.username);
    setStatus(value => value ? {...value, setup_required: false} : value);
  }, [biometricOn, awaitForeground]);

  const toggleBiometric = useCallback(async () => {
    if (biometricOn) {await biometrics.clearBiometricSession(); setBiometricOn(false); return;}
    let token = authState.getToken(); let expiry = authState.expiry();
    if (!token) throw new Error('Entre com sua senha para ativar a biometria.');
    const attempt = generation.current;
    try {
      const refreshed = await api.json('/v1/auth/biometric', {method: 'POST'});
      if (refreshed?.access_token && Number.isFinite(refreshed?.expires_in)) {
        token = refreshed.access_token;
        expiry = Date.now() + refreshed.expires_in * 1000;
        authState.activate(token, expiry);
      }
    } catch {/* Preserve existing token if refresh endpoint is not mocked or unreachable */}
    prompting.current = true;
    try {
      await biometrics.saveBiometricSession(token, expiry);
      if (attempt !== generation.current) {await biometrics.clearBiometricSession(); throw new Error('O aplicativo foi bloqueado. Tente novamente.');}
      setBiometricOn(true);
    } finally {prompting.current = false;}
  }, [biometricOn]);

  const logout = useCallback(async (all = false) => {
    // Require acknowledgement before reporting server revocation; an offline session can still be locked.
    await api.json(all ? '/v1/auth/logout-all' : '/v1/auth/logout', {method: 'POST'});
    lock();
    await biometrics.clearBiometricSession(); setBiometricOn(false);
  }, [lock]);

  const run = useCallback(async (operation: () => Promise<void>, clearSecrets = true) => {
    if (working.current) return;
    const attempt = generation.current;
    working.current = true; setBusy(true); setError('');
    let shouldClear = clearSecrets;
    try {await operation();}
    catch (e) {
      if (isAbort(e) || (clearSecrets && isTransientFailure(e))) shouldClear = false;
      if (mounted.current && attempt === generation.current) setError(isAbort(e) ? 'A validação foi interrompida. Tente entrar novamente.' : e instanceof Error ? e.message : 'Não foi possível entrar.');
    } finally {
      working.current = false;
      if (mounted.current) {
        setBusy(false);
        if (shouldClear && attempt === generation.current) {setPassword(''); setConfirmation(''); setSetupToken('');}
      }
    }
  }, []);
  const setup = !!status?.setup_required && !status.legacy_available;
  const submit = () => run(async () => {
    const attempt = generation.current;
    if (setup && (password.length < 12 || password !== confirmation)) throw new Error('Use uma senha com pelo menos 12 caracteres e repita a mesma senha.');
    const body = await api.json(setup ? '/v1/auth/setup' : '/v1/auth/session', {method: 'POST', body: JSON.stringify({username: username.trim().toLowerCase(), password,
      device_label: deviceLabel, ...(setup ? {display_name: name.trim(), setup_token: setupToken.trim()} : {})})});
    if (attempt !== generation.current) throw new Error('O aplicativo foi bloqueado. Entre novamente.');
    await replaceSession(body);
  });
  const unlockBiometric = useCallback(() => run(async () => {
    const attempt = generation.current;
    prompting.current = true;
    let saved: {token: string; expiresAt: number} | undefined;
    try {
      if (unlockedBiometric.current && unlockedBiometric.current.attempt === attempt && unlockedBiometric.current.saved.expiresAt > Date.now()) {
        saved = unlockedBiometric.current.saved;
      } else {
        saved = await biometrics.readBiometricSession();
        if (attempt !== generation.current) throw new Error('O aplicativo foi bloqueado. Tente novamente.');
        unlockedBiometric.current = {attempt, saved};
      }
      await awaitForeground(attempt);
      let renewedToken = saved.token;
      let renewedExpiresAt = saved.expiresAt;
      const validation = createApiClient({baseUrl: API, token: () => saved!.token,
        readRetryDelaysMs: [400, 800, 1500, 2500],
        fetch: async (input, init) => {
          requirePrivateTransport(API);
          const response = await fetch(input, init);
          const nextToken = response.headers?.get?.('X-Session-Token');
          const nextExpiresIn = Number(response.headers?.get?.('X-Session-Expires-In'));
          if (response.ok && nextToken && Number.isFinite(nextExpiresIn) && nextExpiresIn > 0) {
            renewedToken = nextToken;
            renewedExpiresAt = Date.now() + nextExpiresIn * 1000;
          }
          return response;
        }});
      const controller = new AbortController(); validationControllers.current.add(controller);
      let identity: Identity;
      try {identity = await validation.json('/v1/auth/account', {signal: controller.signal});}
      finally {validationControllers.current.delete(controller);}
      await awaitForeground(attempt);
      if (renewedToken !== saved.token) {
        await biometrics.updateBiometricSession(renewedToken, renewedExpiresAt).catch(() => {});
      }
      unlockedBiometric.current = null;
      authState.activate(renewedToken, renewedExpiresAt); setAccount(identity);
      if (identity?.username) setUsername(prev => prev || identity.username);
    } catch (e) {
      const status = (e as {status?: number})?.status;
      if (!saved || status === 401 || status === 403 || saved.expiresAt <= Date.now()) {
        unlockedBiometric.current = null;
        // Cancellation/failure leaves a usable password form, never a prompt loop.
        if (mounted.current && attempt === generation.current) setPasswordMode(true);
      }
      throw e;
    } finally {prompting.current = false;}
  }, false), [run, awaitForeground]);
  const biometricEntry = biometricOn && biometricSupported && !setup && !passwordMode;
  useEffect(() => {
    if (Platform.OS === 'web' || !biometricEntry || checking || !status?.configured || unlocked || !visible || busy || working.current) return;
    if (automaticBiometricAttempt.current === generation.current) return;
    // Consume before opening: the OS prompt itself can emit blur/focus events.
    automaticBiometricAttempt.current = generation.current;
    unlockBiometric();
  }, [biometricEntry, checking, status?.configured, unlocked, visible, busy, unlockBiometric]);

  const loginScreen = <SafeAreaView style={s.root}><KeyboardAvoidingView style={{flex: 1}} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={s.container}>
      <View style={s.card}>
        <Text style={s.brand}>FINANCIAL TWIN</Text><Text accessibilityRole="header" style={s.title}>{setup ? 'Sua conta, seus dados' : biometricEntry ? 'Desbloqueie seu financeiro' : 'Acesse seu financeiro'}</Text>
        <Text style={s.body}>{setup ? 'Configure o acesso privado ao seu histórico. Escolha uma senha que só você conhece.' : biometricEntry ? 'Use sua digital ou biometria no sensor indicado pelo aparelho.' : 'Entre para consultar e cuidar das suas finanças.'}</Text>
        {checking ? <ActivityIndicator color="#4FD1B5" accessibilityLabel="Verificando acesso"/> : <>
          {!!status && !status.configured && <Text style={s.error}>A segurança precisa ser configurada no servidor antes do primeiro acesso.</Text>}
          {biometricEntry ? <View style={s.biometricEntry}>
            <Pressable accessibilityRole="button" accessibilityLabel="Desbloquear com biometria" accessibilityState={{disabled: busy}} disabled={busy} onPress={unlockBiometric} style={s.fingerprintButton}>
              <FingerprintMark/>
            </Pressable>
            {busy && <ActivityIndicator color="#4FD1B5" accessibilityLabel="Validando biometria"/>}
            <Text style={s.body}>{busy ? 'Confirme sua biometria no aparelho…' : 'Toque na digital para tentar novamente.'}</Text>
            <AuthButton secondary title="Entrar com senha" disabled={busy} onPress={() => {setPasswordMode(true); setError('');}}/>
          </View> : <>
          {setup && <><Text style={s.label}>Como você quer ser chamado?</Text><TextInput accessibilityLabel="Nome" value={name} onChangeText={setName} maxLength={80} style={s.input} autoComplete="name"/></>}
          <Text style={s.label}>Usuário</Text><TextInput accessibilityLabel="Usuário" value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} autoComplete="username" textContentType="username" importantForAutofill="yes" maxLength={64} style={s.input} placeholder="ex.: daniel" placeholderTextColor="#8190A5"/>
          <Text style={s.label}>{setup ? 'Senha · mínimo de 12 caracteres' : 'Senha'}</Text><TextInput accessibilityLabel="Senha" value={password} onChangeText={setPassword} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete={setup ? 'new-password' : 'current-password'} textContentType={setup ? 'newPassword' : 'password'} importantForAutofill="yes" maxLength={256} style={s.input} onSubmitEditing={setup ? undefined : submit}/>
          {setup && <><Text style={s.label}>Repita a senha</Text><TextInput accessibilityLabel="Repita a senha" value={confirmation} onChangeText={setConfirmation} secureTextEntry autoComplete="new-password" textContentType="newPassword" importantForAutofill="yes" maxLength={256} style={s.input}/>
            <Text style={s.label}>Código de primeiro acesso</Text><TextInput accessibilityLabel="Código de primeiro acesso" value={setupToken} onChangeText={setSetupToken} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="off" maxLength={256} style={s.input}/>
            <Text style={s.hint}>No seu computador, abra “Primeiro acesso” e copie o código. Ele autoriza este cadastro e não será pedido nos próximos acessos.</Text></>}
          <AuthButton title={busy ? 'Aguarde…' : setup ? 'Criar meu acesso' : 'Entrar'} disabled={busy || !status?.configured || (setup && !status.setup_available)} onPress={submit}/>
          {setup && <AuthButton secondary title="Já configurei minha conta" disabled={busy} onPress={() => check()}/>}
          {!setup && biometricOn && <AuthButton secondary title="Desbloquear com biometria" disabled={busy} onPress={unlockBiometric}/>} 
          {status?.legacy_available && <Text style={s.hint}>Primeiro acesso: use sua senha atual do servidor. Seu usuário inicial será proprietario.</Text>}
          {!setup && <Text style={s.hint}>Esqueceu a senha? A recuperação é feita no seu servidor para proteger o histórico pessoal.</Text>}
          </>}
        </>}
        {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
        {(!status || !status.configured) && !checking && <AuthButton title="Tentar conexão novamente" secondary onPress={() => check()}/>}
        <View style={s.privacy}><Text style={s.label}>Privacidade desde o acesso</Text><Text style={s.hint}>Sua senha não fica salva no app. A biometria é opcional. Ao minimizar, seus dados ficam ocultos; o desbloqueio só é pedido após 15 minutos fora do app.</Text></View>
        <Text style={s.hint}>Versão {appConfig.expo.version}</Text>
      </View>
    </ScrollView>
  </KeyboardAvoidingView></SafeAreaView>;
  const context: AuthContextValue = {authenticated: unlocked, loginScreen, account, biometricOn, biometricSupported, setAccount, toggleBiometric, replaceSession, lock, logout};
  // The root navigator always exists. Expo Router controls which routes mount;
  // a password response must not replace/remount the entire navigation tree.
  return <AuthContext.Provider value={context}>
    <View style={{flex: 1}}>
      <View style={{flex: 1, opacity: visible ? 1 : 0}} pointerEvents={visible ? 'auto' : 'none'} accessibilityElementsHidden={!visible && unlocked} importantForAccessibility={visible || !unlocked ? 'auto' : 'no-hide-descendants'}>
        <ChoiceOverlayProvider><ScheduleOverlayProvider>{children}</ScheduleOverlayProvider></ChoiceOverlayProvider>
      </View>
      {!visible && <View style={[s.root, {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0}]} accessibilityLabel="Dados protegidos"/>}
    </View>
  </AuthContext.Provider>;
}

export function AuthButton({title, onPress, disabled = false, secondary = false}: {title: string; onPress: () => void; disabled?: boolean; secondary?: boolean}) {
  return <Pressable accessibilityRole="button" accessibilityState={{disabled}} aria-disabled={disabled} disabled={disabled} onPress={onPress} style={[s.button, secondary && s.secondary, disabled && {opacity: 0.45}]}><Text style={[s.buttonText, secondary && {color: '#C6EAE3'}]}>{title}</Text></Pressable>;
}
export const authStyles = StyleSheet.create({
  root: {flex: 1, backgroundColor: '#0B1220'}, container: {flexGrow: 1, justifyContent: 'center', padding: 20, alignItems: 'center'},
  card: {width: '100%', maxWidth: 440, gap: 12}, brand: {color: '#4FD1B5', fontSize: 13, fontWeight: '700', letterSpacing: 2},
  biometricEntry: {gap: 18, alignItems: 'center', paddingVertical: 24},
  fingerprintButton: {width: 112, height: 112, borderRadius: 56, borderWidth: 1, borderColor: '#37576A', backgroundColor: '#172238', alignItems: 'center', justifyContent: 'center'},
  title: {fontSize: 28, color: '#F2F5FA', fontWeight: '700'}, body: {fontSize: 16, lineHeight: 24, color: '#B8C4D5'},
  label: {fontSize: 14, fontWeight: '600', color: '#DCE5F1'}, hint: {fontSize: 13, lineHeight: 20, color: '#A6B6CD'},
  input: {minHeight: 48, backgroundColor: '#172238', borderWidth: 1, borderColor: '#35465F', borderRadius: 12, padding: 12, color: '#F2F5FA', fontSize: 16},
  button: {minHeight: 48, padding: 14, backgroundColor: '#4FD1B5', borderRadius: 12, alignItems: 'center', justifyContent: 'center'},
  secondary: {backgroundColor: '#1A2B3E', borderWidth: 1, borderColor: '#37576A'}, buttonText: {fontWeight: '700', fontSize: 15, color: '#08231D'},
  error: {color: '#FFB6B6', fontSize: 14, lineHeight: 21}, privacy: {gap: 8, borderTopWidth: 1, borderTopColor: '#29364B', paddingTop: 18, marginTop: 12},
});
const s = authStyles;
