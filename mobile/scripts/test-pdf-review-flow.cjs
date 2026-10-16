// Real queue adapter, review list and capture editor; isolated data and HTTP.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map(), cache = new Map(), requests = [], alerts = [];
let quotaFailure = false, renderer, failSave = false;
const native = {useWindowDimensions:()=>({width:390,fontScale:1}),Platform: {OS: 'android'}, Keyboard: {dismiss() {}}, StyleSheet: {create: x => x},
  AppState: {addEventListener: () => ({remove() {}})}, BackHandler: {addEventListener: () => ({remove() {}})},
  FlatList: ({data, renderItem}) => React.createElement('View', null, data.map(item => React.createElement(React.Fragment, {key: item.value}, renderItem({item})))),
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'ActivityIndicator'].map(name => [name, name]))};
const mocks = {'react': React, 'react-native': native, 'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView'},
  './sessionStore': {getItemAsync: async () => null}, '../utils/appAlert': {Alert: {alert: (...args) => alerts.push(args)}}};
function load(file) {
  file = path.resolve(file); if (cache.has(file)) return cache.get(file);
  const m = {exports: {}};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module: m, exports: m.exports, console, URLSearchParams, setTimeout, clearTimeout, AbortController,
    navigator: {locks: {request: async (_name, fn) => fn()}},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => {if (quotaFailure) throw Error('Quota exceeded'); storage.set(key, value);}},
    require: name => {
      if (mocks[name]) return mocks[name];
      if (!name.startsWith('.')) return require(name);
      const target = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');
    }}, {filename: file});
  cache.set(file, m.exports); return m.exports;
}
const db = load('utils/queueDb.web.tsx').webQueueDb;
mocks['../utils/queueDb'] = {useSQLiteContext: () => db};
const {canReviewAsStatement, requeueAsStatement} = load('utils/queueRecovery.ts');
const {createQueueSync} = load('utils/syncQueue.ts');
const Records = load('components/RecordReview.tsx').default;
const {ChoiceOverlayProvider} = load('components/ChoiceOverlay.tsx');
const row = {id: 1, event_id: 'same-pdf-id', kind: 'document', payload: '{}', local_uri: 'blob:original-bytes', filename: 'extrato.pdf', mime_type: 'application/pdf', last_error: 'only BRL is supported'};
let capture = {id: 'capture', provisional_id: 'capture', movement_source: 'capture', merchant_raw: 'Compra PDF', descricao_original: 'Compra PDF',
  valor: -12.59, data_evento: '2026-10-01T12:00:00Z', fluxo: 'saida', natureza: 'CONSUMO', tipo: 'compra', categoria_sugerida: 'Alimentação', categoria: 'Alimentação',
  vendedor: 'Loja', status: 'confirmed', status_revisao: 'validado', revision: 1};
async function api(url, init = {}) {
  const body = init.body ? JSON.parse(init.body) : null; requests.push({url, body, method: init.method || 'GET'});
  if (url.startsWith('/v1/review-records?')) {
    const status = new URLSearchParams(url.split('?')[1]).get('review_status');
    const items = status === 'todos' || status === capture.status_revisao ? [{...capture}] : [];
    return {items, total: items.length};
  }
  if (url.endsWith('/classification-options')) return {categoria: ['Alimentação'], categoria_subcategorias: {}, natureza: ['CONSUMO'], fluxo: ['saida'], metodo_pagamento: [], vendedor: [], plataforma: []};
  if (url === '/v1/account-positions') return {items: []};
  if (url === '/v1/provisional/capture' && !init.method) return {item: {...capture}};
  if (url === '/v1/provisional/capture' && init.method === 'PATCH') {
    if (failSave) throw Object.assign(Error('Revisão desatualizada'), {status: 409});
    assert.equal(body.expected_revision, capture.revision);
    capture = {...capture, ...body, revision: capture.revision + 1, status_revisao: body.validate_review ? 'validado' : 'pendente'};
    return {item: capture, persisted: true, provisional_id: 'capture', request_id: body.request_id};
  }
  throw Error('Unexpected route: ' + url);
}
const settle = () => act(async () => {await new Promise(resolve => setTimeout(resolve, 20));});
const texts = () => renderer.root.findAllByType('Text').map(node => [].concat(node.props.children).join('')).join('\n');
const button = label => {
  const found = renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === label || node.findAllByType('Text').some(t => t.props.children === label));
  assert.ok(found, label); return found;
};
const press = label => act(async () => {const b = button(label); assert.ok(!b.props.disabled); b.props.onPress();});
async function openCapture() {
  const record = renderer.root.findAllByType('Pressable').find(node => node.findAllByType('Text').some(t => t.props.children === 'Compra PDF'));
  assert.ok(record, 'Imported PDF appears in the review list'); await act(async () => record.props.onPress()); await settle();
}
(async () => {
  await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)',
    row.event_id, row.kind, row.payload, row.local_uri, row.filename, row.mime_type, '2026-10-03');
  await db.runAsync('INSERT INTO queue_state', row.event_id, 1, null, row.last_error, 1);
  assert.ok(canReviewAsStatement(row)); assert.equal(canReviewAsStatement({...row, filename: 'receipt.jpg', mime_type: 'image/jpeg'}), true);
  assert.equal(canReviewAsStatement({...row,filename:'voz.txt',mime_type:'text/plain'}),false);
  assert.equal(canReviewAsStatement({...row,filename:'statement.xlsx',mime_type:'application/octet-stream',last_error:'Importação com 2 linha(s) inválida(s).'}),true);
  quotaFailure = true; await assert.rejects(requeueAsStatement(db, row)); quotaFailure = false;
  assert.equal((await db.getAllAsync('SELECT * FROM event_queue'))[0].kind, 'document', 'Failed conversion preserves original route');
  await requeueAsStatement(db, row);
  const queued = (await db.getAllAsync('SELECT * FROM event_queue'))[0];
  assert.equal(queued.kind, 'batch'); assert.equal(queued.event_id, row.event_id); assert.equal(queued.local_uri, row.local_uri); assert.equal(queued.payload, '{}');
  assert.deepEqual(JSON.parse(storage.get('financial-twin-queue-state')), []);
  let ack = {persisted: true, draft_persisted: true, event_id: 'wrong-id', needs_review: false}, done = [];
  const sync = createQueueSync({prepare: async () => true, list: () => db.getAllAsync('SELECT * FROM event_queue'), send: async () => ack,
    done: (item, result) => done.push({item, result}), changed() {}, recordFailure: async () => {},
    remove: item => db.runAsync('DELETE FROM event_queue WHERE event_id=?', item.event_id)});
  await sync(); assert.equal(done.length, 0); assert.equal((await db.getAllAsync('SELECT * FROM event_queue')).length, 1);
  ack = {...ack, event_id: row.event_id}; await sync(); assert.equal(done.length, 1); assert.equal(done[0].result.needs_review, false);
  assert.equal((await db.getAllAsync('SELECT * FROM event_queue')).length, 0, 'Only matching persisted ACK removes the rerouted item');
  await act(async () => {renderer = create(React.createElement(ChoiceOverlayProvider, null, React.createElement(Records, {api, money: String, onUpdated: async () => {}})));}); await settle();
  await press('Validados'); await settle(); await openCapture(); assert.match(texts(), /Status de revisão/);
  await press('Selecionar Status de revisão'); await press('Escolher Pendente'); await press('Salvar correções'); await settle();
  assert.equal(requests.find(r => r.method === 'PATCH').body.validate_review, false);
  await press('Pendentes'); await settle(); await openCapture();
  await press('Selecionar Status de revisão'); await press('Escolher Validado');
  failSave = true; await press('Salvar correções'); await settle(); assert.equal(capture.status_revisao, 'pendente');
  assert.equal((await db.getAllAsync('SELECT * FROM review_drafts WHERE provisional_id=?', 'capture')).length, 1, 'Conflict keeps the local review');
  assert.equal(button('Salvar correções').props.disabled, true, 'A definitive conflict blocks replay until the current source is checked');
  failSave = false; await press('Carregar captura atual e conferir'); await settle();
  await press('Conferi a mudança: recuperar minhas correções'); await settle();
  await press('Salvar correções'); await settle(); assert.equal(capture.status_revisao, 'validado');
  assert.equal((await db.getAllAsync('SELECT * FROM review_drafts WHERE provisional_id=?', 'capture')).length, 0);
  assert.ok(requests.every(r => !r.url.startsWith('/v1/transactions?') && !r.url.startsWith('/v1/transactions/capture')));
  await act(async () => renderer.unmount());
  console.log('PDF review: preserved rerouting, matching ACK, confirmed records, explicit status edits and conflict draft retention — passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
