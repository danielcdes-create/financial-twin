// Production AuthProvider, ControlledChoice, overlay host, batch review and session
// client. Native window-focus events and HTTP/SQLite are synthetic; no real ledger.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
const {EventEmitter} = require('node:events');
global.IS_REACT_ACT_ENVIRONMENT = true;
const mobile = process.cwd(), cache = new Map(), events = new EventEmitter();
const requests = [], drafts = new Map(), depthErrors = [], backHandlers = new Set();
const stats = {modalOpens: 0, modalCloses: 0, blur: 0, focus: 0, canceledRequests: 0, suspensions: 0, dismissKeyboard: 0};
let renderer, pendingRead, resolvePendingRead, mounts = 0, unmounts = 0;
const originalConsoleError = console.error;
console.error = (...args) => {
  if (/Maximum update depth|Too many re-renders/i.test(args.map(String).join(' '))) depthErrors.push(args.map(String).join(' '));
  else originalConsoleError(...args);
};
function nativeFocus(name) {stats[name]++; events.emit(name);}
// Dialog takes focus from the Activity, without changing AppState.currentState.
// This does not invent any display:none-triggered native unmounting behavior.
function AndroidModal({visible = true, children, ...props}) {
  const shownRef = React.useRef(false);
  React.useEffect(() => {
    if (visible && !shownRef.current) {shownRef.current = true; stats.modalOpens++; nativeFocus('blur');}
    else if (!visible && shownRef.current) {shownRef.current = false; stats.modalCloses++; nativeFocus('focus');}
  }, [visible]);
  React.useEffect(() => () => {if (shownRef.current) {shownRef.current = false; stats.modalCloses++; nativeFocus('focus');}}, []);
  return visible ? React.createElement('AndroidModal', props, children) : null;
}
const native = {Platform: {OS: 'android'}, AppState: {currentState: 'active',
  addEventListener: (name, fn) => {events.on(name, fn); return {remove: () => events.off(name, fn)};}},
  BackHandler: {addEventListener: (_name, fn) => {backHandlers.add(fn); return {remove: () => backHandlers.delete(fn)};}},
  Keyboard: {dismiss: () => {stats.dismissKeyboard++;}},
  StyleSheet: {create: value => value, absoluteFillObject: {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0}},
  Modal: AndroidModal,
  FlatList: ({data, renderItem, keyExtractor, ListEmptyComponent}) => React.createElement('FlatList', null,
    data.length ? data.map((item, index) => React.createElement(React.Fragment, {key: keyExtractor ? keyExtractor(item, index) : index}, renderItem({item, index}))) : ListEmptyComponent),
  useWindowDimensions: () => ({width: 390, height: 844, scale: 1, fontScale: 1}),
  ...Object.fromEntries(['ActivityIndicator', 'KeyboardAvoidingView', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name]))};
const db = {getAllAsync: async (_sql, id) => drafts.has(id) ? [{state: drafts.get(id)}] : [],
  runAsync: async (sql, id, state) => {if (sql.startsWith('DELETE')) drafts.delete(id); else drafts.set(id, state);}};
const mocks = {'react': React, 'react-native': native,
  'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView'},
  '../utils/useScreenPrivacy': {useScreenPrivacy() {}},
  '../utils/queueDb': {useSQLiteContext: () => db},
  '../utils/appAlert': {clearDialogs() {}, Alert: {alert: () => {throw new Error('Unexpected alert');}}},
  '../utils/biometricSession': {removeLegacySession: async () => {}, biometricAvailable: async () => false, biometricEnabled: async () => false}};
const sha = 'a'.repeat(64), batchId = 'choice-focus-synthetic';
const sourceRows = [1, 2].map(row_id => ({row_id, date: '2026-10-01', amount: '-12.59', description: `Loja ${row_id}`, kind: 'expense', bank_ref: null}));
const batch = {batch_id: batchId, sha256: sha, filename: 'extrato-sintetico.pdf', status: 'draft', complete: true, rows: sourceRows, row_count: 2, warnings: []};
const accounts = Array.from({length: 10}, (_, index) => ({id: `account-${index}`, tipo: index === 9 ? 'cartao_credito' : 'conta_corrente', instituicao: index === 9 ? 'Cartão sintético' : `Banco sintético ${index}`, ...(index === 9 ? {cartao_final: '1234'} : {})}));
async function syntheticFetch(url, init) {
  const route = new URL(url).pathname, method = init.method || 'GET';
  requests.push({route, method, body: init.body ? JSON.parse(init.body) : null});
  const reply = value => new Response(JSON.stringify(value));
  if (route === '/v1/auth/status') return reply({configured: true, setup_required: false});
  if (route === '/v1/auth/session') return reply({access_token: 'choice-session', expires_in: 43200, account: {username: 'synthetic', display_name: 'Teste'}});
  assert.equal(init.headers.get('Authorization'), 'Bearer choice-session');
  if (route === '/v1/capture-batches') return reply({items: [batch], has_more: false});
  if (route === `/v1/capture-batches/${batchId}`) return reply(batch);
  if (route.endsWith('/preview')) {
    const body = JSON.parse(init.body);
    return reply({scan_complete: true, preview_token: 'b'.repeat(64), new_count: body.rows.length, duplicate_count: 0, possible_duplicate_count: 0,
      insert_count: body.rows.filter(row => row.selected).length, rows: body.rows.map(row => ({...row, status: 'new', confidence: 'confirmed', reason: 'Nova.', matches: [], will_insert: row.selected}))});
  }
  if (route === '/v1/choice-test/pending') return new Promise((resolve, reject) => {
    const cancel = () => {stats.canceledRequests++; reject(Object.assign(new Error('Synthetic native request canceled'), {name: 'AbortError'}));};
    init.signal.addEventListener('abort', cancel, {once: true});
    resolvePendingRead = () => {init.signal.removeEventListener('abort', cancel); resolve(reply({persisted: true}));};
  });
  throw new Error(`Unexpected request ${method} ${route}`);
}
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  cache.set(file, module.exports);
  const relative = path.relative(mobile, file);
  const oldRoot = process.env.CHOICE_ORIGINAL_ROOT;
  const oldComponent = ['components/AuthProvider.tsx', 'components/ControlledChoice.tsx'].includes(relative.replaceAll('\\', '/'));
  const source = oldRoot && oldComponent ? path.join(oldRoot, 'mobile', relative) : file;
  const code = ts.transpileModule(fs.readFileSync(source, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React,
    target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module, exports: module.exports, console, Error, Date, URL, Headers, FormData, AbortController,
    setTimeout, clearTimeout, process: {env: {EXPO_PUBLIC_API_BASE_URL: 'https://choice-test.invalid'}}, fetch: syntheticFetch,
    require: name => {
      if (mocks[name]) return mocks[name];
      if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file), name), 'utf8'));
      if (name.startsWith('.')) {const relativeFile = path.resolve(path.dirname(file), name); return load(fs.existsSync(relativeFile + '.tsx') ? relativeFile + '.tsx' : relativeFile + '.ts');}
      throw new Error(`Unexpected module ${name}`);
    }}, {filename: source});
  cache.set(file, module.exports); return module.exports;
}
const {default: AuthProvider, useAuth} = load(path.join(mobile, 'components/AuthProvider.tsx'));
const Choice = load(path.join(mobile, 'components/ControlledChoice.tsx')).default;
const BatchReview = load(path.join(mobile, 'components/StatementBatchReview.tsx')).default;
const {authState, api} = load(path.join(mobile, 'utils/sessionClient.ts'));
const realSuspend = authState.suspend;
authState.suspend = () => {stats.suspensions++; realSuspend();};
function PrivateReview() {
  const [kind, setKind] = React.useState('expense'), [note, setNote] = React.useState('');
  const [category, setCategory] = React.useState('');
  const [kindCalls, setKindCalls] = React.useState(0);
  React.useEffect(() => {mounts++; return () => {unmounts++;};}, []);
  return React.createElement('View', null,
    React.createElement('TextInput', {accessibilityLabel: 'Rascunho de teste', value: note, onChangeText: setNote}),
    React.createElement('Text', {accessibilityLabel: 'Número de seleções do tipo'}, kindCalls),
    React.createElement(Choice, {label: 'Categoria', value: category, onChange: setCategory,
      options: ['Alimentação', 'Transporte', ...Array.from({length: 38}, (_, i) => `Categoria ${i}`)]}),
    React.createElement(Choice, {label: 'Tipo de teste', value: kind, options: ['expense', 'income'], display: value => value === 'expense' ? 'Despesa' : 'Receita',
      onChange: value => {setKind(value); setKindCalls(count => count + 1);}, allowClear: false}),
    React.createElement(BatchReview, {api: api.json, visible: true, revision: 0, accounts, profiles: [], onUpdated: async () => {}}));
}
function Screen() {const auth = useAuth(); return auth.authenticated ? React.createElement(PrivateReview) : auth.loginScreen;}
const settle = (ms = 15) => act(async () => {await new Promise(resolve => setTimeout(resolve, ms));});
function button(label) {
  const match = renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === label || node.findAllByType('Text').some(text => text.props.children === label));
  assert.ok(match, `Button ${label}`); return match;
}
const press = label => act(async () => {const match = button(label); assert.ok(!match.props.disabled, `${label} enabled`); match.props.onPress();});
const input = (label, value) => act(async () => {renderer.root.findByProps({accessibilityLabel: label}).props.onChangeText(value);});
const localNote = () => renderer.root.findByProps({accessibilityLabel: 'Rascunho de teste'}).props.value;
const labels = () => renderer.root.findAllByType('Pressable').map(node => node.props.accessibilityLabel).filter(Boolean);
const texts = () => renderer.root.findAllByType('Text').map(node => [].concat(node.props.children).join('')).join('\n');
const count = suffix => requests.filter(row => row.route.endsWith(suffix)).length;
const pending = () => {
  pendingRead = api.json('/v1/choice-test/pending').then(result => ({result}), error => ({error}));
  return settle();
};
const back = () => act(async () => {
  let handled = false;
  for (const handler of [...backHandlers].reverse()) if (handler()) {handled = true; break;}
  if (!handled) {const modal = renderer.root.findAllByType('AndroidModal')[0]; if (modal) {modal.props.onRequestClose(); handled = true;}}
  assert.equal(handled, true, 'Android back closes the active selection');
});
const appState = state => act(async () => {native.AppState.currentState = state; events.emit('change', state);});
function assertChoiceDidNotSuspend() {
  if (stats.modalOpens) console.log('Native Modal focus evidence:', JSON.stringify(stats));
  assert.equal(stats.modalOpens, 0, 'An app selection must remain in the Activity window');
  assert.equal(stats.blur, 0, 'Opening account/card/type selectors must not emit native window blur');
  assert.equal(stats.suspensions, 0, 'Opening a selector must not suspend the accepted session');
  assert.equal(stats.canceledRequests, 0, 'Opening a selector must not cancel a private request');
  assert.equal(authState.getToken(), 'choice-session');
  assert.equal(native.AppState.currentState, 'active');
}
(async () => {
  await act(async () => {renderer = create(React.createElement(AuthProvider, null, React.createElement(Screen)));}); await settle();
  await input('Usuário', 'synthetic'); await input('Senha', 'synthetic-password'); await press('Entrar'); await settle();
  assert.equal(authState.unlocked(), true); assert.equal(mounts, 1);
  await input('Rascunho de teste', 'Preservar revisão local');
  const keyboardBefore = stats.dismissKeyboard;
  await press('Selecionar Categoria');
  assert.equal(stats.dismissKeyboard, keyboardBefore + 1, 'Opening a category releases the previous editor keyboard');
  assert.notEqual(renderer.root.findByProps({accessibilityLabel: 'Buscar opção de Categoria'}).props.autoFocus, true,
    'Category search never opens the keyboard automatically');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 40);
  await input('Buscar opção de Categoria', 'alimentacao');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 1);
  await input('Buscar opção de Categoria', 'categoria inexistente');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 0);
  await input('Buscar opção de Categoria', '');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 40);
  await press('Escolher Alimentação');
  await press('Selecionar Categoria');
  assert.equal(button('Escolher Alimentação').props.accessibilityState.selected, true);
  await back();
  assert.equal(localNote(), 'Preservar revisão local');
  assert.equal(mounts, 1); assert.equal(unmounts, 0);
  await press('Revisar extrato-sintetico.pdf'); await settle();
  assert.equal(count('/preview'), 0);
  await pending();
  await press('Selecionar Conta ou cartão do extrato'); await settle();
  assertChoiceDidNotSuspend();
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 10, 'All account/card options appear');
  await input('Buscar opção de Conta ou cartão do extrato', 'cartao');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 1, 'Search matches accents without requiring them');
  await press('Escolher Cartão sintético · Cartão · 1234'); await settle(510);
  assert.equal(count('/preview'), 1, 'Selecting a card issues one automatic comparison');
  assert.equal(requests.filter(row => row.route.endsWith('/preview')).at(-1).body.account_ref, 'ledger:account-9');
  assert.match(texts(), /2 novas/);
  await act(async () => resolvePendingRead()); assert.equal((await pendingRead).error, undefined);
  const beforeRepeats = requests.length;
  for (let index = 0; index < 12; index++) {
    await press('Selecionar Conta ou cartão do extrato'); await back();
    await press('Selecionar Tipo de teste'); await press(index % 2 ? 'Escolher Despesa' : 'Escolher Receita');
  }
  await settle(510);
  assert.equal(requests.length, beforeRepeats, 'Opening, closing and selecting another local field never reloads the batch');
  assert.equal(count('/preview'), 1); assertChoiceDidNotSuspend();
  await press('Selecionar Tipo da linha 1'); await press('Escolher Receita ou entrada'); await settle(510);
  assert.equal(count('/preview'), 2, 'Editing a reviewed row issues only one comparison');
  assert.equal(requests.filter(row => row.route.endsWith('/preview')).at(-1).body.rows[0].kind, 'income');
  await press('Selecionar Conta ou cartão do extrato');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 10, 'Closing and reopening clears the search');
  await press('Escolher Banco sintético 2 · Conta'); await settle(510);
  assert.equal(count('/preview'), 3); assert.equal(requests.filter(row => row.route.endsWith('/preview')).at(-1).body.account_ref, 'ledger:account-2');
  const persisted = JSON.parse(drafts.get(batchId));
  assert.equal(persisted.account, 'ledger:account-2'); assert.equal(persisted.rows[0].kind, 'income');
  await pending(); await press('Selecionar Conta ou cartão do extrato');
  await appState('background'); await settle();
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 0, 'Real background closes the app selection');
  assert.equal(stats.canceledRequests, 1, 'Real background still cancels network work');
  assert.equal(stats.suspensions, 1, 'Real background still suspends network credentials');
  assert.equal((await pendingRead).error.name, 'AbortError');
  assert.equal(authState.unlocked(), true, 'Brief background preserves the 15-minute session grace');
  assert.equal(localNote(), 'Preservar revisão local'); assert.equal(mounts, 1); assert.equal(unmounts, 0);
  const covered = renderer.root.findAllByType('View').find(node => node.props.accessibilityElementsHidden === true);
  assert.ok(covered, 'Private tree is hidden from accessibility during background');
  const coveredStyle = Object.assign({}, ...[].concat(covered.props.style));
  assert.notEqual(coveredStyle.display, 'none', 'Privacy cover must keep native layout mounted');
  assert.equal(coveredStyle.opacity, 0); assert.equal(covered.props.pointerEvents, 'none');
  await appState('active'); await settle(510);
  assert.equal(authState.getToken(), 'choice-session'); assert.equal(localNote(), 'Preservar revisão local');
  assert.equal(count('/preview'), 3, 'Returning does not replay completed comparisons');
  assert.deepEqual(JSON.parse(drafts.get(batchId)).rows, persisted.rows);
  await press('Selecionar Tipo de teste');
  const typeCallsBefore = renderer.root.findByProps({accessibilityLabel: 'Número de seleções do tipo'}).props.children;
  await act(async () => {const select = button('Escolher Receita').props.onPress; select(); select();});
  assert.equal(renderer.root.findByProps({accessibilityLabel: 'Número de seleções do tipo'}).props.children, typeCallsBefore + 1,
    'Two selection events before rerender consume the choice only once');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 0);
  await act(async () => {
    button('Selecionar Conta ou cartão do extrato').props.onPress();
    native.AppState.currentState = 'background'; events.emit('change', 'background');
  }); await settle();
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 0,
    'Opening and background in one native event batch closes the selection');
  await press('Selecionar Conta ou cartão do extrato');
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 0, 'A queued press cannot reopen a choice while backgrounded');
  assert.equal(stats.suspensions, 2); assert.equal(localNote(), 'Preservar revisão local');
  await appState('active'); await settle(510);
  assert.equal(labels().filter(label => label.startsWith('Escolher ')).length, 0, 'Returning never restores the closed selection');
  assert.equal(authState.getToken(), 'choice-session'); assert.equal(localNote(), 'Preservar revisão local');
  assert.deepEqual(JSON.parse(drafts.get(batchId)).rows, persisted.rows);
  assert.equal(count('/preview'), 3, 'The background race never replays a comparison');
  await press('Selecionar Tipo de teste'); await back(); await settle();
  assert.equal(stats.modalOpens, 0); assert.equal(stats.blur, 0); assert.equal(stats.canceledRequests, 1);
  assert.deepEqual(depthErrors, []); assert.ok(requests.length <= 10, 'No request storm across repeated choices and background');
  await act(async () => renderer.unmount());
  assert.equal(events.eventNames().length, 0); assert.equal(backHandlers.size, 0); assert.equal(unmounts, 1);
  console.log('PASS: real account/card/type choices and batch review stay in one Android window; search/back/repeated selections, bounded calls, background privacy and preserved drafts.');
})().catch(async error => {
  console.log('Choice focus diagnostics:', JSON.stringify({stats, requests: requests.length, depthErrors}));
  originalConsoleError(error); process.exitCode = 1;
  await act(async () => renderer?.unmount());
}).finally(() => {console.error = originalConsoleError;});
