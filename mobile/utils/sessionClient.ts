import { createApiClient } from './apiClient';
import { AuthState, requirePrivateTransport } from './authState';
import { Platform } from 'react-native';
import { uploadFetch } from './uploadTransport';

const configuredApi = (process.env.EXPO_PUBLIC_API_BASE_URL || '').replace(/\/$/, '');
function baseUrl() {
  if (Platform.OS !== 'web') return configuredApi;
  const webApi = (process.env.EXPO_PUBLIC_WEB_API_BASE_URL || '').replace(/\/$/, '');
  if (webApi) return webApi;
  if (typeof window !== 'undefined' && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) {
    // The desktop launcher starts the local API on this port; it need not send credentials through the tailnet.
    try {if (['localhost', '127.0.0.1', '[::1]'].includes(new URL(configuredApi).hostname)) return configuredApi;} catch {}
    return 'http://127.0.0.1:8000';
  }
  return configuredApi;
}
export const API = baseUrl();
export const authState = new AuthState();
export const api = createApiClient({baseUrl: API, token: authState.getToken, requireSession: true,
  awaitSession: authState.awaitSession,
  sessionIdentity: authState.sessionIdentity,
  fetch: (input, init) => {requirePrivateTransport(API); return fetch(input, init);},
  uploadFetch: (input, init) => {requirePrivateTransport(API); return uploadFetch(input, init);},
  onUnauthorized: () => authState.lock()});
authState.subscribe(() => api.reset());
