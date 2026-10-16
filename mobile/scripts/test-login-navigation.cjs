// Exercise our root layout against Expo SDK 57's actual native SQLite lifecycle.
// The web SQLite adapter ignores onInit and cannot reproduce this native failure.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
let host, opens = 0, closes = 0;
const statements = [];
const react = {
  createElement: (type, props, ...children) => ({type, props: {...props, ...(children.length ? {children} : {})}}),
  createContext: () => ({Provider: 'Context.Provider'}),
  memo: (component, compare) => Object.assign(component, {compare}),
  useRef: initial => host.slot(() => ({current: initial})),
  useState: initial => {
    const slot = host.slot(() => ({value: initial}));
    return [slot.value, next => {slot.value = typeof next === 'function' ? next(slot.value) : next;}];
  },
  useEffect: (effect, dependencies) => {
    const slot = host.slot(() => ({dependencies: null, cleanup: null}));
    if (!slot.dependencies || dependencies.some((item, index) => item !== slot.dependencies[index])) {
      slot.dependencies = dependencies;
      host.effects.push(() => {slot.cleanup?.(); slot.cleanup = effect();});
    }
  },
};
react.default = react;
react.__esModule = true;
function compile(source, filename, dependencies) {
  const module = {exports: {}};
  const javascript = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(javascript, {module, exports: module.exports, Date, setTimeout, clearTimeout,
    require: name => {
      if (name === 'react') return react;
      if (!(name in dependencies)) throw new Error(`Unexpected dependency ${name}`);
      return dependencies[name];
    }}, {filename});
  return module.exports;
}
const sdk = compile(fs.readFileSync('node_modules/expo-sqlite/src/hooks.tsx', 'utf8'), 'expo-sqlite/hooks.tsx', {
  'expo-asset': {}, './ExpoSQLite': {}, './pathUtils': {},
  './SQLiteDatabase': {openDatabaseAsync: async () => {
    opens++;
    return {execAsync: async sql => {statements.push(sql);}, closeAsync: async () => {closes++;}};
  }},
});
const root = compile(fs.readFileSync('app/_layout.tsx', 'utf8'), 'app/_layout.tsx', {
  'expo-router': {Stack: 'Stack'}, '../utils/queueDb': {SQLiteProvider: sdk.SQLiteProvider},
  'expo-status-bar': {StatusBar: 'StatusBar'}, 'react-native-safe-area-context': {SafeAreaProvider: 'SafeAreaProvider'},
  '../components/AuthProvider': {__esModule: true, default: 'AuthProvider'},
  '../components/AppDialogHost': {__esModule: true, default: 'AppDialogHost'},
  '../components/UserOperationBoundary': {__esModule: true, default: 'UserOperationBoundary'},
  '../utils/userOperation': {},
}).default;
const {AuthState} = compile(fs.readFileSync('utils/authState.ts', 'utf8'), 'authState.ts', {});
const state = new AuthState();
class HookHost {
  constructor() {this.slots = []; this.effects = []; this.cursor = 0;}
  slot(create) {const index = this.cursor++; return this.slots[index] ??= create();}
  render(node) {
    host = this; this.cursor = 0;
    const native = node.type(node.props);
    return native.type(native.props);
  }
  commit() {for (const effect of this.effects.splice(0)) effect();}
}
(async () => {
  const hooks = new HookHost();
  let previous = root();
  assert.equal(hooks.render(previous), null, 'SQLite must initialize before private content mounts');
  hooks.commit();
  // Wait for native SQLite initialization, now including the additive receipt
  // and preserved-draft tables. Drain the task queue instead of assuming a
  // fixed number of promise turns for a particular schema length.
  await new Promise(resolve => setImmediate(resolve));
  assert.notEqual(hooks.render(previous), null, 'SQLite did not finish initializing');
  state.activate('synthetic-accepted-login', Date.now() + 60000);
  for (let navigation = 0; navigation < 20; navigation++) {
    const next = root();
    const compared = sdk.SQLiteProvider.compare(previous.props, next.props);
    hooks.render(next);
    hooks.commit();
    const tree = hooks.render(next);
    // A native-provider null render unmounts AuthProvider; its real cleanup locks.
    if (!tree) state.lock();
    assert.equal(state.unlocked(), true, `Navigation ${navigation + 1} discarded a successfully accepted login`);
    assert.notEqual(tree, null, 'Navigation unmounted the authentication subtree');
    assert.equal(compared, true, 'Root changes restarted the native SQLite provider');
    previous = next;
  }
  assert.equal(opens, 1, 'Navigation reopened the local queue');
  assert.equal(closes, 0, 'Navigation closed the local queue');
  assert.equal(statements.length, 8, 'All eight additive tables initialize exactly once');
  assert.ok(statements.every(sql => /^CREATE TABLE IF NOT EXISTS /.test(sql)), 'Initialization must preserve queued data');
  console.log('PASS: accepted login survives 20 root/navigation updates using the actual Expo native SQLite provider; queue opened once, never closed or recreated.');
})().catch(error => {console.error(error.message); process.exitCode = 1;});
