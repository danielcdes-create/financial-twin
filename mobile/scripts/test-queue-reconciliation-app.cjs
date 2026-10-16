// Real App, queue sync and recovery; isolated storage, child UI and HTTP.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map(), cache = new Map(), requests = [], alerts = [], removed = [], imported = new Set();
let delayEvent = '', releaseUpload, renderer;
class FormDataMock {constructor() {this.fields = new Map();} append(key, value) {this.fields.set(key, value);} get(key) {return this.fields.get(key);}}
const native = {Platform: {OS: 'android'}, StyleSheet: {create: value => value},
  useWindowDimensions: () => ({width: 390, height: 844}),
  ...Object.fromEntries(['ActivityIndicator', 'FlatList', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name]))};
async function json(url) {return url === '/health' ? {mongodb: 'connected', auth_required: true} : {items: [], total: 0};}
async function upload(url, data) {
  assert.equal(url, '/v1/capture-batches/file-status', 'Recovery must not extract or resubmit an imported file');
  const event_id = data.get('event_id'); requests.push(event_id);
  if (event_id === delayEvent) await new Promise(resolve => {releaseUpload = resolve;});
  return {imported: imported.has(event_id), event_id, sha256: 'ab'.repeat(32), batch_id: '33333333-3333-4333-8333-333333333333'};
}
const mocks = {
  'react': React, 'react-native':{...native,BackHandler:{addEventListener:()=>({remove(){}})}},
  'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView', useSafeAreaInsets: () => ({top: 0, bottom: 0})},
  'expo-document-picker': {}, 'expo-image-picker': {}, 'expo-router': {usePathname:()=> '/',router: {push() {}}},
  'expo-notification-capture': {__esModule: true, default: {available: false}},
  './utils/appAlert': {Alert: {alert: (...args) => alerts.push(args)}},
  '../utils/appAlert': {Alert: {alert: (...args) => alerts.push(args)}},
  './components/AuthProvider': {useAuth: () => ({lock() {}})},
  './utils/sessionClient': {API: 'https://synthetic.invalid', api: {json, upload, getReadGeneration:()=>0, subscribeMutations:()=>()=>{}, configureMutationStore(){}, pendingMutations:async()=>[],resumeMutation:async()=>{}, subscribeAvailability: () => () => {}, getAvailability: () => 'connected'},
    authState: {getToken: () => 'synthetic-session', unlocked: () => true, sessionIdentity: () => 1}},
  './utils/useForeground': {useForeground: () => true},
  './sessionStore': {getItemAsync: async () => {throw Error('Do not read CPF');}},
  './uploadFile': {appendQueuedFile: async (data, row) => data.append('file', {uri: row.local_uri})},
  './utils/durableFiles': {removeQueuedFile: async uri => removed.push(uri)},
};
function load(file) {
  file = path.resolve(file); if (cache.has(file)) return cache.get(file);
  const m = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  }}).outputText, {
    module: m, exports: m.exports, console, URLSearchParams, FormData: FormDataMock, AbortController, Date,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: {locks: {request: async (_name, work) => work()}},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
    require: name => {
      if (mocks[name]) return mocks[name];
      if (name.startsWith('./components/') && name !== './components/InteractionControls') return {__esModule: true, default: props => React.createElement(name.slice(13) + 'Stub', props)};
      if (!name.startsWith('.')) return require(name);
      const target = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');
    },
  }, {filename: file});
  cache.set(file, m.exports); return m.exports;
}
const db = load('utils/queueDb.web.tsx').webQueueDb;
mocks['./utils/queueDb'] = {useSQLiteContext: () => db};
const App = load('App.tsx').default;
const list = () => db.getAllAsync('SELECT * FROM event_queue ORDER BY id');
const settle = () => act(async () => {await new Promise(resolve => setTimeout(resolve, 15));});
const button = label => {
  const found = renderer.root.findAllByType('Pressable').find(node => node.findAllByType('Text').some(text => text.props.children === label));
  assert.ok(found, `Expected visible action: ${label}`); return found;
};
const press = async label => {await act(async () => {button(label).props.onPress();}); await settle();};
const count = event => requests.filter(id => id === event).length;
const capturePanel = () => renderer.root.findByType('CapturePanelStub');
async function seed(event_id) {
  await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)',
    event_id, 'document', '{}', 'file:///capture-queue/' + event_id, event_id + '.pdf', 'application/pdf', '2026-10-03');
  await db.runAsync('INSERT INTO queue_state', event_id, 1, null, 'only BRL is supported', 1);
}
(async () => {
  const first = '11111111-1111-4111-8111-111111111111', second = '22222222-2222-4222-8222-222222222222';
  await seed(first); await seed(second);
  await act(async () => {renderer = create(React.createElement(App), {createNodeMock: () => ({scrollTo() {}})});}); await settle();
  assert.equal(count(first), 1); assert.equal(count(second), 1); assert.equal((await list()).length, 2);
  await press('Mais'); await press('Ajustes');
  assert.ok(button('Revisar como fatura / extrato'), 'Quarantined file remains visible before a committed import');
  await press('Capturar');
  imported.add(first); delayEvent = first;
  await act(async () => {void capturePanel().props.onBatchUpdated();}); await settle();
  assert.equal(count(first), 2, 'Commit callback rechecks immediately inside the regular throttle window');
  assert.equal(typeof releaseUpload, 'function');
  await press('Mais'); await press('Ajustes');
  await press('Revisar como fatura / extrato');
  assert.equal(count(first), 2, 'Manual retry shares the automatic check already in flight');
  await act(async () => {delayEvent = ''; releaseUpload();}); await settle();
  assert.equal((await list()).length, 1); assert.equal((await list())[0].event_id, second);
  assert.equal(alerts.length, 0, 'Concurrent recovery does not show a false missing-file/reroute error');
  assert.equal(removed.length, 0); assert.equal(count(second), 2);
  assert.equal(JSON.parse(storage.get('financial-twin-queue-acknowledgements'))[0].identity.includes('file:///capture-queue/'+first),true);
  await press('Capturar'); imported.add(second);
  await act(async () => {await capturePanel().props.onBatchUpdated();}); await settle();
  assert.equal(count(second), 3, 'A later committed batch resets the throttle and checks the remaining failed file');
  assert.equal((await list()).length, 0); assert.equal(removed.length, 0); assert.equal(alerts.length, 0);
  const receipts=await db.getAllAsync('SELECT * FROM queue_acknowledgements');
  assert.equal(receipts.length,2);assert.ok(receipts.every(row=>JSON.parse(row.result).imported===true));
  await act(async () => renderer.unmount());
  console.log('App queue reconciliation: commit recheck, shared pending recovery, safe concurrent retry and unmatched preservation — passed.');
})().catch(async error => {if (renderer) await act(async () => renderer.unmount()); console.error(error); process.exitCode = 1;});
