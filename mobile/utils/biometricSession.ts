import * as SecureStore from 'expo-secure-store';
import * as LocalAuthentication from 'expo-local-authentication';
import { Platform } from 'react-native';

const KEY = 'financial-twin-biometric-session-v1';
const ENABLED = 'financial-twin-biometric-enabled-v1';
const options: SecureStore.SecureStoreOptions = {requireAuthentication: true,
  keychainService: 'financial-twin-biometric-v1',
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  authenticationPrompt: 'Desbloquear Financial Twin'};

export async function biometricAvailable() {
  return SecureStore.canUseBiometricAuthentication() && await LocalAuthentication.hasHardwareAsync() &&
    await LocalAuthentication.isEnrolledAsync() &&
    await LocalAuthentication.getEnrolledLevelAsync() === LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG;
}
export async function biometricEnabled() {return await SecureStore.getItemAsync(ENABLED) === '1';}
export async function saveBiometricSession(token: string, expiresAt: number) {
  if (!await biometricAvailable()) throw new Error('Cadastre uma digital ou biometria forte nos ajustes do aparelho.');
  // Android prompts while writing the authenticated key. iOS needs confirmation on creation.
  if (Platform.OS === 'ios') {
    const result = await LocalAuthentication.authenticateAsync({promptMessage: 'Ativar desbloqueio por biometria',
      cancelLabel: 'Cancelar', disableDeviceFallback: true, fallbackLabel: '', biometricsSecurityLevel: 'strong'});
    if (!result.success) throw new Error('Biometria não confirmada. Sua senha continua disponível.');
  }
  await SecureStore.setItemAsync(KEY, JSON.stringify({token, expiresAt}), options);
  await SecureStore.setItemAsync(ENABLED, '1', {keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
}
export async function updateBiometricSession(token: string, expiresAt: number) {
  await SecureStore.setItemAsync(KEY, JSON.stringify({token, expiresAt}), options);
}
export async function readBiometricSession(): Promise<{token: string; expiresAt: number}> {
  const value = await SecureStore.getItemAsync(KEY, options);
  if (!value) throw new Error('Biometria alterada ou sessão indisponível. Entre com sua senha e ative novamente.');
  const saved = JSON.parse(value);
  if (typeof saved.token !== 'string' || typeof saved.expiresAt !== 'number' || saved.expiresAt <= Date.now()) {
    throw new Error('Sessão expirada. Entre com sua senha.');
  }
  return saved;
}
export async function clearBiometricSession() {
  await SecureStore.deleteItemAsync(KEY, options);
  await SecureStore.deleteItemAsync(ENABLED);
}
export async function removeLegacySession() {await SecureStore.deleteItemAsync('financial-twin-session');}
