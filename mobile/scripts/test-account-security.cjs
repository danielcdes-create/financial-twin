const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const cache = new Map();
let strong = true, enrolled = true, confirmed = true;
const platform = {OS: 'ios'};
const values = new Map(), writes = [];
const nativeStore = {WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7, canUseBiometricAuthentication: () => true,
  getItemAsync: async (key, options) => {if (key.includes('biometric-session')) assert.equal(options.requireAuthentication, true); return values.get(key) || null;},
  setItemAsync: async (key, value, options) => {writes.push({key, value, options}); values.set(key, value);},
  deleteItemAsync: async key => values.delete(key)};
let prompt;
const localAuth = {SecurityLevel: {BIOMETRIC_STRONG: 3}, hasHardwareAsync: async () => true,
  isEnrolledAsync: async () => enrolled, getEnrolledLevelAsync: async () => strong ? 3 : 2,
  authenticateAsync: async options => {prompt = options; return {success: confirmed};}};
function load(file) {
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText,
    {module, exports: module.exports, URL, Headers, FormData, AbortController, fetch, Date, setTimeout, clearTimeout,
      process: {env: {EXPO_PUBLIC_API_BASE_URL: 'https://private.example'}}, window: {location: {hostname: '127.0.0.1'}},
      require: name => name === 'react-native' ? {Platform: platform} : name === 'expo-secure-store' ? nativeStore : name === 'expo-local-authentication' ? localAuth : load('utils/' + name.replace('./', '') + '.ts')});
  cache.set(file, module.exports); return module.exports;
}
(async () => {
  const {AuthState, requirePrivateTransport} = load('utils/authState.ts');
  const state = new AuthState();
  assert.equal(state.getToken(), '');
  assert.throws(() => state.activate('expired', Date.now() - 1), /expirada/);
  state.activate('sensitive-token', Date.now() + 60000);
  state.suspend(); assert.equal(state.getToken(), '', 'OS dialog hides data and suspends network'); assert.equal(state.unlocked(), true);
  state.resume(); assert.equal(state.getToken(), 'sensitive-token');
  state.lock(); assert.equal(state.getToken(), ''); assert.equal(state.unlocked(), false);
  for (const url of ['https://private.example', 'http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000']) requirePrivateTransport(url);
  for (const url of ['http://private.example', 'http://100.70.1.2', 'https://user:password@private.example', 'file:///private', '']) assert.throws(() => requirePrivateTransport(url));

  const biometric = load('utils/biometricSession.ts');
  assert.equal(await biometric.biometricAvailable(), true);
  strong = false; assert.equal(await biometric.biometricAvailable(), false); strong = true;
  enrolled = false; await assert.rejects(biometric.saveBiometricSession('secret', Date.now() + 60000), /Cadastre/); enrolled = true;
  confirmed = false; await assert.rejects(biometric.saveBiometricSession('secret', Date.now() + 60000), /não confirmada/);
  assert.equal(writes.length, 0, 'Cancellation must never enable biometrics or persist a token'); confirmed = true;
  await biometric.saveBiometricSession('biometric-secret', Date.now() + 60000);
  const sessionWrite = writes.find(item => item.key.includes('biometric-session'));
  assert.equal(sessionWrite.options.requireAuthentication, true);
  assert.equal(sessionWrite.options.keychainAccessible, 7);
  assert.equal(prompt.disableDeviceFallback, true); assert.equal(prompt.biometricsSecurityLevel, 'strong');
  assert.equal(await biometric.biometricEnabled(), true);
  assert.equal((await biometric.readBiometricSession()).token, 'biometric-secret');
  values.delete(sessionWrite.key); await assert.rejects(biometric.readBiometricSession(), /alterada/);
  values.set(sessionWrite.key, JSON.stringify({token: 'expired', expiresAt: Date.now() - 1}));
  await assert.rejects(biometric.readBiometricSession(), /expirada/);
  await biometric.clearBiometricSession(); assert.equal(await biometric.biometricEnabled(), false);
  const web = load('utils/biometricSession.web.ts');
  assert.equal(await web.biometricAvailable(), false);
  await assert.rejects(web.saveBiometricSession('never-store-on-web', Date.now() + 60000));

  const {createApiClient} = load('utils/apiClient.ts');
  let calls = 0;
  const client = createApiClient({baseUrl: 'https://private.example', token: state.getToken, requireSession: true, readRetryDelaysMs: [],
    fetch: async (_url, init) => {calls++; assert.equal(init.cache, 'no-store'); return {ok: true, status: 200, text: async () => '{}'};}});
  await assert.rejects(client.json('/v1/dashboard'), /Entre/); assert.equal(calls, 0, 'Locked app must not send finance requests');
  await client.json('/v1/auth/status'); assert.equal(calls, 1);
  state.activate('current-token', Date.now() + 60000);
  let finish, captured;
  const slow = createApiClient({baseUrl: 'https://private.example', token: state.getToken, readRetryDelaysMs: [],
    fetch: async (_url, init) => {captured = init; return new Promise(resolve => {finish = resolve;});}});
  const pending = slow.json('/v1/transactions/x', {method: 'PATCH', body: '{}'});
  state.lock(); slow.reset();
  assert.equal(captured.signal.aborted, true, 'Lock cancels writes as well as reads');
  finish({ok: true, status: 200, text: async () => '{"sensitive":"late data"}'});
  await assert.rejects(pending, error => error.name === 'AbortError');
  let invalidations = 0;
  state.activate('revoked-token', Date.now() + 60000);
  const revoked = createApiClient({baseUrl: 'https://private.example', token: state.getToken, readRetryDelaysMs: [], onUnauthorized: () => {invalidations++; state.lock();},
    fetch: async () => ({ok: false, status: 401, text: async () => '{"detail":"Sessão encerrada"}'})});
  await assert.rejects(revoked.json('/v1/auth/account'));
  assert.equal(invalidations, 1); assert.equal(state.getToken(), '');
  platform.OS = 'web';
  const desktop = load('utils/sessionClient.ts');
  assert.equal(desktop.API, 'http://127.0.0.1:8000', 'Local desktop must use the local server without tailnet dependency');
  await assert.rejects(desktop.api.json('/v1/dashboard'), /Entre/);
  console.log('PASS: account privacy, TLS, strong biometrics, cancellation, protected storage, expiration, revocation and late-write blocking.');
})().catch(error => {console.error(error); process.exitCode = 1;});
