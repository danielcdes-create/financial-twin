// Captured vendor text ranks the same chips as typing. Synthetic names only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const cache = new Map();
const reviews = new Map();
let renderer;
const db = {
  getAllAsync: async (sql, id) => {
    if (!sql.includes('review_drafts')) return [];
    if (id) return reviews.has(id) ? [reviews.get(id)] : [];
    return [...reviews.values()];
  },
  runAsync: async (sql, ...args) => {
    if (!sql.includes('review_drafts')) return;
    if (sql.startsWith('DELETE')) { reviews.delete(args[0]); return; }
    const [provisional_id, item, draft, updated_at] = args;
    reviews.set(provisional_id, {provisional_id, item, draft, updated_at});
  },
};
const native = {
  Platform: {OS: 'android'},
  Alert: {alert() { throw new Error('Native dialog must not be used'); }},
  StyleSheet: {create: value => value},
  Keyboard: {dismiss() {}},
  AppState: {currentState: 'active', addEventListener: () => ({remove() {}}) },
  BackHandler: {addEventListener: () => ({remove() {}}) },
  FlatList: ({data, renderItem, ListEmptyComponent}) => React.createElement('View', null, data.length ? data.map(item => React.createElement(React.Fragment, {key: item.value}, renderItem({item}))) : ListEmptyComponent),
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'ActivityIndicator', 'ScrollView', 'Modal'].map(name => [name, name])),
};
const mocks = {
  react: React,
  'react-native': native,
  'react-native-safe-area-context': {SafeAreaView: 'View'},
  '../utils/queueDb': {useSQLiteContext: () => db},
};
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText, {
    module, exports: module.exports, console, Date, Intl, Math, JSON, Promise, Error, WeakMap, Map, Set, Symbol, Object, URLSearchParams, AbortController, setTimeout, clearTimeout,
    require: name => {
      if (mocks[name]) return mocks[name];
      if (!name.startsWith('.')) return require(name);
      const next = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(next + '.tsx') ? next + '.tsx' : next + '.ts');
    },
  }, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}

const {rankedOptions, suggestionQuery} = load('utils/classification.ts');
const vendors = ['Alfa Distante', 'Beta Distante', 'Gama Distante', 'Delta Distante', 'Epsilon Distante', 'Padaria Distante', 'Lanche Norte'];
const captured = {merchant_raw: "LANCHE NORTE'S", plataforma_sugerida: 'Entrega Sintetica'};
assert.equal(suggestionQuery('vendedor', 'Pada', captured), 'Pada');
assert.equal(suggestionQuery('vendedor', '  ', captured), "LANCHE NORTE'S");
assert.equal(suggestionQuery('plataforma', '', captured), 'Entrega Sintetica');
assert.equal(suggestionQuery('localidade', '', captured), '');
assert.deepEqual(rankedOptions(suggestionQuery('vendedor', '', captured), vendors), ['Lanche Norte']);
assert.deepEqual(rankedOptions(suggestionQuery('vendedor', '', {merchant_raw: "LANCHE NORTE'S"}), ['LancheNorte', 'Padaria Distante']), ['LancheNorte']);
assert.deepEqual(rankedOptions(suggestionQuery('vendedor', 'Pada', captured), vendors), ['Padaria Distante']);
assert.equal(rankedOptions('', vendors)[0], 'Alfa Distante');
assert.ok(!rankedOptions('', vendors).includes('Padaria Distante'));

const Provisional = load('components/ProvisionalReview.tsx').default;
const Provider = load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider;
const platforms = ['Alfa App', 'Beta App', 'Gama App', 'Delta App', 'Epsilon App', 'Outra Plataforma Distante', 'Entrega Sintetica'];
const item = {
  provisional_id: 'capture-vendor', revision: 1, status: 'provisional', merchant_raw: "LANCHE NORTE'S",
  plataforma_sugerida: 'Entrega Sintetica', valor: -10, data_evento: '2026-10-14T16:01:00Z', fluxo: 'saida', natureza: 'COMPRA', tipo: 'compra',
};
const options = {natureza: ['COMPRA'], fluxo: ['saida'], categoria: ['Casa'], categoria_subcategorias: {Casa: []}, metodo_pagamento: [], vendedor: vendors, plataforma: platforms};
const api = async route => {
  if (route.endsWith('/classification-options')) return options;
  if (route === '/v1/account-positions') return {items: []};
  if (route.includes('?')) return {items: [item], total: 1};
  return {item};
};
const text = () => renderer.root.findAllByType('Text').map(node => [node.props.children].flat(Infinity).filter(part => typeof part === 'string' || typeof part === 'number').join('')).join('\n');
const field = label => renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === label);
const button = label => {
  const node = renderer.root.findAllByType('Pressable').find(entry => entry.findAllByType('Text').some(child => child.props.children === label));
  assert.ok(node, label);
  return node;
};

(async () => {
  await act(async () => { renderer = create(React.createElement(Provider, null, React.createElement(Provisional, {api, initialItem: item, money: String, onUpdated: async () => {}})), {createNodeMock: () => ({})}); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
  assert.equal(field('Vendedor').props.value, '');
  assert.equal(field('Plataforma').props.value, '');
  assert.match(text(), /Lanche Norte/);
  assert.match(text(), /Entrega Sintetica/);
  assert.doesNotMatch(text(), /Padaria Distante/);
  assert.doesNotMatch(text(), /Alfa Distante/);
  await act(async () => { field('Vendedor').props.onChangeText('Pada'); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  assert.match(text(), /Padaria Distante/);
  assert.doesNotMatch(text(), /Lanche Norte/);
  assert.equal(field('Vendedor').props.value, 'Pada');
  await act(async () => { button('Padaria Distante').props.onPress(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  assert.equal(field('Vendedor').props.value, 'Padaria Distante');
  await act(async () => renderer.unmount());
  console.log('PASS: captured vendor text ranks review chips, and typed text replaces it.');
})().catch(error => { console.error(error); process.exitCode = 1; });
