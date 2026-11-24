// App + dialog/selector/operation hosts are real; native dispatch and I/O are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');

global.IS_REACT_ACT_ENVIRONMENT = true;

let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'Async local-back assertions did not finish');
});

const cache = new Map();
const handlers = [];
let route = '/';
let renderer = null;

const sqliteDb = {getAllAsync: async () => [], runAsync: async () => {}, execAsync: async () => {}};
const queueSync = async () => {};
const summary = {
  period_start: '2026-10-01',
  period_end: '2026-10-31',
  category_spending: [{category: 'Categoria 0', amount: 100}],
  monthly_series: [],
  income: 2000,
  expenses: 1000,
};
const api = {
  json: async url => {
    if (url.startsWith('/v1/dashboard/overview')) return {selected: summary, month: summary, year: summary};
    if (url === '/v1/accounts') return {items: [{id: 'savings', tipo: 'poupanca', instituicao: 'Poupança teste'}]};
    if (url.startsWith('/v1/movements?')) return {items: [], total: 0, has_more: false};
    return {items: [], total: 0, mongodb: 'connected', auth_required: true};
  },
  upload: async () => {},
  getReadGeneration: () => 0,
  subscribeAvailability: () => () => {},
  getAvailability: () => 'connected',
  configureMutationStore() {},
  pendingMutations: async () => [],
  subscribeMutations: () => () => {},
};

const native = {
  Platform: {OS: 'android'},
  StyleSheet: {create: x => x},
  Keyboard: {dismiss() {}},
  AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  BackHandler: {
    addEventListener: (name, callback) => {
      const entry = {name, callback};
      handlers.push(entry);
      return {
        remove() {
          const index = handlers.indexOf(entry);
          if (index >= 0) handlers.splice(index, 1);
        },
      };
    },
  },
  Alert: {alert() {}},
  useWindowDimensions: () => ({width: 390, height: 844, fontScale: 1}),
  ...Object.fromEntries(['ActivityIndicator', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name])),
  FlatList: props =>
    React.createElement(
      'ListSurface',
      {data: props.data},
      props.ListHeaderComponent,
      (props.data || []).map((item, index) =>
        React.createElement(
          React.Fragment,
          {key: props.keyExtractor ? props.keyExtractor(item, index) : (item.id ?? index)},
          props.renderItem({item, index}),
        ),
      ),
      props.ListFooterComponent,
    ),
};

const mocks = {
  react: React,
  'react-native': native,
  'react-native-safe-area-context': {SafeAreaView: 'View', useSafeAreaInsets: () => ({top: 0, bottom: 0})},
  'expo-document-picker': {},
  'expo-image-picker': {},
  'expo-router': {usePathname: () => route, router: {push() {}}},
  'expo-notification-capture': {__esModule: true, default: {available: false}},
  './components/AuthProvider': {useAuth: () => ({lock() {}})},
  './utils/queueDb': {useSQLiteContext: () => sqliteDb},
  './utils/useQueueSync': {useQueueSync: () => queueSync},
  './utils/useForeground': {useForeground: () => true},
  './utils/sessionClient': {
    API: 'https://synthetic.invalid',
    api,
    authState: {getToken: () => 'session', unlocked: () => true, sessionIdentity: () => 1},
  },
  './sessionStore': {getItemAsync: async () => null},
  './utils/durableFiles': {},
  './uploadFile': {appendQueuedFile: async () => {}},
};

function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const m = {exports: {}};
  cache.set(file, m.exports);
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
      module: m,
      exports: m.exports,
      console,
      Error,
      Date,
      Intl,
      Symbol,
      Promise,
      URL,
      URLSearchParams,
      AbortController,
      setTimeout,
      clearTimeout,
      setInterval,
      clearInterval,
      require: name => {
        if (mocks[name]) return mocks[name];
        if (name.startsWith('./components/') && name !== './components/InteractionControls') {
          return {__esModule: true, default: props => React.createElement(name.slice(13) + 'Stub', props)};
        }
        if (!name.startsWith('.')) return require(name);
        const target = path.resolve(path.dirname(file), name);
        return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');
      },
    },
    {filename: file},
  );
  cache.set(file, m.exports);
  return m.exports;
}

const App = load('App.tsx').default;
const Boundary = load('components/UserOperationBoundary.tsx').default;
const Host = load('components/AppDialogHost.tsx').default;
const choices = load('components/ChoiceOverlay.tsx');
const gate = load('utils/userOperation.ts');
const dialogs = load('utils/appAlert.ts');
const localBack = load('utils/localBack.ts');

const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
const button = label => {
  const node = renderer.root.findAllByType('Pressable').find(n => n.findAllByType('Text').some(text => text.props.children === label));
  assert.ok(node, label);
  return node;
};
const press = label => act(async () => { await button(label).props.onPress(); });

function OpenChoice() {
  const overlay = choices.useChoiceOverlay();
  return React.createElement(
    'Pressable',
    {
      onPress: () =>
        overlay.open({
          owner: 'fixture',
          label: 'Synthetic choices',
          value: '',
          options: [{value: 'one', label: 'One'}],
          allowClear: true,
          onSelect() {},
        }),
    },
    React.createElement('Text', null, 'Open fixture selector'),
  );
}

const tree = () =>
  React.createElement(
    React.Fragment,
    null,
    React.createElement(Boundary, null, React.createElement(choices.ChoiceOverlayProvider, null, React.createElement(App), React.createElement(OpenChoice))),
    React.createElement(Host),
  );

const shareTree = () =>
  React.createElement(
    React.Fragment,
    null,
    React.createElement(
      Boundary,
      null,
      React.createElement(
        choices.ChoiceOverlayProvider,
        null,
        React.createElement(App, {key: 'index'}),
        React.createElement(App, {key: 'share', navigationRoute: '/handle-share', initialPurchaseError: 'Synthetic share'}),
      ),
    ),
    React.createElement(Host),
  );

const hardwareBack = () => {
  for (const entry of handlers.slice().reverse()) {
    if (entry.callback()) return true;
  }
  return false;
};
const appBackCount = () => handlers.filter(entry => entry.callback === localBack.dispatchLocalBack).length;
const stub = name => renderer.root.findAllByType(name + 'Stub').length;

(async () => {
  await act(async () => { renderer = create(tree(), {createNodeMock: () => ({scrollTo() {}})}); });
  await settle();
  await press('Mais');
  for (const [label, component] of [
    ['Decisões e evidências', 'FinancialLab'],
    ['Fechamento do mês', 'MonthClose'],
    ['Cabe na minha vida?', 'PurchaseCheck'],
    ['Rascunhos', 'DraftManager'],
    ['Contas', 'AccountPositions'],
    ['Cartões', 'CardManager'],
    ['Categorias', 'CategoryManager'],
    ['Compromissos', 'CommitmentManager'],
    ['Ajustes', 'AppUpdate'],
  ]) {
    await press(label);
    if (label !== 'Ajustes') assert.equal(stub(component), 1, label);
    await act(async () => assert.equal(hardwareBack(), true, label));
    assert.ok(button('Decisões e evidências'), label + ' returns to More');
  }
  for (const [label, component] of [
    ['Contas', 'AccountPositions'],
    ['Cartões', 'CardManager'],
    ['Categorias', 'CategoryManager'],
    ['Compromissos', 'CommitmentManager'],
    ['Ajustes', 'AppUpdate'],
  ]) {
    await press(label);
    if (label !== 'Ajustes') assert.equal(stub(component), 1, label);
    await press('‹ Voltar a Mais');
    assert.ok(button('Decisões e evidências'), label + ' UI Back button returns to More');
  }
  await press('Decisões e evidências');
  await press('Open fixture selector');
  await act(async () => assert.equal(hardwareBack(), true));
  assert.equal(stub('FinancialLab'), 1, 'Overlay closes before leaving its page');
  await act(async () => dialogs.Alert.alert('Synthetic dialog', 'Keep page', [{text: 'Cancel', style: 'cancel'}]));
  await act(async () => assert.equal(hardwareBack(), true));
  assert.equal(dialogs.dialogSnapshot(), null);
  assert.equal(stub('FinancialLab'), 1, 'Dialog cancels before navigation');
  let release;
  await act(async () => {
    gate.runUserAction(() => new Promise(resolve => { release = resolve; }));
  });
  await act(async () => assert.equal(hardwareBack(), true));
  assert.equal(stub('FinancialLab'), 1);
  await act(async () => release());
  assert.equal(stub('FinancialLab'), 1, 'Blocked Back is never replayed');
  route = '/account';
  await act(async () => renderer.update(tree()));
  await act(async () => assert.equal(hardwareBack(), false, 'Inactive index does not consume another Router route Back'));
  route = '/';
  await act(async () => renderer.update(tree()));
  await act(async () => assert.equal(hardwareBack(), true));
  await press('Início');
  await act(async () => assert.equal(hardwareBack(), false, 'Top-level tab delegates native default'));
  await act(async () => renderer.unmount());
  renderer = null;
  assert.equal(handlers.length, 0);
  route = '/handle-share';
  await act(async () => { renderer = create(shareTree(), {createNodeMock: () => ({scrollTo() {}})}); });
  await settle();
  assert.equal(appBackCount(), 1, 'Only shared App owns native Back while index remains mounted');
  assert.equal(stub('PurchaseCheck'), 1);
  await act(async () => assert.equal(hardwareBack(), true));
  assert.equal(stub('PurchaseCheck'), 0, 'Shared App subtab returns to its own More page');
  route = '/';
  await act(async () => renderer.update(shareTree()));
  assert.equal(appBackCount(), 1, 'Only index App owns Back after returning to root');
  await act(async () => assert.equal(hardwareBack(), false, 'Index default cannot navigate hidden share App'));
  await act(async () => renderer.unmount());
  renderer = null;
  assert.equal(handlers.length, 0);
  completed = true;
  console.log('PASS all More subtabs, overlays, dialogs, synchronous mutation lock/no replay, both App routes and native default');
})().catch(async error => {
  console.error(error);
  if (renderer) await act(async () => renderer.unmount());
  process.exitCode = 1;
});
