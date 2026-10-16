// Real production components; HTTP, native views and timers are simulated.
// This proves lifecycle/response ordering, not Android keyboard animation.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), React = require('react'), {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const cache = new Map(), failures = [], dialogs = [];
const native = {useWindowDimensions:()=>({width:390,fontScale:1}),Platform: {OS: 'android'}, AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  BackHandler: {addEventListener: () => ({remove() {}})}, Keyboard: {dismiss() {}}, StyleSheet: {create: x => x},
  FlatList: ({data, renderItem}) => React.createElement('View', null, data.map(item => React.createElement(React.Fragment, {key: item.value}, renderItem({item})))),
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'ActivityIndicator'].map(name => [name, name]))};
const mocks = {'react': React, 'react-native': native, 'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView'},
  '../utils/queueDb': {useSQLiteContext: () => ({getAllAsync: async () => [], runAsync: async () => {}})},
  '../utils/appAlert': {Alert: {alert: (...args) => dialogs.push(args)}}};
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const original = process.env.UI_STABILITY_ORIGINAL;
  const candidate = original && path.join(original, path.relative(process.cwd(), file));
  const source = candidate && fs.existsSync(candidate) ? candidate : file;
  const m = {exports: {}};
  const code = ts.transpileModule(fs.readFileSync(source, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module: m, exports: m.exports, console, URLSearchParams, setTimeout, clearTimeout, AbortController,
    require: name => {
      if (mocks[name]) return mocks[name];
      const target = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');
    }}, {filename: source});
  cache.set(file, m.exports); return m.exports;
}
const {ChoiceOverlayProvider} = load('components/ChoiceOverlay.tsx');
const Choice = load('components/ControlledChoice.tsx').default;
const Transaction = load('components/TransactionEditor.tsx').default;
const Records = load('components/RecordReview.tsx').default;
const Lab = load('components/FinancialLab.tsx').default;
const deferred = () => {let resolve, reject;const promise = new Promise((yes, no) => {resolve = yes; reject = no;});return {promise, resolve, reject};};
const settle = (ms = 20) => act(async () => {await new Promise(resolve => setTimeout(resolve, ms));});
let renderer;
const mount = async element => act(async () => {renderer = create(React.createElement(ChoiceOverlayProvider, null, element));});
const texts = () => renderer.root.findAllByType('Text').map(node => [].concat(node.props.children).join('')).join('\n');
const button = label => {
  const found = renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === label || node.findAllByType('Text').some(t => t.props.children === label));
  assert.ok(found, `Button: ${label}`); return found;
};
const press = label => act(async () => {const node = button(label);assert.ok(!node.props.disabled, `${label} enabled`);node.props.onPress();});
const input = (label, value) => act(async () => renderer.root.findByProps({accessibilityLabel: label}).props.onChangeText(value));
async function check(name, run) {
  dialogs.length = 0;
  try {await run();console.log('PASS:', name);} catch (error) {failures.push(name);console.error('FAIL:', name, error.message);}
  finally {if (renderer) {await act(async () => renderer.unmount());renderer = null;}}
}
(async () => {
  await check('Write confirmation arrives after ACK without waiting for delayed refresh, and read failure preserves success', async () => {
    dialogs.length = 0;
    const acknowledgement = deferred(), refresh = deferred();
    let writes = 0, refreshing = false, requestBody;
    const item = {id: 'confirmation-probe', valor: -12.59, source_token: 'token-before'};
    const api = (route, init = {}) => {
      if (init.method === 'PATCH') {writes++;requestBody = JSON.parse(init.body);return acknowledgement.promise;}
      if (route === '/confirmation-probe/refresh') {refreshing = true;return refresh.promise;}
      return Promise.resolve(route.endsWith('classification-options') ? {} : {total: 0});
    };
    await mount(React.createElement(Transaction, {item, api, money: String,
      onSaved: () => api('/confirmation-probe/refresh'), onCancel() {}}));
    await input('Observações', 'Descrição conferida');
    const save = button('Salvar sem validar').props.onPress;
    await act(async () => {save();save();});
    assert.equal(writes, 1, 'Two queued save events cannot duplicate a write');
    assert.equal(requestBody.expected_token, 'token-before');
    assert.equal(dialogs.length, 0, 'Success is never announced before server acknowledgement');
    await act(async () => acknowledgement.resolve({item: {...item, detalhe: 'Descrição conferida', source_token: 'token-after'}}));
    assert.equal(refreshing, true);
    assert.equal(dialogs[0]?.[0], 'Registro atualizado', 'Confirmation cannot wait for a delayed read');
    assert.equal(button('Salvar sem validar').props.disabled, false, 'The ACK supplies the current item/token; a secondary refresh cannot hold the edit lock');
    await act(async () => refresh.reject(new Error('Leitura secundária indisponível')));
    assert.equal(dialogs.at(-1)[0], 'Alteração salva');
    assert.match(dialogs.at(-1)[1], /A gravação foi confirmada/);
    assert.ok(!dialogs.some(dialog => dialog[0] === 'Não foi possível salvar'), 'Read failure cannot be presented as write failure');
    assert.equal(button('Salvar sem validar').props.disabled, false);
  });
  await check('A rejected write retains its draft and never emits a success confirmation', async () => {
    dialogs.length = 0;
    let refreshes = 0;
    const api = (route, init = {}) => init.method === 'PATCH'
      ? Promise.reject(Object.assign(new Error('Fonte mudou'), {status: 409}))
      : Promise.resolve(route.endsWith('classification-options') ? {} : {total: 0});
    await mount(React.createElement(Transaction, {item: {id: 'rejected-probe', valor: -12, source_token: 'old'}, api, money: String,
      onSaved: () => {refreshes++;}, onCancel() {}}));
    await input('Observações', 'Rascunho conservado');
    await press('Salvar sem validar');
    assert.equal(dialogs.at(-1)[0], 'Não foi possível salvar');
    assert.equal(refreshes, 0);
    assert.equal(renderer.root.findByProps({accessibilityLabel: 'Observações'}).props.value, 'Rascunho conservado');
    assert.equal(button('Salvar sem validar').props.disabled, true, 'A conflicting source still requires explicit recovery');
  });
  await check('Late classification options never replace an editable input or erase a supplier draft', async () => {
    const taxonomy = deferred();
    const api = route => route.endsWith('classification-options') ? taxonomy.promise : Promise.resolve({total: 0});
    await mount(React.createElement(Transaction, {item: {id: 'synthetic', valor: -12.59}, api, money: String, onSaved() {}, onCancel() {}}));
    const category = button('Selecionar Categoria');
    assert.equal(category.props.disabled, true, 'Taxonomy is loading');
    const supplier = renderer.root.findByProps({accessibilityLabel: 'Vendedor'});
    await input('Vendedor', 'Rascunho da loja');
    await act(async () => taxonomy.resolve({categoria: ['Alimentação', 'Transporte'], categoria_subcategorias: {Alimentação: ['Mercado']}}));
    assert.equal(button('Selecionar Categoria'), category, 'Category control stays mounted');
    assert.equal(renderer.root.findByProps({accessibilityLabel: 'Vendedor'}), supplier, 'Supplier input stays mounted');
    assert.equal(supplier.props.value, 'Rascunho da loja');
    await press('Selecionar Categoria'); await press('Escolher Alimentação');
    await press('Selecionar Subcategoria'); await press('Escolher Mercado');
    await press('Selecionar Categoria'); await press('Escolher Transporte');
    await press('Selecionar Subcategoria');
    assert.equal(renderer.root.findAllByType('Pressable').filter(n => n.props.accessibilityLabel?.startsWith('Escolher ')).length, 0, 'Empty dependent options stay a selector, not free text');
  });
  await check('A pending save closes its selector and rejects queued option presses', async () => {
    let calls = 0;
    const props = {label: 'Conta', value: '', options: ['A', 'B'], onChange: () => calls++};
    await mount(React.createElement(Choice, props));
    await press('Selecionar Conta');
    const queued = button('Escolher B').props.onPress;
    await act(async () => renderer.update(React.createElement(ChoiceOverlayProvider, null, React.createElement(Choice, {...props, disabled: true}))));
    await act(async () => queued());
    assert.equal(calls, 0);
    assert.equal(button('Selecionar Conta').props.disabled, true);
    assert.equal(renderer.root.findAllByType('Pressable').filter(n => n.props.accessibilityLabel?.startsWith('Escolher ')).length, 0);
  });
  await check('Record filters retain the latest response when older requests finish later', async () => {
    const reads = [];
    const api = route => {const pending = deferred(); reads.push({route, ...pending}); return pending.promise;};
    await mount(React.createElement(Records, {api, money: String, onUpdated: async () => {}}));await settle();
    await press('Validados');await settle();
    assert.equal(reads.length, 2);
    const row = name => ({id: name, vendedor: name, valor: -12, status_revisao: 'validado'});
    await act(async () => reads[1].resolve({items: [row('Resposta atual')], total: 1}));
    await act(async () => reads[0].resolve({items: [row('Resposta antiga')], total: 1}));
    assert.match(texts(), /Resposta atual/);assert.doesNotMatch(texts(), /Resposta antiga/);
    await press('Todos');await settle();await press('Pendentes');await settle();
    await act(async () => reads[3].resolve({items: [row('Última busca')], total: 1}));
    await act(async () => reads[2].reject(new Error('Erro da busca ultrapassada')));
    assert.equal(dialogs.length, 0, 'Stale failures do not interrupt current UI');
  });
  await check('Editing financial dates never reloads while typing; Update uses the completed range', async () => {
    const calls = [];
    const api = async route => {calls.push(route);return {income: 0, expenses: 0, gaps: [], trail_preview: []};};
    await mount(React.createElement(Lab, {api, onBack() {}}));await settle();
    assert.equal(calls.length, 1);
    const field = renderer.root.findByProps({accessibilityLabel: 'Período inicial (AAAA-MM-DD)'});
    for (const value of ['2', '20', '2026-', '2026-09-01']) {await input('Período inicial (AAAA-MM-DD)', value);await settle();}
    assert.equal(calls.length, 1, 'No partial-date requests or loading flicker');
    assert.equal(renderer.root.findByProps({accessibilityLabel: 'Período inicial (AAAA-MM-DD)'}), field);
    await press('Atualizar fechamento');await settle();
    assert.equal(calls.length, 2);assert.match(calls[1], /start=2026-09-01/);
    assert.match(texts(), /Período exibido: 2026-09-01/);
  });
  await check('Overlapping decision loads keep selectors locked until the final response', async () => {
    const closure=deferred(), categories=deferred();
    const api=route=>route.includes('/closure?')?closure.promise:route==='/v1/categories'?categories.promise:Promise.resolve({items:[]});
    await mount(React.createElement(Lab,{api,onBack(){}}));await settle();
    await press('Experimentos');
    await act(async()=>closure.resolve({gaps:[],trail_preview:[]}));
    assert.equal(button('Selecionar Categoria').props.disabled,true,'First completed request must not unlock a still-loading selector');
    await act(async()=>categories.resolve({items:[{name:'Alimentação'}]}));
    assert.equal(button('Selecionar Categoria').props.disabled,false);
    await press('Selecionar Categoria');await press('Escolher Alimentação');
  });
  if (failures.length) throw new Error(`${failures.length} UI regressions: ${failures.join('; ')}`);
})().catch(error => {console.error(error.message);process.exitCode = 1;});
