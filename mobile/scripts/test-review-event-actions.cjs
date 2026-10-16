// Render the real App review rows with synthetic API responses and no network/storage writes.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const cache = new Map(), alerts = [], decisions = [];
let renderer, reviews = [];
const native = {Platform: {OS: 'android'}, StyleSheet: {create: value => value},
  useWindowDimensions: () => ({width: 390, height: 844, fontScale: 1}),
  ...Object.fromEntries(['ActivityIndicator', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name])),
  FlatList: props => React.createElement('ReviewList', null, props.ListHeaderComponent,
    (props.data || []).map((item, index) => React.createElement(React.Fragment, {key: item.review_id || index}, props.renderItem({item, index}))), props.ListFooterComponent)};
const db = {getAllAsync: async () => [], runAsync: async () => {}, execAsync: async () => {}};
async function json(url, init = {}) {
  if (init.method && init.method !== 'GET') {
    assert.match(url, /^\/v1\/reviews\/[^/]+\/decision$/);
    decisions.push({url, body: JSON.parse(init.body)});
    reviews = [];
    return {persisted: true};
  }
  if (url.startsWith('/v1/reviews')) return {items: reviews, total: reviews.length, next_cursor: null};
  return {items: [], total: 0, mongodb: 'connected', auth_required: true};
}
const mocks = {
  'react': React, 'react-native':{...native,BackHandler:{addEventListener:()=>({remove(){}})}},
  'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView', useSafeAreaInsets: () => ({top: 0, bottom: 0})},
  'expo-document-picker': {}, 'expo-image-picker': {}, 'expo-router': {usePathname:()=> '/',router: {push() {}}},
  'expo-notification-capture': {__esModule: true, default: {available: false}},
  './components/AuthProvider': {useAuth: () => ({lock() {}})},
  './utils/appAlert': {Alert: {alert: (...args) => alerts.push(args)}},
  '../utils/appAlert': {Alert: {alert: (...args) => alerts.push(args)}},
  './utils/queueDb': {useSQLiteContext: () => db},
  './utils/useQueueSync': {useQueueSync: () => async () => {}},
  './utils/useForeground': {useForeground: () => true},
  './sessionStore': {getItemAsync: async () => {throw Error('Unexpected private store read');}},
  './uploadFile': {appendQueuedFile: async () => {throw Error('Unexpected file upload');}},
  './utils/durableFiles': {persistQueuedFile: async () => {throw Error('Unexpected capture');}, removeQueuedFile: async () => {throw Error('Unexpected file cleanup');}},
  './utils/sessionClient': {API: 'https://synthetic.invalid', api: {json, upload: async () => {throw Error('Unexpected upload');},
    getReadGeneration: () => 0, configureMutationStore() {}, pendingMutations: async () => [], resumeMutation: async () => {},
    subscribeMutations: () => () => {}, subscribeAvailability: () => () => {}, getAvailability: () => 'connected'},
    authState: {getToken: () => 'synthetic-session', unlocked: () => true, sessionIdentity: () => 1}},
};
function load(file) {
  file = path.resolve(file); if (cache.has(file)) return cache.get(file);
  const m = {exports: {}};
  const sourcePath = file === path.resolve('App.tsx') && process.env.REVIEW_EVENT_ORIGINAL ? process.env.REVIEW_EVENT_ORIGINAL : file;
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  }}).outputText, {module: m, exports: m.exports, console, URLSearchParams, AbortController, Date,
    setTimeout, clearTimeout, setInterval, clearInterval,
    require: name => {
      if (mocks[name]) return mocks[name];
      if (name.startsWith('./components/') && name !== './components/InteractionControls') return {__esModule: true, default: props => React.createElement(name.slice(13) + 'Stub', props)};
      if (!name.startsWith('.')) return require(name);
      const target = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');
    }}, {filename: file});
  cache.set(file, m.exports); return m.exports;
}
const App = load('App.tsx').default;
const text = () => renderer.root.findAllByType('Text').map(node => [node.props.children].flat(Infinity).filter(value => typeof value === 'string').join('')).join('\n');
const button = label => renderer.root.findAllByType('Pressable').find(node => node.findAllByType('Text').some(child => child.props.children === label));
const settle = () => act(async () => {await new Promise(resolve => setTimeout(resolve, 20));});
async function press(label) {
  const target = button(label); assert.ok(target, `Visible action: ${label}`);
  await act(async () => target.props.onPress()); await settle();
}
async function mount(review) {
  reviews = [review];
  await act(async () => {renderer = create(React.createElement(App), {createNodeMock: () => ({scrollTo() {}})});}); await settle();
  await press('Revisão');
}
async function unmount() {await act(async () => renderer.unmount()); renderer = null;}
const tokens = {review_token: 'synthetic-review-v1', source_token: 'synthetic-source-v1', candidate_token: 'synthetic-candidate-v1'};
const needsParse = {...tokens, review_id: 'needs-parse', reason: 'event_needs_parse', source: null, candidate: null,
  source_event: {event_id: 'event-unparsed', raw_payload: {text: 'Fonte sintética sem valor seguro.'}}};
const parsedSource = {valor: -12.59, data_evento: '2026-10-01T12:00:00Z', merchant_raw: 'Loja sintética'};
const possibleMatch = {...tokens, review_id: 'possible-match', reason: 'possible_match', candidate_transaction_id: 'synthetic-ledger',
  source: parsedSource, candidate: {...parsedSource, descricao_original: 'Loja sintética'},
  source_event: {event_id: 'event-parsed', raw_payload: {text: 'Original sintético com valor e data já extraídos.'}}};
(async () => {
  await mount(needsParse);
  assert.match(text(), /A extração não forneceu dados suficientes/);
  assert.match(text(), /Fonte sintética sem valor seguro/);
  assert.ok(button('Registrar dados manualmente'));
  assert.equal(button('Confirmar correspondência'), undefined);
  await press('Descartar pendência e preservar original');
  assert.equal(decisions.length, 1); assert.equal(decisions[0].body.action, 'discard');
  assert.equal(decisions[0].body.review_token, needsParse.review_token);
  assert.equal(decisions[0].body.source_token, needsParse.source_token);
  assert.doesNotMatch(text(), /Fonte sintética sem valor seguro/);
  assert.equal(alerts.length,1,'The completed decision presents one confirmation');
  assert.equal(alerts[0][0],'Decisão salva');
  alerts.length=0;
  await unmount();
  await mount(possibleMatch);
  assert.match(text(), /Original sintético com valor e data já extraídos/);
  assert.match(text(), /Captura: R\$\s*12,59|Captura: -R\$\s*12,59/);
  assert.doesNotMatch(text(), /A extração não forneceu dados suficientes/);
  assert.equal(button('Registrar dados manualmente'), undefined);
  assert.equal(button('Descartar pendência e preservar original'), undefined);
  assert.ok(button('Confirmar correspondência')); assert.ok(button('Manter separada'));
  assert.equal(alerts.length, 0);
  await unmount();
  console.log('App review events: needs_parse exposes original/manual/discard; parsed possible_match retains original, extracted data and matching actions without false extraction failure — passed.');
})().catch(async error => {if (renderer) await unmount(); console.error(error); process.exitCode = 1;});
