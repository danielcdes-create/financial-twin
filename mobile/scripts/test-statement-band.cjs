// Real statement search. A new amount or due date on the same commitment must refetch and release the button.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const native = {
  Platform: {OS: 'android'}, StyleSheet: {create: value => value},
  Alert: {alert() {throw new Error('Native alert must not be used');}},
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable'].map(name => [name, name])),
};
const mocks = {react: React, 'react-native': native};
const cache = new Map();
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module, exports: module.exports, console, Error, Date, Intl, Math, JSON, URL, URLSearchParams, AbortController, setTimeout, clearTimeout, Promise, require: name => {
    if (mocks[name]) return mocks[name];
    if (!name.startsWith('.')) return require(name);
    const target = path.resolve(path.dirname(file), name);
    return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
  }}, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}

const Search = load('components/StatementSearch.tsx').default;
const calls = [];
const pending = [];
const api = requestPath => {
  calls.push(String(requestPath));
  return new Promise(resolve => pending.push(resolve));
};
const commitment = {commitment_id: 'ipva', amount: 100, next_due_date: '2026-11-10'};
let renderer;
const button = () => renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === 'Buscar no extrato');
const element = props => React.createElement(Search, {api, commitment: props, suggestionIds: [], money: String, onSelect() {}});

(async () => {
  await act(async () => {renderer = create(element(commitment));});
  assert.equal(calls.length, 1);
  assert.match(calls[0], /amount_min=75\.00/);
  assert.match(calls[0], /amount_max=125\.00/);
  assert.match(calls[0], /date_from=2026-09-26/);
  assert.equal(button().props.disabled, true);
  await act(async () => {pending.shift()({items: [], truncated: false});});
  assert.equal(button().props.disabled, false);

  await act(async () => {renderer.update(element({...commitment, amount: 40, next_due_date: '2026-12-10'}));});
  assert.equal(calls.length, 2, 'same commitment id still refetches the new band');
  assert.match(calls[1], /amount_min=30\.00/);
  assert.match(calls[1], /amount_max=50\.00/);
  assert.match(calls[1], /date_from=2026-10-26/);
  assert.equal(calls[1].includes('ipva'), true);
  assert.equal(button().props.disabled, true);
  await act(async () => {pending.shift()({items: [{id: 'row', valor: -40, data_evento: '2026-12-10', linkable: true, merchant_raw: 'IPVA'}], truncated: false});});
  assert.equal(button().props.disabled, false);
  assert.match(renderer.root.findAllByType('Text').map(node => [].concat(node.props.children).join('')).join('\n'), /IPVA/);

  await act(async () => {renderer.update(element({commitment_id: 'ipva', amount: 0, next_due_date: ''}));});
  assert.equal(calls.length, 2, 'an empty band does not send a search');
  assert.equal(button().props.disabled, false);
  console.log('Statement search refetches a changed amount or due date and releases the button.');
})().catch(error => {console.error(error); process.exitCode = 1;});
