// Real React effects + the production AuthProvider/API client. Only native OS
// surfaces and HTTP responses are synthetic; no owner credentials or ledger.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const {EventEmitter} = require('node:events');
const events = new EventEmitter();
let clock = Date.now();
class TestDate extends Date {static now() {return clock;}}
const timers = new Map();
let timerId = 0;
function testTimeout(callback, delay) {
  if (delay < 60_000) return setTimeout(callback, delay);
  const id = {id: ++timerId}; timers.set(id, {callback, at: clock + delay}); return id;
}
function testClearTimeout(id) {if (!timers.delete(id)) clearTimeout(id);}
async function advance(ms, fire = false) {
  await act(async () => {
    clock += ms;
    if (fire) for (const [id, timer] of [...timers]) if (timer.at <= clock) {timers.delete(id); timer.callback();}
  });
}
const native = {Platform: {OS: 'android'}, AppState: {currentState: 'active',
  addEventListener: (name, listener) => {events.on(name, listener); return {remove: () => events.off(name, listener)};}},
  StyleSheet: {create: value => value}, Keyboard: {dismiss() {}}, BackHandler: {addEventListener: () => ({remove() {}})},
  ...Object.fromEntries(['ActivityIndicator', 'KeyboardAvoidingView', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name]))};
const cache = new Map();
const requests = [];
let pendingLogin, rejectLogin = false, privateMounts = 0, privateUnmounts = 0, navigatorMounts = 0, navigatorUnmounts = 0;
const mocks = {
  'react': React, 'react-native': native,
  'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView', SafeAreaProvider: 'SafeAreaProvider'},
  'expo-status-bar': {StatusBar: 'StatusBar'},
  'expo-router': {Stack: TestStack},
  '../utils/queueDb': {SQLiteProvider: ({children}) => children},
  '../components/AppDialogHost': {__esModule: true, default: () => null},
  '../utils/useScreenPrivacy': {useScreenPrivacy() {}},
  '../utils/appAlert': {clearDialogs() {}},
  '../utils/biometricSession': {removeLegacySession: async () => {}, biometricAvailable: async () => false, biometricEnabled: async () => false},
};
const providerSource = process.env.LOGIN_PROVIDER_SOURCE;
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  cache.set(file, module.exports);
  const source = providerSource && file.endsWith(path.join('components', 'AuthProvider.tsx')) ? providerSource : file;
  const code = ts.transpileModule(fs.readFileSync(source, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module, exports: module.exports, Error, Date: TestDate, URL, Headers, FormData, AbortController,
    setTimeout: testTimeout, clearTimeout: testClearTimeout, process: {env: {EXPO_PUBLIC_API_BASE_URL: 'https://test.invalid'}},
    fetch: async (url, init) => {
      const route = new URL(url).pathname;
      requests.push({route, token: init.headers.get('Authorization')});
      if (route === '/v1/auth/status') return new Response(JSON.stringify({configured: true, setup_required: false}));
      if (route === '/v1/auth/session') return new Promise(resolve => {
        pendingLogin = () => resolve(new Response(JSON.stringify(rejectLogin ? {detail: 'Usuário ou senha incorretos.'} : {
          access_token: 'synthetic-session', expires_in: 43200, account: {username: 'synthetic', display_name: 'Teste'}}), {status: rejectLogin ? 401 : 200}));
      });
      assert.equal(init.headers.get('Authorization'), 'Bearer synthetic-session');
      return new Response(JSON.stringify({ready: true}));
    }, require: name => {
      if (mocks[name]) return mocks[name];
      if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file), name), 'utf8'));
      if (name.startsWith('.')) {
        const source = path.resolve(path.dirname(file), name);
        return load(fs.existsSync(source + '.tsx') ? source + '.tsx' : source + '.ts');
      }
      throw new Error(`Unexpected module ${name}`);
    }}, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}
const {useAuth} = load('components/AuthProvider.tsx');
const {authState, api} = load('utils/sessionClient.ts');
const {withDevicePrompt} = load('utils/devicePrompt.ts');
const RootLayout = load('app/_layout.tsx').default;
const LoginScreen = load('app/login.tsx').default;
function PrivateScreen() {
  React.useEffect(() => {
    privateMounts++;
    void api.json('/v1/dashboard/overview').catch(() => {});
    return () => {privateUnmounts++;};
  }, []);
  return React.createElement('Text', null, 'Financeiro de teste');
}
// Test the real root layout/guards while replacing only Expo Router's native
// navigation surface. Native SQLite has its own SDK lifecycle regression.
function TestStack({children}) {
  React.useEffect(() => {navigatorMounts++; return () => {navigatorUnmounts++;};}, []);
  const available = React.Children.toArray(children).flatMap(group => group.props.guard ? React.Children.toArray(group.props.children) : []);
  const privateRoutes = available.filter(screen => screen.props.name !== 'login');
  assert.equal(privateRoutes.length > 0, useAuth().authenticated, 'All private routes follow the session guard');
  return React.createElement(privateRoutes.length ? PrivateScreen : LoginScreen);
}
TestStack.Protected = function Protected() {return null;};
TestStack.Screen = function Screen() {return null;};
let renderer;
const settle = async () => {await act(async () => {await new Promise(resolve => setTimeout(resolve, 10));});};
const emit = async (name, state) => {await act(async () => {
  if (name === 'change') native.AppState.currentState = state;
  events.emit(name, state);
});};
const input = (label, text) => act(async () => renderer.root.findByProps({accessibilityLabel: label}).props.onChangeText(text));
async function startLogin() {
  await input('Usuário', 'synthetic'); await input('Senha', 'synthetic-password');
  await act(async () => {
    const button = renderer.root.findAllByType('Pressable').find(node => node.findAllByType('Text').some(text => text.props.children === 'Entrar'));
    button.props.onPress();
  });
}
async function acceptedLogin() {await act(async () => pendingLogin()); await settle();}
(async () => {
  await act(async () => {renderer = create(React.createElement(RootLayout));});
  await settle();
  assert.equal(privateMounts, 0, 'Private routes must not mount before authentication');
  assert.equal(requests.filter(item => item.route.startsWith('/v1/dashboard')).length, 0);
  // The Android window loses focus when credential/system overlays open; the
  // Activity remains active. This is different from actually leaving the app.
  await startLogin();
  await emit('blur');
  await acceptedLogin();
  assert.equal(privateMounts, 0, 'Accepted login stays covered until the app regains focus');
  await emit('focus'); await settle();
  assert.equal(authState.unlocked(), true, 'A successful login must survive transient Android window focus loss');
  assert.equal(privateMounts, 1, 'Accepted login must mount the private screen');
  assert.equal(requests.filter(item => item.route.startsWith('/v1/dashboard')).length, 1);
  await emit('blur');
  assert.equal(authState.getToken(), '', 'No network access while the window is covered');
  assert.equal(authState.unlocked(), true, 'Window blur suspends, rather than discards, the accepted session');
  await emit('focus'); await settle();
  assert.equal(authState.getToken(), 'synthetic-session');
  assert.equal(privateUnmounts, 0, 'A system overlay must preserve the current private work');
  await emit('change', 'background');
  assert.equal(authState.unlocked(), true, 'Minimizing must preserve the accepted session');
  assert.equal(authState.getToken(), '', 'Background session must suspend all private networking');
  assert.equal(privateUnmounts, 0, 'Minimizing preserves the current screen and draft');
  await advance(14 * 60_000 + 59_999);
  await emit('change', 'active'); await settle();
  assert.equal(authState.getToken(), 'synthetic-session', 'Return just before fifteen minutes needs no fingerprint');
  assert.equal(privateMounts, 1);
  let closePicker;
  let picker;
  await act(async () => {picker = withDevicePrompt(() => new Promise(resolve => {closePicker = resolve;}));});
  await emit('change', 'background');
  await advance(3 * 60_000, true);
  assert.equal(authState.unlocked(), true, 'A PDF picker open for over two minutes must preserve the session');
  await act(async () => closePicker({uri: 'content://synthetic/statement.pdf'}));
  await picker;
  assert.equal(authState.unlocked(), true, 'Picker result arriving before foreground must not discard the capture screen');
  await emit('change', 'active'); await settle();
  assert.equal(privateUnmounts, 0);
  await emit('change', 'background');
  await advance(10 * 60_000);
  await emit('change', 'background');
  await advance(5 * 60_000);
  // Android may freeze every timer while suspended: foreground must still lock.
  await emit('change', 'active'); await settle();
  assert.equal(authState.unlocked(), false, 'Return at fifteen minutes locks even when timers were frozen');
  assert.equal(privateUnmounts, 1);
  assert.equal(authState.getToken(), '');
  await startLogin(); await acceptedLogin();
  await emit('change', 'background'); await advance(15 * 60_000, true);
  assert.equal(authState.unlocked(), false, 'A running background timer also locks at fifteen minutes');
  await emit('change', 'active'); await settle();
  rejectLogin = true; await startLogin(); await acceptedLogin();
  assert.equal(authState.unlocked(), false, 'Wrong credentials never activate a session');
  assert.ok(renderer.root.findAllByType('Text').some(node => node.props.children === 'Usuário ou senha incorretos.'));
  rejectLogin = false; await startLogin();
  await emit('change', 'background'); await acceptedLogin();
  await emit('change', 'active'); await settle();
  assert.equal(authState.unlocked(), false, 'A response received after actual backgrounding never unlocks');
  assert.equal(privateMounts, 2);
  assert.equal(navigatorMounts, 1, 'The root navigator must remain mounted across login and locking');
  assert.equal(navigatorUnmounts, 0);
  await act(async () => authState.activate('synthetic-session', TestDate.now() + 60_000));
  await emit('change', 'background'); await advance(60_000, true);
  assert.equal(authState.unlocked(), false, 'Server session expiry is respected inside the grace period');
  await emit('change', 'active');
  await act(async () => authState.activate('synthetic-session', TestDate.now() + 60_000));
  await emit('change', 'background'); await act(async () => authState.lock());
  await emit('change', 'active');
  assert.equal(authState.unlocked(), false, 'Explicit lock is never undone by a short background return');
  await act(async () => renderer.unmount());
  assert.equal(authState.unlocked(), false);
  assert.equal(events.eventNames().length, 0, 'No leaked native listeners');
  assert.equal(timers.size, 0, 'No leaked background/expiry timers');
  console.log('PASS: login → private request, 15-minute background grace, preserved screen, frozen-timer deadline, repeated background, expiry, wrong password and late login response');
})().catch(async error => {console.error(error); process.exitCode = 1; await act(async () => renderer?.unmount());});
