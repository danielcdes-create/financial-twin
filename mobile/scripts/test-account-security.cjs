const assert = require('node:assert/strict');
const nodeCrypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const done = typeof globalThis.__expectAsyncCompletion === 'function'
  ? globalThis.__expectAsyncCompletion('test-account-security')
  : null;
let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'test-account-security async assertions did not finish');
});

const cache = new Map();
let strong = true, enrolled = true, confirmed = true;
let failWriteOnceInvalidated = false, failReadError = null;
const platform = {OS: 'ios'};
const values = new Map(), writes = [], deletes = [];
const nativeStore = {
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7,
  canUseBiometricAuthentication: () => true,
  getItemAsync: async (key, options) => {
    if (key.includes('biometric-session')) {
      assert.equal(options.requireAuthentication, true);
      if (failReadError) throw failReadError;
    }
    return values.get(key) || null;
  },
  setItemAsync: async (key, value, options) => {
    if (key.includes('biometric-session') && failWriteOnceInvalidated) {
      failWriteOnceInvalidated = false;
      throw new Error('android.security.keystore.KeyPermanentlyInvalidatedException: Key permanently invalidated');
    }
    writes.push({key, value, options});
    values.set(key, value);
  },
  deleteItemAsync: async key => {
    deletes.push(key);
    values.delete(key);
  },
};
let prompt;
const localAuth = {
  SecurityLevel: {BIOMETRIC_STRONG: 3},
  hasHardwareAsync: async () => true,
  isEnrolledAsync: async () => enrolled,
  getEnrolledLevelAsync: async () => (strong ? 3 : 2),
  authenticateAsync: async options => {
    prompt = options;
    return {success: confirmed};
  },
};

let csprngCalls = 0;
const webcryptoProxy = {
  getRandomValues(arr) {
    csprngCalls++;
    return nodeCrypto.webcrypto.getRandomValues(arr);
  },
};

function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    }).outputText,
    {
      module,
      exports: module.exports,
      URL,
      Headers,
      FormData,
      AbortController,
      fetch,
      Date,
      Math,
      Number,
      String,
      Boolean,
      Error,
      Uint8Array,
      Uint32Array,
      BigInt,
      JSON,
      Promise,
      crypto: webcryptoProxy,
      setTimeout,
      clearTimeout,
      process: {env: {EXPO_PUBLIC_API_BASE_URL: 'https://private.example'}},
      window: {location: {hostname: '127.0.0.1'}},
      require: name =>
        name === 'react-native'
          ? {Platform: platform}
          : name === 'expo-secure-store'
            ? nativeStore
            : name === 'expo-local-authentication'
              ? localAuth
              : load(path.resolve(path.dirname(file), name) + '.ts'),
    },
    {filename: file},
  );
  cache.set(file, module.exports);
  return module.exports;
}

(async () => {
  const {AuthState, requirePrivateTransport} = load('utils/authState.ts');
  const state = new AuthState();
  assert.equal(state.getToken(), '');
  assert.throws(() => state.activate('expired', Date.now() - 1), /expirada/);
  state.activate('sensitive-token', Date.now() + 60000);
  state.suspend();
  assert.equal(state.getToken(), '', 'OS dialog hides data and suspends network');
  assert.equal(state.unlocked(), true);
  state.resume();
  assert.equal(state.getToken(), 'sensitive-token');
  state.lock();
  assert.equal(state.getToken(), '');
  assert.equal(state.unlocked(), false);
  for (const url of ['https://private.example', 'http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000']) {
    requirePrivateTransport(url);
  }
  for (const url of ['http://private.example', 'http://100.70.1.2', 'https://user:password@private.example', 'file:///private', '']) {
    assert.throws(() => requirePrivateTransport(url));
  }

  const biometric = load('utils/biometricSession.ts');

  // ChaCha20-Poly1305 AEAD envelope vectors: multi-block (>64B), UTF-8 2/3/4-byte chars, and tampering rejection
  const anchorA = JSON.stringify({aeadKey: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'});
  const anchorB = JSON.stringify({aeadKey: 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210'});
  const multiBlockToken = `tok-ação-€-💳🔐-${'x'.repeat(180)}`;
  const multiBlockPlain = JSON.stringify({token: multiBlockToken, expiresAt: 1999999999000});
  assert.ok(Buffer.byteLength(multiBlockPlain, 'utf8') > 192, 'Exercises >= 3 ChaCha20 64-byte blocks');
  const csprngBeforeSeal = csprngCalls;
  const sealed = biometric.sealEnvelope(anchorA, multiBlockPlain);
  assert.ok(csprngCalls > csprngBeforeSeal, 'sealEnvelope uses WebCrypto CSPRNG when available');
  assert.match(sealed, /^v2:[0-9a-f]{24}:[0-9a-f]+:[0-9a-f]{32}$/);
  const opened = biometric.openEnvelope(anchorA, sealed);
  assert.deepEqual(opened, {token: multiBlockToken, expiresAt: 1999999999000});

  const flipHexByte = hex => {
    const firstByte = parseInt(hex.slice(0, 2), 16) ^ 0x01;
    return firstByte.toString(16).padStart(2, '0') + hex.slice(2);
  };
  const [, nonceHex, ctHex, tagHex] = sealed.split(':');
  assert.equal(biometric.openEnvelope(anchorA, `v2:${flipHexByte(nonceHex)}:${ctHex}:${tagHex}`), null, 'Bit-flipped nonce rejected');
  assert.equal(biometric.openEnvelope(anchorA, `v2:${nonceHex}:${flipHexByte(ctHex)}:${tagHex}`), null, 'Bit-flipped ciphertext rejected');
  assert.equal(biometric.openEnvelope(anchorA, `v2:${nonceHex}:${ctHex}:${flipHexByte(tagHex)}`), null, 'Bit-flipped Poly1305 tag rejected');
  assert.equal(biometric.openEnvelope(anchorB, sealed), null, 'Wrong anchor key rejected');
  assert.equal(biometric.openEnvelope(anchorA, `v1:${nonceHex}:${ctHex}:${tagHex}`), null, 'Non-v2 prefix rejected');
  assert.equal(biometric.openEnvelope(anchorA, `v2:zzzz:${ctHex}:${tagHex}`), null, 'Malformed hex rejected');
  assert.equal(biometric.openEnvelope(anchorA, 'v2:only:three'), null, 'Wrong segment count rejected');

  assert.equal(await biometric.biometricAvailable(), true);
  strong = false;
  assert.equal(await biometric.biometricAvailable(), false);
  strong = true;
  enrolled = false;
  await assert.rejects(biometric.saveBiometricSession('secret', Date.now() + 60000), /Cadastre/);
  enrolled = true;
  confirmed = false;
  await assert.rejects(biometric.saveBiometricSession('secret', Date.now() + 60000), /não confirmada/);
  assert.equal(writes.length, 0, 'Cancellation must never enable biometrics or persist a token');
  confirmed = true;

  // KeyPermanentlyInvalidated on writeProtectedKey triggers delete + retry and succeeds
  failWriteOnceInvalidated = true;
  deletes.length = 0;
  const csprngBeforeSave = csprngCalls;
  await biometric.saveBiometricSession('biometric-secret', Date.now() + 60000);
  assert.ok(csprngCalls > csprngBeforeSave, 'saveBiometricSession uses WebCrypto CSPRNG');
  assert.ok(deletes.some(k => k.includes('biometric-session')), 'KeyPermanentlyInvalidated deletes stale keystore entry before retrying');
  const sessionWrite = writes.find(item => item.key.includes('biometric-session'));
  assert.equal(sessionWrite.options.requireAuthentication, true);
  assert.equal(sessionWrite.options.keychainAccessible, 7);
  assert.equal(prompt.disableDeviceFallback, true);
  assert.equal(prompt.biometricsSecurityLevel, 'strong');
  assert.equal(await biometric.biometricEnabled(), true);
  assert.equal((await biometric.readBiometricSession()).token, 'biometric-secret');

  const protectedWritesBefore = writes.filter(item => item.key.includes('biometric-session')).length;
  await biometric.updateBiometricSession('renewed-biometric-secret', Date.now() + 120000);
  assert.equal(writes.filter(item => item.key.includes('biometric-session')).length, protectedWritesBefore, 'Sliding renewal uses hardware-anchored envelope without triggering a second OS prompt');
  assert.equal(await biometric.biometricEnabled(), true);
  assert.equal((await biometric.readBiometricSession()).token, 'renewed-biometric-secret');

  // LOCKOUT normalization and KeyPermanentlyInvalidated cleanup during readBiometricSession
  failReadError = new Error('BIOMETRIC_ERROR_LOCKOUT: Too many attempts');
  await assert.rejects(biometric.readBiometricSession(), /Muitas tentativas no sensor/);
  failReadError = new Error('KeyPermanentlyInvalidatedException: Key permanently invalidated');
  await assert.rejects(biometric.readBiometricSession(), /Biometria alterada ou sessão indisponível/);
  assert.equal(values.has(sessionWrite.key), false, 'KeyPermanentlyInvalidated during read cleans up invalidated key');
  assert.equal(biometric.hasUnlockedBiometricAnchor(), false);
  failReadError = null;

  await assert.rejects(biometric.readBiometricSession(), /alterada/);
  await biometric.clearBiometricSession();
  values.set(sessionWrite.key, JSON.stringify({token: 'expired', expiresAt: Date.now() - 1}));
  await assert.rejects(biometric.readBiometricSession(), /expirada/);
  await biometric.clearBiometricSession();
  assert.equal(await biometric.biometricEnabled(), false);

  const web = load('utils/biometricSession.web.ts');
  assert.equal(await web.biometricAvailable(), false);
  await assert.rejects(web.saveBiometricSession('never-store-on-web', Date.now() + 60000));

  const {createApiClient} = load('utils/apiClient.ts');
  let calls = 0;
  const client = createApiClient({
    baseUrl: 'https://private.example',
    token: state.getToken,
    requireSession: true,
    readRetryDelaysMs: [],
    fetch: async (_url, init) => {
      calls++;
      assert.equal(init.cache, 'no-store');
      return {ok: true, status: 200, text: async () => '{}'};
    },
  });
  await assert.rejects(client.json('/v1/dashboard'), /Entre/);
  assert.equal(calls, 0, 'Locked app must not send finance requests');
  await client.json('/v1/auth/status');
  assert.equal(calls, 1);
  state.activate('current-token', Date.now() + 60000);
  let finish, captured;
  const slow = createApiClient({
    baseUrl: 'https://private.example',
    token: state.getToken,
    readRetryDelaysMs: [],
    fetch: async (_url, init) => {
      captured = init;
      return new Promise(resolve => {
        finish = resolve;
      });
    },
  });
  const pending = slow.json('/v1/transactions/x', {method: 'PATCH', body: '{}'});
  state.lock();
  slow.reset();
  assert.equal(captured.signal.aborted, true, 'Lock cancels writes as well as reads');
  finish({ok: true, status: 200, text: async () => '{"sensitive":"late data"}'});
  await assert.rejects(pending, error => error.name === 'AbortError');
  let invalidations = 0;
  state.activate('revoked-token', Date.now() + 60000);
  const revoked = createApiClient({
    baseUrl: 'https://private.example',
    token: state.getToken,
    readRetryDelaysMs: [],
    onUnauthorized: () => {
      invalidations++;
      state.lock();
    },
    fetch: async () => ({ok: false, status: 401, text: async () => '{"detail":"Sessão encerrada"}'}),
  });
  await assert.rejects(revoked.json('/v1/auth/account'));
  assert.equal(invalidations, 1);
  assert.equal(state.getToken(), '');
  platform.OS = 'web';
  const desktop = load('utils/sessionClient.ts');
  assert.equal(desktop.API, 'http://127.0.0.1:8000', 'Local desktop must use the local server without tailnet dependency');
  await assert.rejects(desktop.api.json('/v1/dashboard'), /Entre/);

  completed = true;
  if (done) done();
  console.log('PASS: account privacy, TLS, WebCrypto CSPRNG, ChaCha20-Poly1305 AEAD, KeyPermanentlyInvalidated recovery, LOCKOUT, renewal, expiration, revocation and late-write blocking.');
})().catch(error => {
  completed = true;
  if (done) done();
  console.error(error);
  process.exitCode = 1;
});
