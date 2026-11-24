// P0 runtime contract suite for biometricSession.ts CSPRNG/AEAD tampering (BUG-05)
// and AuthProvider.tsx setup, sliding X-Session-Token renewal, logout, and PDF CPF preservation (BUG-11).
const assert = require('node:assert/strict');
const nodeCrypto = require('node:crypto');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');

global.IS_REACT_ACT_ENVIRONMENT = true;
const done = typeof globalThis.__expectAsyncCompletion === 'function'
  ? globalThis.__expectAsyncCompletion('test-biometric-crypto-and-auth')
  : null;
let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'test-biometric-crypto-and-auth async suite did not finish');
});

function createBiometricHarness({provideCrypto = true, os = 'android'} = {}) {
  const storeMap = new Map();
  let protectedReads = 0;
  let protectedWrites = 0;
  let mathRandomCalled = false;

  const secureStore = {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7,
    canUseBiometricAuthentication: () => true,
    getItemAsync: async (key, opts) => {
      if (key.includes('biometric-session')) {
        assert.equal(opts?.requireAuthentication, true);
        protectedReads++;
      }
      return storeMap.has(key) ? storeMap.get(key) : null;
    },
    setItemAsync: async (key, value, opts) => {
      if (key.includes('biometric-session')) {
        assert.equal(opts?.requireAuthentication, true);
        protectedWrites++;
      }
      storeMap.set(key, value);
    },
    deleteItemAsync: async key => {
      storeMap.delete(key);
    },
  };

  const localAuth = {
    SecurityLevel: {BIOMETRIC_STRONG: 3},
    hasHardwareAsync: async () => true,
    isEnrolledAsync: async () => true,
    getEnrolledLevelAsync: async () => 3,
    authenticateAsync: async () => ({success: true}),
  };

  const trackedMath = Object.create(Math);
  trackedMath.random = () => {
    mathRandomCalled = true;
    return Math.random();
  };

  const file = path.resolve('utils/biometricSession.ts');
  const mod = {exports: {}};
  const sandbox = {
    module: mod,
    exports: mod.exports,
    Date,
    Math: trackedMath,
    Number,
    String,
    Boolean,
    Error,
    Uint8Array,
    Uint32Array,
    BigInt,
    JSON,
    Promise,
    __DEV__: false,
    process: {env: {NODE_ENV: 'production'}},
    ...(provideCrypto ? {crypto: nodeCrypto.webcrypto} : {}),
    require: name => {
      if (name === 'react-native') return {Platform: {OS: os}};
      if (name === 'expo-secure-store') return secureStore;
      if (name === 'expo-local-authentication') return localAuth;
      throw new Error(`Unexpected dependency in biometricSession: ${name}`);
    },
  };
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  vm.runInNewContext(compiled, sandbox, {filename: file});

  return {
    biometric: mod.exports,
    storeMap,
    getProtectedReads: () => protectedReads,
    getProtectedWrites: () => protectedWrites,
    wasMathRandomCalled: () => mathRandomCalled,
  };
}

function createAuthProviderHarness() {
  const events = new EventEmitter();
  const storeMap = new Map();
  let protectedReads = 0;
  let protectedWrites = 0;
  const httpCalls = [];
  let statusBody = {configured: true, setup_required: false, setup_available: false, legacy_available: false};
  let nextAccountHeaders = {};
  let logoutShouldFailNetwork = false;

  const native = {
    Platform: {OS: 'android'},
    AppState: {
      currentState: 'active',
      addEventListener: (event, listener) => {
        events.on(event, listener);
        return {remove: () => events.off(event, listener)};
      },
    },
    Keyboard: {dismiss() {}},
    BackHandler: {addEventListener: () => ({remove() {}})},
    Alert: {alert() {}},
    StyleSheet: {create: v => v},
    ...Object.fromEntries(['ActivityIndicator', 'KeyboardAvoidingView', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(n => [n, n])),
  };

  const secureStore = {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7,
    canUseBiometricAuthentication: () => true,
    getItemAsync: async (key, opts) => {
      if (key.includes('biometric-session')) {
        assert.equal(opts?.requireAuthentication, true);
        protectedReads++;
      }
      return storeMap.has(key) ? storeMap.get(key) : null;
    },
    setItemAsync: async (key, value, opts) => {
      if (key.includes('biometric-session')) {
        assert.equal(opts?.requireAuthentication, true);
        protectedWrites++;
      }
      storeMap.set(key, value);
    },
    deleteItemAsync: async key => {
      storeMap.delete(key);
    },
  };

  const localAuth = {
    SecurityLevel: {BIOMETRIC_STRONG: 3},
    hasHardwareAsync: async () => true,
    isEnrolledAsync: async () => true,
    getEnrolledLevelAsync: async () => 3,
    authenticateAsync: async () => ({success: true}),
  };

  const mocks = {
    react: React,
    'react-native': native,
    'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView'},
    'expo-secure-store': secureStore,
    'expo-local-authentication': localAuth,
    'expo-screen-capture': {allowScreenCaptureAsync: async () => {}},
  };

  const cache = new Map();
  function load(file) {
    file = path.resolve(file);
    if (cache.has(file)) return cache.get(file);
    const mod = {exports: {}};
    cache.set(file, mod.exports);
    const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.React,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
      },
    }).outputText;
    vm.runInNewContext(
      compiled,
      {
        module: mod,
        exports: mod.exports,
        console,
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
        URL,
        URLSearchParams,
        Headers,
        FormData,
        AbortController,
        Symbol,
        Promise,
        crypto: nodeCrypto.webcrypto,
        setTimeout,
        clearTimeout,
        process: {env: {EXPO_PUBLIC_API_BASE_URL: 'https://private.example', NODE_ENV: 'production'}},
        fetch: async (input, init = {}) => {
          const urlStr = typeof input === 'string' ? input : input.url;
          const pathname = new URL(urlStr).pathname;
          const method = init.method || 'GET';
          const bodyObj = typeof init.body === 'string' && init.body ? JSON.parse(init.body) : null;
          const authHeader = init.headers?.get ? init.headers.get('Authorization') : init.headers?.Authorization;
          httpCalls.push({pathname, method, body: bodyObj, auth: authHeader});

          if (pathname === '/v1/auth/status') {
            return new Response(JSON.stringify(statusBody), {status: 200});
          }
          if (pathname === '/v1/auth/setup') {
            return new Response(
              JSON.stringify({
                access_token: 'setup-session-token',
                expires_in: 3600,
                account: {username: bodyObj.username, display_name: bodyObj.display_name || 'Owner'},
              }),
              {status: 200},
            );
          }
          if (pathname === '/v1/auth/session') {
            return new Response(
              JSON.stringify({
                access_token: 'password-session-token',
                expires_in: 3600,
                account: {username: bodyObj.username || 'daniel', display_name: 'Daniel'},
              }),
              {status: 200},
            );
          }
          if (pathname === '/v1/auth/biometric') {
            return new Response(JSON.stringify({access_token: 'bio-initial-token', expires_in: 86400}), {status: 200});
          }
          if (pathname === '/v1/auth/account') {
            return new Response(JSON.stringify({username: 'daniel', display_name: 'Daniel'}), {
              status: 200,
              headers: nextAccountHeaders,
            });
          }
          if (pathname === '/v1/auth/logout' || pathname === '/v1/auth/logout-all') {
            if (logoutShouldFailNetwork) throw new TypeError('Network request failed');
            return new Response(JSON.stringify({ok: true}), {status: 200});
          }
          return new Response(JSON.stringify({}), {status: 200});
        },
        require: name => {
          if (mocks[name]) return mocks[name];
          if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file), name), 'utf8'));
          if (!name.startsWith('.')) return require(name);
          const target = path.resolve(path.dirname(file), name);
          return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
        },
      },
      {filename: file},
    );
    cache.set(file, mod.exports);
    return mod.exports;
  }

  return {
    load,
    storeMap,
    httpCalls,
    setStatusBody: next => { statusBody = next; },
    setNextAccountHeaders: next => { nextAccountHeaders = next; },
    setLogoutNetworkFailure: flag => { logoutShouldFailNetwork = flag; },
    getProtectedReads: () => protectedReads,
    getProtectedWrites: () => protectedWrites,
    resetProtectedCounters: () => { protectedReads = 0; protectedWrites = 0; },
  };
}

const settle = (ms = 25) => act(async () => { await new Promise(resolve => setTimeout(resolve, ms)); });

(async () => {
  const contractFailures = [];

  // 1. AuthProvider first-access setup validation + POST /v1/auth/setup + logout(false/true) network-error finally wipe
  {
    const h = createAuthProviderHarness();
    h.setStatusBody({configured: true, setup_required: true, setup_available: true, legacy_available: false});
    const {default: AuthProvider, useAuth} = h.load('components/AuthProvider.tsx');
    const {authState} = h.load('utils/sessionClient.ts');
    const biometricMod = h.load('utils/biometricSession.ts');

    let latestAuth = null;
    function Probe() {
      const auth = useAuth();
      React.useEffect(() => { latestAuth = auth; }, [auth]);
      return auth.authenticated ? React.createElement('Text', null, 'Authenticated Screen') : auth.loginScreen;
    }

    let renderer;
    await act(async () => {
      renderer = create(React.createElement(AuthProvider, null, React.createElement(Probe)));
    });
    await settle();

    const setField = async (label, value) => {
      const input = renderer.root.findByProps({accessibilityLabel: label});
      await act(async () => input.props.onChangeText(value));
    };
    const pressTitle = async title => {
      const btn = renderer.root.findAllByType('Pressable').find(
        n => n.props.accessibilityLabel === title || n.findAllByType('Text').some(t => t.props.children === title),
      );
      assert.ok(btn, `Button "${title}" should exist`);
      await act(async () => btn.props.onPress());
      await settle();
    };
    const renderedText = () =>
      renderer.root.findAllByType('Text').map(n => [].concat(n.props.children).flat(Infinity).filter(Boolean).join('')).join('\n');

    // Short password (<12 chars) rejected locally without POST /v1/auth/setup
    await setField('Nome', 'Daniel');
    await setField('Usuário', 'Daniel');
    await setField('Senha', 'curta123');
    await setField('Repita a senha', 'curta123');
    await setField('Código de primeiro acesso', 'setup-code-1');
    await pressTitle('Criar meu acesso');
    assert.match(renderedText(), /pelo menos 12 caracteres/);
    assert.equal(h.httpCalls.filter(c => c.pathname === '/v1/auth/setup').length, 0);

    // Mismatched confirmation rejected locally without POST /v1/auth/setup
    await setField('Senha', 'SenhaForteCom14!');
    await setField('Repita a senha', 'OutraSenhaCom14!');
    await pressTitle('Criar meu acesso');
    assert.match(renderedText(), /repita a mesma senha/);
    assert.equal(h.httpCalls.filter(c => c.pathname === '/v1/auth/setup').length, 0);

    // Valid setup calls POST /v1/auth/setup and unlocks session
    await setField('Senha', 'SenhaForteCom14!');
    await setField('Repita a senha', 'SenhaForteCom14!');
    await setField('Código de primeiro acesso', '  token-inicial-99 ');
    await pressTitle('Criar meu acesso');
    const setupCalls = h.httpCalls.filter(c => c.pathname === '/v1/auth/setup');
    assert.equal(setupCalls.length, 1);
    assert.equal(setupCalls[0].method, 'POST');
    assert.equal(setupCalls[0].body.username, 'daniel');
    assert.equal(setupCalls[0].body.display_name, 'Daniel');
    assert.equal(setupCalls[0].body.setup_token, 'token-inicial-99');
    assert.equal(authState.unlocked(), true);

    // Enable biometrics, seed CPF, and test sliding renewal via X-Session-Token + X-Session-Expires-In on unlockBiometric
    await act(async () => { await latestAuth.toggleBiometric(); });
    assert.equal(latestAuth.biometricOn, true);
    assert.equal(h.storeMap.get('financial-twin-biometric-enabled-v1'), '1');

    // Lock and unlock via biometric with X-Session-Token sliding renewal
    h.resetProtectedCounters();
    h.setNextAccountHeaders({
      'X-Session-Token': 'sliding-renewed-jwt',
      'X-Session-Expires-In': '172800',
    });
    await act(async () => { latestAuth.lock(); });
    await settle(40);
    assert.equal(authState.unlocked(), true, 'Automatic biometric unlock succeeds');
    assert.equal(authState.getToken(), 'sliding-renewed-jwt', 'Sliding renewal activates X-Session-Token');
    assert.equal(h.getProtectedReads(), 1, 'Biometric unlock prompted hardware keystore once');
    assert.equal(h.getProtectedWrites(), 0, 'Sliding X-Session-Token renewal updates v2 envelope without a 2nd hardware write prompt');
    const sealedEnabled = h.storeMap.get('financial-twin-biometric-enabled-v1');
    assert.match(String(sealedEnabled), /^1:v2:[0-9a-f]{24}:[0-9a-f]+:[0-9a-f]{32}$/, 'ENABLED stores 1:v2:... sealed envelope');
    assert.equal((await biometricMod.readBiometricSession()).token, 'sliding-renewed-jwt');
    h.setNextAccountHeaders({});

    // logout(false) and logout(true) MUST delete financial-twin-pdf-cpf and lock in finally even when HTTP rejects
    h.storeMap.set('financial-twin-pdf-cpf', '12345678901');
    h.setLogoutNetworkFailure(true);
    await assert.rejects(
      async () => { await act(async () => { await latestAuth.logout(false); }); },
      /Network request failed/,
    );
    assert.equal(authState.unlocked(), false, 'logout(false) locks session in finally on network failure');
    assert.equal(h.storeMap.has('financial-twin-pdf-cpf'), false, 'logout(false) wipes financial-twin-pdf-cpf in finally');
    assert.ok(h.httpCalls.some(c => c.pathname === '/v1/auth/logout' && c.method === 'POST'));

    await act(async () => {
      await latestAuth.replaceSession({
        access_token: 'relogged-token',
        expires_in: 3600,
        account: {username: 'daniel', display_name: 'Daniel'},
      });
    });
    h.storeMap.set('financial-twin-pdf-cpf', '12345678901');
    await assert.rejects(
      async () => { await act(async () => { await latestAuth.logout(true); }); },
      /Network request failed/,
    );
    assert.equal(authState.unlocked(), false, 'logout(true) locks session in finally on network failure');
    assert.equal(h.storeMap.has('financial-twin-pdf-cpf'), false, 'logout(true) wipes financial-twin-pdf-cpf in finally');
    assert.ok(h.httpCalls.some(c => c.pathname === '/v1/auth/logout-all' && c.method === 'POST'));
    h.setLogoutNetworkFailure(false);

    // BUG-11 Contract A: Disabling biometrics via toggleBiometric() MUST preserve financial-twin-pdf-cpf
    await act(async () => {
      await latestAuth.replaceSession({
        access_token: 'session-before-bio-off',
        expires_in: 3600,
        account: {username: 'daniel', display_name: 'Daniel'},
      });
    });
    await act(async () => { await latestAuth.toggleBiometric(); });
    assert.equal(latestAuth.biometricOn, true);
    h.storeMap.set('financial-twin-pdf-cpf', '12345678901');
    await act(async () => { await latestAuth.toggleBiometric(); });
    assert.equal(latestAuth.biometricOn, false);
    try {
      assert.equal(
        h.storeMap.get('financial-twin-pdf-cpf'),
        '12345678901',
        'BUG-11: Disabling biometrics (toggleBiometric) must preserve financial-twin-pdf-cpf in SecureStore',
      );
    } catch (err) {
      contractFailures.push(err.message);
    }

    await act(async () => renderer.unmount());
  }

  // 2. BUG-11 Contract B: Password login (replaceSession) when biometric anchor is locked MUST preserve financial-twin-pdf-cpf
  {
    const h = createAuthProviderHarness();
    // Pre-seed biometric session as enabled on disk, with NO unlocked inMemoryAnchor in this fresh VM instance
    h.storeMap.set('financial-twin-biometric-enabled-v1', '1');
    h.storeMap.set(
      'financial-twin-biometric-session-v1',
      JSON.stringify({
        token: 'old-bio-token',
        expiresAt: Date.now() + 3600_000,
        aeadKey: '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff',
      }),
    );
    h.storeMap.set('financial-twin-pdf-cpf', '98765432100');

    // Mount on web/password mode or cancel initial prompt -> test replaceSession while biometricOn===true & hasUnlockedBiometricAnchor()===false
    const {default: AuthProvider, useAuth} = h.load('components/AuthProvider.tsx');
    const biometricMod = h.load('utils/biometricSession.ts');
    assert.equal(biometricMod.hasUnlockedBiometricAnchor(), false, 'Fresh session starts with locked hardware anchor');

    // Temporarily fail protected read as canceled so user lands on password form with biometricOn=true & anchor locked
    const origGet = h.storeMap.get.bind(h.storeMap);
    let cancelOnce = true;
    h.storeMap.get = key => {
      if (cancelOnce && key.includes('biometric-session')) {
        cancelOnce = false;
        throw new Error('User canceled the authentication');
      }
      return origGet(key);
    };

    let latestAuth = null;
    function Probe() {
      const auth = useAuth();
      React.useEffect(() => { latestAuth = auth; }, [auth]);
      return auth.authenticated ? React.createElement('Text', null, 'In') : auth.loginScreen;
    }
    let renderer;
    await act(async () => {
      renderer = create(React.createElement(AuthProvider, null, React.createElement(Probe)));
    });
    await settle(40);
    assert.equal(latestAuth.biometricOn, true);
    assert.equal(biometricMod.hasUnlockedBiometricAnchor(), false);

    await act(async () => {
      await latestAuth.replaceSession({
        access_token: 'fresh-password-token',
        expires_in: 3600,
        account: {username: 'daniel', display_name: 'Daniel'},
      });
    });
    try {
      assert.equal(
        origGet('financial-twin-pdf-cpf'),
        '98765432100',
        'BUG-11: Password login (replaceSession) when biometric anchor is locked must preserve financial-twin-pdf-cpf',
      );
    } catch (err) {
      contractFailures.push(err.message);
    }
    await act(async () => renderer.unmount());
  }

  // 3. BUG-05 Contract A: Without crypto.getRandomValues in production, sealEnvelope & saveBiometricSession MUST refuse Math.random()
  {
    const noCrypto = createBiometricHarness({provideCrypto: false, os: 'android'});
    const anchor = JSON.stringify({aeadKey: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'});
    try {
      assert.throws(
        () => noCrypto.biometric.sealEnvelope(anchor, JSON.stringify({token: 't', expiresAt: Date.now() + 60_000})),
        'BUG-05: sealEnvelope must throw when crypto.getRandomValues is absent instead of using Math.random()',
      );
    } catch (err) {
      contractFailures.push(err.message);
    }
    try {
      await assert.rejects(
        () => noCrypto.biometric.saveBiometricSession('secret-token', Date.now() + 60_000),
        'BUG-05: saveBiometricSession must reject when crypto.getRandomValues is absent instead of using Math.random()',
      );
      assert.equal(noCrypto.wasMathRandomCalled(), false, 'BUG-05: Math.random() must never be used for cryptographic key/nonce generation');
    } catch (err) {
      contractFailures.push(err.message);
    }
  }

  // 4. BUG-05 Contract B: Corrupted/tampered cachedEnvelope ('1:v2:...') MUST cause readBiometricSession() to throw (never fall back to older KEY token)
  {
    const h = createBiometricHarness({provideCrypto: true, os: 'android'});
    await h.biometric.saveBiometricSession('older-anchor-token', Date.now() + 60_000);
    await h.biometric.updateBiometricSession('newer-envelope-token', Date.now() + 120_000);

    const enabledVal = h.storeMap.get('financial-twin-biometric-enabled-v1');
    assert.ok(typeof enabledVal === 'string' && enabledVal.startsWith('1:v2:'));
    // Corrupt the last hex byte of the Poly1305 authentication tag
    const tamperedLastByte = (parseInt(enabledVal.slice(-2), 16) ^ 0xff).toString(16).padStart(2, '0');
    const corruptedEnabled = enabledVal.slice(0, -2) + tamperedLastByte;
    h.storeMap.set('financial-twin-biometric-enabled-v1', corruptedEnabled);

    assert.equal(await h.biometric.biometricEnabled(), true, 'Loads tampered cachedEnvelope');
    try {
      await assert.rejects(
        () => h.biometric.readBiometricSession(),
        'BUG-05: readBiometricSession() must throw when cachedEnvelope tag/ciphertext is corrupted instead of falling back to older token in KEY',
      );
    } catch (err) {
      contractFailures.push(err.message);
    }
  }

  assert.equal(
    contractFailures.length,
    0,
    `Biometric crypto & AuthProvider security contract violations:\n- ${contractFailures.join('\n- ')}`,
  );

  completed = true;
  if (done) done();
  console.log('PASS biometric CSPRNG, AEAD integrity, AuthProvider setup, sliding renewal, logout, and CPF preservation contracts.');
})().catch(error => {
  completed = true;
  if (done) done();
  console.error(error);
  process.exitCode = 1;
});
