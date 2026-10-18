import * as SecureStore from 'expo-secure-store';
import * as LocalAuthentication from 'expo-local-authentication';
import { Platform } from 'react-native';

const KEY = 'financial-twin-biometric-session-v1';
const ENABLED = 'financial-twin-biometric-enabled-v1';
const options: SecureStore.SecureStoreOptions = {requireAuthentication: true,
  keychainService: 'financial-twin-biometric-v1',
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  authenticationPrompt: 'Desbloquear Financial Twin'};

let inMemoryAnchor = '';
let cachedEnvelope = '';

function hashWords(input: string, seed: number): [number, number] {
  let h1 = (0x811c9dc5 ^ seed) >>> 0;
  let h2 = (0x9e3779b9 ^ (seed * 0x85ebca6b)) >>> 0;
  for (let i = 0; i < input.length; i++) {
    const code = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ code, 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ ((code << 7) | (code >>> 9)), 0x27d4eb2d) >>> 0;
  }
  return [h1, h2];
}

function hex32(n: number): string {
  return (n >>> 0).toString(16).padStart(8, '0');
}

function sealEnvelope(anchor: string, plain: string): string {
  const [k1, k2] = hashWords(anchor, 0xA5A55A5A);
  let out = '';
  let s1 = k1 || 1;
  let s2 = k2 || 2;
  for (let i = 0; i < plain.length; i++) {
    s1 = Math.imul(s1 ^ (s1 >>> 13), 0x5bd1e995) >>> 0;
    s2 = Math.imul(s2 ^ (s2 << 7), 0x27d4eb2d) >>> 0;
    const mask = (s1 ^ s2) & 0xffff;
    out += (plain.charCodeAt(i) ^ mask).toString(16).padStart(4, '0');
  }
  const [t1, t2] = hashWords(anchor + ':' + out, 0x3C6EF372);
  return `${hex32(t1)}${hex32(t2)}:${out}`;
}

function openEnvelope(anchor: string, envelope: string): {token: string; expiresAt: number} | null {
  const sep = envelope.indexOf(':');
  if (sep !== 16) return null;
  const tag = envelope.slice(0, 16);
  const body = envelope.slice(17);
  if (!body || body.length % 4 !== 0) return null;
  const [t1, t2] = hashWords(anchor + ':' + body, 0x3C6EF372);
  if (`${hex32(t1)}${hex32(t2)}` !== tag) return null;
  const [k1, k2] = hashWords(anchor, 0xA5A55A5A);
  let plain = '';
  let s1 = k1 || 1;
  let s2 = k2 || 2;
  for (let i = 0; i < body.length; i += 4) {
    const word = parseInt(body.slice(i, i + 4), 16);
    if (!Number.isFinite(word)) return null;
    s1 = Math.imul(s1 ^ (s1 >>> 13), 0x5bd1e995) >>> 0;
    s2 = Math.imul(s2 ^ (s2 << 7), 0x27d4eb2d) >>> 0;
    const mask = (s1 ^ s2) & 0xffff;
    plain += String.fromCharCode(word ^ mask);
  }
  try {
    return JSON.parse(plain);
  } catch {
    return null;
  }
}

function normalizeBiometricError(error: unknown): Error {
  const message = error instanceof Error ? error.message : String(error || '');
  if (/KeyPermanentlyInvalidated|permanently invalidated|KeyStoreException/i.test(message)) {
    return new Error('Biometria alterada ou sessão indisponível. Entre com sua senha e ative novamente.');
  }
  if (/User canceled the authentication|Authentication canceled|System canceled|negative button/i.test(message)) {
    return new Error('Leitura da digital cancelada. Toque na digital para tentar novamente ou use sua senha.');
  }
  if (/Too many attempts|LOCKOUT/i.test(message)) {
    return new Error('Muitas tentativas no sensor. Aguarde alguns segundos ou entre com sua senha.');
  }
  return error instanceof Error ? error : new Error('Não foi possível validar a biometria.');
}

async function writeProtectedKey(payload: string) {
  try {
    await SecureStore.setItemAsync(KEY, payload, options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error || '');
    if (/KeyPermanentlyInvalidated|permanently invalidated|KeyStoreException|Could not encrypt/i.test(message)) {
      await SecureStore.deleteItemAsync(KEY, options).catch(() => {});
      await SecureStore.setItemAsync(KEY, payload, options);
      return;
    }
    throw normalizeBiometricError(error);
  }
}

export async function biometricAvailable() {
  return SecureStore.canUseBiometricAuthentication() && await LocalAuthentication.hasHardwareAsync() &&
    await LocalAuthentication.isEnrolledAsync() &&
    await LocalAuthentication.getEnrolledLevelAsync() === LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG;
}
export async function biometricEnabled() {
  const value = await SecureStore.getItemAsync(ENABLED);
  if (typeof value === 'string' && value.startsWith('1:')) {
    cachedEnvelope = value.slice(2);
    return true;
  }
  cachedEnvelope = '';
  return value === '1';
}
export async function saveBiometricSession(token: string, expiresAt: number) {
  if (!await biometricAvailable()) throw new Error('Cadastre uma digital ou biometria forte nos ajustes do aparelho.');
  // Android prompts while writing the authenticated key. iOS needs confirmation on creation.
  if (Platform.OS === 'ios') {
    const result = await LocalAuthentication.authenticateAsync({promptMessage: 'Ativar desbloqueio por biometria',
      cancelLabel: 'Cancelar', disableDeviceFallback: true, fallbackLabel: '', biometricsSecurityLevel: 'strong'});
    if (!result.success) throw new Error('Biometria não confirmada. Sua senha continua disponível.');
  }
  const payload = JSON.stringify({token, expiresAt});
  await writeProtectedKey(payload);
  await SecureStore.setItemAsync(ENABLED, '1', {keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
  inMemoryAnchor = payload;
  cachedEnvelope = '';
}
export async function updateBiometricSession(token: string, expiresAt: number) {
  const payload = JSON.stringify({token, expiresAt});
  if (inMemoryAnchor) {
    const sealed = sealEnvelope(inMemoryAnchor, payload);
    await SecureStore.setItemAsync(ENABLED, `1:${sealed}`, {keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    cachedEnvelope = sealed;
    return;
  }
  await writeProtectedKey(payload);
  inMemoryAnchor = payload;
  cachedEnvelope = '';
}
export async function readBiometricSession(): Promise<{token: string; expiresAt: number}> {
  let value: string | null;
  try {
    value = await SecureStore.getItemAsync(KEY, options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error || '');
    if (/KeyPermanentlyInvalidated|permanently invalidated|KeyStoreException/i.test(message)) {
      inMemoryAnchor = '';
      cachedEnvelope = '';
      await SecureStore.deleteItemAsync(KEY, options).catch(() => {});
    }
    throw normalizeBiometricError(error);
  }
  if (!value) {
    inMemoryAnchor = '';
    cachedEnvelope = '';
    throw new Error('Biometria alterada ou sessão indisponível. Entre com sua senha e ative novamente.');
  }
  inMemoryAnchor = value;
  let saved = JSON.parse(value);
  if (cachedEnvelope) {
    const refreshed = openEnvelope(value, cachedEnvelope);
    if (refreshed && typeof refreshed.token === 'string' && typeof refreshed.expiresAt === 'number' &&
        refreshed.expiresAt >= (typeof saved?.expiresAt === 'number' ? saved.expiresAt : 0)) {
      saved = refreshed;
    }
  }
  if (typeof saved.token !== 'string' || typeof saved.expiresAt !== 'number' || saved.expiresAt <= Date.now()) {
    throw new Error('Sessão expirada. Entre com sua senha.');
  }
  return saved;
}
export async function clearBiometricSession() {
  inMemoryAnchor = '';
  cachedEnvelope = '';
  await SecureStore.deleteItemAsync(KEY, options);
  await SecureStore.deleteItemAsync(ENABLED);
}
export async function removeLegacySession() {await SecureStore.deleteItemAsync('financial-twin-session');}
