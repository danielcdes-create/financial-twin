// Production provider, secure-session reader and HTTP client; OS/HTTP are synthetic.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
const {EventEmitter} = require('node:events');
global.IS_REACT_ACT_ENVIRONMENT = true;
const events = new EventEmitter(), cache = new Map();
let renderer, enabled = true, supported = true, configured = true, promptCount = 0, resolvePrompt, rejectPrompt;
let saved = {token: 'synthetic-biometric', expiresAt: Date.now() + 3_600_000}, revoked = false, validations = 0, allows = 0;
let current;
const requests = [];
const native = {Platform: {OS: 'android'}, AppState: {currentState: 'active', addEventListener: (event, listener) => {
  events.on(event, listener); return {remove: () => events.off(event, listener)};
}}, Keyboard: {dismiss() {}}, StyleSheet: {create: value => value},
  ...Object.fromEntries(['ActivityIndicator', 'KeyboardAvoidingView', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name]))};
const store = {WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7, canUseBiometricAuthentication: () => supported,
  getItemAsync: async (key, options) => {
    if (key.includes('enabled')) return enabled ? '1' : null;
    assert.equal(options.requireAuthentication, true, 'Automatic entry must still require OS authentication');
    promptCount++;
    return new Promise((resolve, reject) => {resolvePrompt = () => resolve(saved ? JSON.stringify(saved) : null); rejectPrompt = reject;});
  }, deleteItemAsync: async () => {}, setItemAsync: async () => {throw Error('Unexpected session persistence');}};
const mocks = {'react': React, 'react-native': native, 'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView'},
  'expo-secure-store': store, 'expo-local-authentication': {SecurityLevel: {BIOMETRIC_STRONG: 3}, hasHardwareAsync: async () => true,
    isEnrolledAsync: async () => true, getEnrolledLevelAsync: async () => 3},
  'expo-screen-capture': {allowScreenCaptureAsync: async () => {allows++;},
    preventScreenCaptureAsync: () => {throw Error('Screenshots must be allowed');}},
  '../utils/appAlert': {clearDialogs() {}}};
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  const source = process.env.BIOMETRIC_PROVIDER_SOURCE && file.endsWith(path.join('components', 'AuthProvider.tsx')) ? process.env.BIOMETRIC_PROVIDER_SOURCE : file;
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(source, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText,
  {module, exports: module.exports, console, Date, Error, URL, Headers, FormData, AbortController, setTimeout, clearTimeout,
    process: {env: {EXPO_PUBLIC_API_BASE_URL: 'https://synthetic.invalid'}},
    fetch: async (url, init) => {
      const route = new URL(url).pathname; requests.push(route);
      if (route === '/v1/auth/status') return new Response(JSON.stringify({configured, setup_required: !configured, setup_available: true}));
      assert.equal(route, '/v1/auth/account'); validations++;
      assert.equal(init.headers.get('Authorization'), 'Bearer synthetic-biometric');
      return new Response(JSON.stringify(revoked ? {detail: 'Sessão encerrada'} : {username: 'synthetic', display_name: 'Teste'}), {status: revoked ? 401 : 200});
    }, require: name => {
      if (mocks[name]) return mocks[name];
      if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file), name), 'utf8'));
      if (name.startsWith('.')) {const target = path.resolve(path.dirname(file), name);return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');}
      throw Error(`Unexpected module ${name}`);
    }});
  cache.set(file, module.exports); return module.exports;
}
const {default: Provider, useAuth} = load('components/AuthProvider.tsx');
const {authState} = load('utils/sessionClient.ts');
function Probe() {
  const auth = useAuth();
  React.useEffect(() => {current = auth;}, [auth]);
  return auth.authenticated ? React.createElement('Text', null, 'Private test screen') : auth.loginScreen;
}
const settle = async () => act(async () => {await new Promise(resolve => setTimeout(resolve, 15));});
const emit = async (event, state) => act(async () => {if (event === 'change') native.AppState.currentState = state; events.emit(event, state);});
async function mount() {
  promptCount = 0; validations = 0; allows = 0; requests.length = 0;
  await act(async () => {renderer = create(React.createElement(Provider, null, React.createElement(Probe)));}); await settle();
}
async function unmount() {await act(async () => renderer.unmount());renderer = null;}
const press = async title => act(async () => {
  const button = renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === title || node.findAllByType('Text').some(text => text.props.children === title));
  assert.ok(button, title); assert.ok(!button.props.disabled); button.props.onPress();
});
(async () => {
  const checkCapturePolicy = directory => {
    for (const item of fs.readdirSync(directory, {withFileTypes: true})) {
      const file = path.join(directory, item.name);
      if (item.isDirectory()) checkCapturePolicy(file);
      else if (/\.(tsx?|jsx?|kt|java)$/.test(item.name)) {
        assert.doesNotMatch(fs.readFileSync(file, 'utf8'), /preventScreenCapture|usePreventScreenCapture|FLAG_SECURE/, `Screenshot blocking returned in ${file}`);
      }
    }
  };
  for (const directory of ['components', 'utils', 'app', 'modules', 'plugins', 'android/app/src/main/java']) {
    // Expo's generated Android project is intentionally absent from a clean source checkout.
    // Inspect it when present, while keeping source-only validation reproducible.
    if (fs.existsSync(directory)) checkCapturePolicy(directory);
  }
  await mount();
  assert.equal(promptCount, 1, 'Enabled biometrics must prompt automatically on launch');
  assert.equal(renderer.root.findAllByType('TextInput').length, 0, 'Fingerprint entry precedes the password form');
  assert.equal(validations, 0, 'No private request before OS authentication');
  assert.equal(allows, 1, 'Android screenshots are allowed on launch');
  await emit('blur'); await emit('change', 'inactive');
  await act(async () => resolvePrompt()); await settle();
  assert.equal(authState.unlocked(), false, 'OS result alone cannot uncover the app under its prompt');
  await emit('change', 'active'); await emit('focus'); await settle();
  assert.equal(authState.unlocked(), true); assert.equal(current.account.username, 'synthetic');
  assert.equal(validations, 1); assert.equal(promptCount, 1, 'Prompt focus events must not duplicate authentication');
  assert.equal(allows, 2, 'Android screenshots are reallowed on foreground');
  await emit('change', 'background'); await emit('change', 'active'); await settle();
  assert.equal(promptCount, 1, 'Returning inside the 15-minute grace needs no new prompt');
  await act(async () => current.lock()); await settle();
  assert.equal(promptCount, 2, 'A new locked session can automatically request biometrics once');
  await act(async () => rejectPrompt(Error('Biometria cancelada'))); await settle();
  assert.equal(authState.unlocked(), false); assert.equal(renderer.root.findAllByType('TextInput').length, 2);
  await emit('blur'); await emit('focus'); await settle();
  assert.equal(promptCount, 2, 'Cancellation must not trigger an automatic retry loop');
  await press('Desbloquear com biometria'); await act(async () => resolvePrompt()); await settle();
  assert.equal(authState.unlocked(), true, 'Explicit retry works after cancellation');
  await act(async () => current.lock()); await settle();
  assert.equal(promptCount, 4, 'Choosing password after cancellation applies only to that locked cycle');
  await act(async () => resolvePrompt()); await settle();
  await unmount();

  for (const [name, value, serverRevoked] of [['expired', {token: 'synthetic-biometric', expiresAt: Date.now() - 1}, false], ['invalidated', null, false], ['revoked', {token: 'synthetic-biometric', expiresAt: Date.now() + 60_000}, true]]) {
    saved = value; revoked = serverRevoked; await mount();
    await act(async () => resolvePrompt()); await settle();
    assert.equal(authState.unlocked(), false, name); assert.equal(renderer.root.findAllByType('TextInput').length, 2, name);
    assert.equal(validations, serverRevoked ? 1 : 0, name); assert.equal(promptCount, 1, name);
    await unmount();
  }
  revoked = false; enabled = false; await mount();
  assert.equal(promptCount, 0, 'No opt-in means password entry'); assert.equal(renderer.root.findAllByType('TextInput').length, 2); await unmount();
  enabled = true; supported = false; await mount(); assert.equal(promptCount, 0, 'Unavailable biometrics never prompt'); await unmount();
  supported = true; configured = false; await mount(); assert.equal(promptCount, 0, 'Setup never prompts'); await unmount(); configured = true;
  native.AppState.currentState = 'background'; await mount(); assert.equal(promptCount, 0, 'No authentication prompt while backgrounded');
  await emit('change', 'active'); await settle(); assert.equal(promptCount, 1); await act(async () => rejectPrompt(Error('Cancel'))); await unmount();
  console.log('PASS: automatic protected biometric entry, single OS prompt, cancellation/password fallback, expiry, revocation, foreground and screenshot allowance.');
})().catch(async error => {console.error(error);if (renderer) await unmount();process.exitCode = 1;});
