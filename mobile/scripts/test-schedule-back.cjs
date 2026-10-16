// Real schedule/detail host. Native back delivery is the recorded listener list, newest first.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const handlers = [];
const native = {
  Platform: {OS: 'android'},
  StyleSheet: {create: value => value},
  Keyboard: {dismiss() {}},
  AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  BackHandler: {addEventListener(name, callback) {
    const entry = {name, callback};
    handlers.push(entry);
    return {remove() {const index = handlers.indexOf(entry); if (index >= 0) handlers.splice(index, 1);}};
  }},
  ...Object.fromEntries(['ActivityIndicator', 'FlatList', 'Modal', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(name => [name, name])),
};
const mocks = {
  react: React,
  'react-native': native,
  'react-native-safe-area-context': {SafeAreaView: 'View', useSafeAreaInsets: () => ({top: 0, bottom: 0})},
};
const cache = new Map();
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module, exports: module.exports, console, Error, Date, Intl, Math, JSON, Symbol, Promise, URL, URLSearchParams, AbortController, setTimeout, clearTimeout, setInterval, clearInterval, require: name => {
    if (mocks[name]) return mocks[name];
    if (!name.startsWith('.')) return require(name);
    const target = path.resolve(path.dirname(file), name);
    return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
  }}, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}

const schedule = load('components/ScheduleOverlay.tsx');
const choices = load('components/ChoiceOverlay.tsx');
const DialogHost = load('components/AppDialogHost.tsx').default;
const Boundary = load('components/UserOperationBoundary.tsx').default;
const localBack = load('utils/localBack.ts');
const gate = load('utils/userOperation.ts');
const dialogs = load('utils/appAlert.ts');
const hardwareBack = () => {for (const entry of handlers.slice().reverse()) if (entry.callback()) return true; return false;};
const labeled = label => renderer.root.findAll(node => node.props && node.props.accessibilityLabel === label);
const childMounted = () => renderer.root.findAllByType('Text').filter(node => node.props.children === 'Tela de baixo').length;
const press = async label => {
  const node = renderer.root.findAllByType('Pressable').find(item => item.props.accessibilityLabel === label);
  assert.ok(node, label);
  await act(async () => {await node.props.onPress();});
};
let renderer = null;
let closedCount = 0;
let pageBacks = 0;

function Screen() {
  const overlay = schedule.useScheduleOverlay();
  const choice = choices.useChoiceOverlay();
  const [mode, setMode] = React.useState('detail');
  React.useEffect(() => {
    if (native.Platform.OS !== 'android') return undefined;
    const subscription = native.BackHandler.addEventListener('hardwareBackPress', localBack.dispatchLocalBack);
    return () => subscription.remove();
  }, []);
  localBack.useLocalBack(() => {pageBacks += 1;}, true, 10);
  React.useLayoutEffect(() => {
    const close = () => {closedCount += 1; setMode('closed');};
    if (mode === 'detail') {
      overlay.setPanel({
        title: 'Detalhe de teste',
        onClose: close,
        menu: [{label: 'Encerrar', accessibilityLabel: 'Encerrar teste', onPress() {}}],
        body: React.createElement('Text', null, 'Corpo do detalhe'),
      });
    } else if (mode === 'schedule') {
      overlay.show({
        title: 'Assinatura teste',
        rows: [{kind: 'row', key: 'row-1', label: 'Parcela'}],
        onClose: close,
        onToggleYear() {},
        onLink() {},
      });
    } else overlay.clear();
    return () => overlay.clear();
  }, [mode, overlay]);
  return React.createElement(React.Fragment, null,
    React.createElement('Text', null, 'Tela de baixo'),
    React.createElement('Pressable', {accessibilityLabel: 'Reabrir detalhe', onPress: () => setMode('detail')}, React.createElement('Text', null, 'Reabrir detalhe')),
    React.createElement('Pressable', {accessibilityLabel: 'Abrir cronograma', onPress: () => setMode('schedule')}, React.createElement('Text', null, 'Abrir cronograma')),
    React.createElement('Pressable', {accessibilityLabel: 'Abrir seletor', onPress: () => choice.open({
      owner: 'fixture', label: 'Seletor de teste', value: '', options: [{value: 'a', label: 'Opção'}], allowClear: true, onSelect() {},
    })}, React.createElement('Text', null, 'Abrir seletor')));
}

function Tree() {
  return React.createElement(choices.ChoiceOverlayProvider, null,
    React.createElement(schedule.ScheduleOverlayProvider, null,
      React.createElement(Boundary, null, React.createElement(Screen)),
      React.createElement(DialogHost)));
}

function layerListenersOnly() {
  const allowed = new Set([gate.isUserInteractionBlocked, localBack.dispatchLocalBack]);
  assert.equal(handlers.length, 2, 'overlay must not install its own hardware-back listener');
  assert.ok(handlers.every(entry => allowed.has(entry.callback)), 'hardware back stays on the shared dispatcher and the operation lock');
}

(async () => {
  await act(async () => {renderer = create(React.createElement(Tree), {createNodeMock: () => ({scrollTo() {}})});});
  assert.equal(labeled('Detalhe de teste').length, 1);
  assert.equal(childMounted(), 1);
  layerListenersOnly();

  await press('Mais ações');
  assert.equal(labeled('Menu do compromisso').length, 1);
  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(labeled('Menu do compromisso').length, 0, 'idle back closes the overflow menu first');
  assert.equal(labeled('Detalhe de teste').length, 1);
  assert.equal(closedCount, 0);
  assert.equal(pageBacks, 0);
  assert.equal(childMounted(), 1);
  layerListenersOnly();

  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(labeled('Detalhe de teste').length, 0, 'next idle back closes the panel');
  assert.equal(closedCount, 1);
  assert.equal(pageBacks, 0, 'the screen underneath does not take that press');
  assert.equal(childMounted(), 1);

  await press('Reabrir detalhe');
  assert.equal(labeled('Detalhe de teste').length, 1);
  let release;
  await act(async () => {gate.runUserAction(() => new Promise(resolve => {release = resolve;}));});
  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(labeled('Detalhe de teste').length, 1, 'locked back does not close the panel');
  assert.equal(closedCount, 1);
  await act(async () => {release();});
  assert.equal(labeled('Detalhe de teste').length, 1, 'a blocked back is not replayed');
  assert.equal(closedCount, 1);
  assert.equal(childMounted(), 1);

  await act(async () => {dialogs.Alert.alert('Confirme', 'Fique no detalhe', [{text: 'Cancelar', style: 'cancel'}]);});
  assert.ok(dialogs.dialogSnapshot());
  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(dialogs.dialogSnapshot(), null, 'dialog consumes back');
  assert.equal(labeled('Detalhe de teste').length, 1, 'dialog back does not close the panel');
  assert.equal(closedCount, 1);

  await press('Abrir seletor');
  assert.equal(labeled('Seletor de teste').length, 1);
  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(labeled('Seletor de teste').length, 0, 'selector consumes back');
  assert.equal(labeled('Detalhe de teste').length, 1, 'selector back does not close the panel');
  assert.equal(closedCount, 1);
  assert.equal(childMounted(), 1);
  layerListenersOnly();

  await press('Abrir cronograma');
  assert.equal(labeled('Cronograma de Assinatura teste').length, 1);
  assert.equal(labeled('Detalhe de teste').length, 0);
  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(labeled('Cronograma de Assinatura teste').length, 0, 'idle back closes the schedule');
  assert.equal(closedCount, 2);
  assert.equal(pageBacks, 0);
  assert.equal(childMounted(), 1);

  await act(async () => {assert.equal(hardwareBack(), true);});
  assert.equal(pageBacks, 1, 'with the layer gone, back reaches the screen underneath');
  assert.equal(childMounted(), 1);
  await act(async () => {renderer.unmount();});
  renderer = null;
  assert.equal(handlers.length, 0);

  native.Platform.OS = 'web';
  function WebScreen() {
    const overlay = schedule.useScheduleOverlay();
    React.useLayoutEffect(() => {
      overlay.setPanel({title: 'Detalhe web', onClose() {}, body: React.createElement('Text', null, 'Web')});
      return () => overlay.clear();
    }, [overlay]);
    return React.createElement('Text', null, 'Tela web');
  }
  await act(async () => {renderer = create(React.createElement(schedule.ScheduleOverlayProvider, null, React.createElement(WebScreen)), {createNodeMock: () => ({scrollTo() {}})});});
  assert.equal(labeled('Detalhe web').length, 1);
  assert.equal(handlers.length, 0, 'web schedule layer does not subscribe a native back listener');
  await act(async () => {renderer.unmount();});
  renderer = null;
  console.log('PASS schedule hardware back closes menu then panel, leaves the screen mounted, and yields to lock, dialog, and selector');
})().catch(async error => {console.error(error); if (renderer) await act(async () => renderer.unmount()); process.exitCode = 1;});
