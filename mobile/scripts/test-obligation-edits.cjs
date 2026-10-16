// Real commitment form, detail and presentation. Synthetic transport only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const db = {getAllAsync: async () => [], runAsync: async () => {}};
const native = {
  useWindowDimensions: () => ({width: 390, height: 800, fontScale: 1}),
  Platform: {OS: 'android'}, StyleSheet: {create: value => value},
  Alert: {alert() {throw new Error('Native alert must not be used');}},
  BackHandler: {addEventListener: () => ({remove() {}})},
  AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  Keyboard: {dismiss() {}},
  FlatList: () => null,
  Modal: ({children}) => React.createElement('Modal', null, children),
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'Image', 'ActivityIndicator', 'ScrollView'].map(name => [name, name])),
};
const mocks = {
  react: React,
  'react-native': native,
  '../utils/queueDb': {useSQLiteContext: () => db},
  './VoiceCapture': {__esModule: true, default: ({onApply, disabled}) => React.createElement('Pressable', {
    accessibilityRole: 'button', accessibilityLabel: 'Aplicar rascunho de voz', disabled,
    onPress: () => onApply({
      kind: 'obrigacao', name: 'IPVA', amount: '300.00', obligation_plan: 'parcelado', installment_count: 3,
      cash_due_date: '2027-01-15T00:00:00Z', next_due_date: '2027-02-15', payment_method: 'pix',
    }, 'IPVA parcelado em três vezes'),
  }, React.createElement('Text', null, 'Aplicar rascunho de voz'))},
  'react-native-safe-area-context': {SafeAreaView: 'View', useSafeAreaInsets: () => ({top: 0, bottom: 0})},
  'expo-image-picker': {}, 'expo-sharing': {}, 'react-native-view-shot': {}, '../utils/devicePrompt': {},
};
const cache = new Map();
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText;
  vm.runInNewContext(code, {module, exports: module.exports, console, Error, Date, Intl, Math, JSON, URLSearchParams, AbortController, setTimeout, clearTimeout, Promise, require: name => {
    if (mocks[name]) return mocks[name];
    if (!name.startsWith('.')) return require(name);
    const target = path.resolve(path.dirname(file), name);
    return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
  }}, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}

const presentation = load('utils/commitmentPresentation.ts');
presentation.setCommitmentCutoff('2026-10-01');
assert.equal(presentation.DEFAULT_COMMITMENT_CUTOFF, '2026-10-01');
assert.equal(presentation.nextOpenDue({
  status: 'ativo', kind: 'assinatura', frequency: 'mensal', renewal_status: 'automatica',
  next_due_date: '2026-11-10', end_date: '2026-10-10', linked_transactions: [],
}, '2026-10-13'), null);
const stillOpen = presentation.nextOpenDue({
  status: 'ativo', kind: 'assinatura', frequency: 'mensal', renewal_status: 'automatica',
  next_due_date: '2026-10-10', end_date: '2026-10-10', linked_transactions: [],
}, '2026-10-13');
assert.equal(stillOpen.due, '2026-10-10');
assert.equal(presentation.previousDue('2026-12-10', 'mensal'), '2026-11-10');

const dialogs = load('utils/appAlert.ts');
const Manager = load('components/CommitmentManager.tsx').default;
const ScheduleHost = load('components/ScheduleOverlay.tsx').ScheduleOverlayProvider;

let renderer = null;
const settle = (ms = 30) => act(async () => {await new Promise(resolve => setTimeout(resolve, ms));});
const button = label => {
  const found = renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === label || node.findAllByType('Text').some(child => child.props.children === label));
  assert.ok(found, 'Button: ' + label);
  return found;
};
const press = label => act(async () => {const node = button(label); assert.ok(!node.props.disabled, label + ' enabled'); const result = node.props.onPress(); if (result && result.then) await result;});
const input = label => renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === label);
const fill = async (label, value) => {const node = input(label); assert.ok(node, label); await act(async () => {node.props.onChangeText(value);});};
const showNotes = async () => {if (!input('Observações (opcional)')) await press('Avançado');};
const dismiss = async label => {
  const current = dialogs.dialogSnapshot();
  assert.ok(current, 'Dialog open');
  const chosen = current.buttons.find(item => item.text === label);
  assert.ok(chosen, 'Dialog button: ' + label);
  await act(async () => {dialogs.dismissDialog(chosen, current);});
  await settle();
};
function screen(api) {
  return React.createElement(ScheduleHost, null, React.createElement(Manager, {api, upload: async () => ({}), accounts: [], cards: [], money: String, onUpdated() {}}));
}
function mount(api) {
  return act(async () => {renderer = create(screen(api), {createNodeMock: () => ({scrollTo() {}})});});
}

const progressing = {
  commitment_id: 'ipva-open', name: 'IPVA andamento', revision: 2, kind: 'obrigacao', obligation_plan: 'parcelado',
  installment_count: 3, next_installment_number: 2, purchase_date: '2026-10-11', next_due_date: '2026-12-10T00:00:00Z',
  cash_due_date: '2026-11-20T15:00:00Z', amount: 100, frequency: 'mensal', payment_method: 'pix',
  renewal_status: 'nao_aplicavel', status: 'ativo', schedule: [], linked_transactions: [],
};
const concluded = {
  commitment_id: 'ipva-done', name: 'IPVA concluido', revision: 5, kind: 'obrigacao', obligation_plan: 'parcelado',
  installment_count: 3, next_installment_number: 4, purchase_date: '2026-10-11', next_due_date: '2027-01-10',
  cash_due_date: '2026-11-20', amount: 100, frequency: 'mensal', payment_method: 'pix',
  renewal_status: 'nao_aplicavel', status: 'concluido', schedule: [], linked_transactions: [],
};
const cash = {
  commitment_id: 'iptu-cash', name: 'IPTU a vista', revision: 1, kind: 'obrigacao', obligation_plan: 'a_vista',
  purchase_date: '2026-10-11', next_due_date: '2026-11-20', cash_due_date: '2026-11-20T00:00:00Z', amount: 80,
  frequency: 'mensal', payment_method: 'pix', renewal_status: 'nao_aplicavel', status: 'ativo', schedule: [], linked_transactions: [],
};
const split = {
  commitment_id: 'iptu-split', name: 'IPTU parcelado', revision: 3, kind: 'obrigacao', obligation_plan: 'parcelado',
  installment_count: 3, next_installment_number: 2, purchase_date: '2026-10-01', next_due_date: '2026-12-10',
  cash_due_date: '2026-11-20', amount: 90, frequency: 'mensal', payment_method: 'boleto', renewal_status: 'nao_aplicavel',
  status: 'ativo', schedule_anchor_day: 10, schedule: [], linked_transactions: [],
};
const subscription = {
  commitment_id: 'claro', name: 'Claro', revision: 3, kind: 'assinatura', purchase_date: '2024-01-10',
  next_due_date: '2026-11-10', schedule_anchor_day: 10, amount: 50, frequency: 'mensal', payment_method: 'debito',
  renewal_status: 'automatica', status: 'ativo', schedule: [], linked_transactions: [],
};

(async () => {
  const edits = [];
  const api = async (requestPath, init = {}) => {
    const clean = String(requestPath).split('?')[0];
    if (init.method === 'PUT' || init.method === 'POST') {
      const body = JSON.parse(init.body);
      edits.push({path: clean, method: init.method, body});
      if (init.method === 'POST' && clean === '/v1/commitments') return {persisted: true, creation_id: body.creation_id, commitment_id: 'voice-1', revision: 1};
      return {commitment_id: body.kind ? (body.name || 'saved') : 'saved', revision: (body.revision || 1) + 1, status: body.status || 'ativo'};
    }
    if (clean === '/v1/commitments') return {items: [progressing, concluded, cash, split, subscription]};
    return {items: []};
  };
  await mount(api);
  await settle();

  await press('Abrir IPVA andamento');
  await press('Editar compromisso IPVA andamento');
  assert.equal(input('Data à vista').props.value, '20/11/2026');
  assert.equal(input('Primeira parcela').props.value, '10/11/2026');
  assert.equal(input('Data à vista').props.value.length, 10);
  await showNotes();
  await fill('Observações (opcional)', 'Nova observação');
  await press('Salvar e sincronizar');
  await settle();
  const notes = edits.at(-1).body;
  assert.equal(notes.kind, 'obrigacao');
  assert.equal(notes.next_due_date, '2026-12-10');
  assert.equal(notes.next_installment_number, 2);
  assert.equal(notes.installment_count, 3);
  assert.equal(notes.cash_due_date, '2026-11-20');
  assert.equal(notes.notes, 'Nova observação');
  assert.equal(notes.status, 'ativo');
  await dismiss('OK');

  await press('Concluídos e cancelados');
  await press('Abrir IPVA concluido');
  await press('Editar compromisso IPVA concluido');
  assert.equal(input('Primeira parcela').props.value, '10/10/2026');
  await showNotes();
  await fill('Observações (opcional)', 'Nota depois');
  await press('Salvar e sincronizar');
  await settle();
  const done = edits.at(-1).body;
  assert.equal(done.status, 'concluido');
  assert.equal(done.next_installment_number, 4);
  assert.equal(done.installment_count, 3);
  assert.equal(done.next_due_date, '2027-01-10');
  assert.equal(done.notes, 'Nota depois');
  await dismiss('OK');

  await press('Abrir IPTU a vista');
  await press('Mais ações de IPTU a vista');
  assert.equal(renderer.root.findAllByType('Pressable').filter(node => node.props.accessibilityLabel === 'Terminar IPTU a vista em').length, 0);
  await press('Encerrar IPTU a vista');
  await dismiss('Confirmar encerramento de IPTU a vista');
  await settle();
  const closedCash = edits.at(-1).body;
  assert.equal(closedCash.status, 'cancelado');
  assert.equal(closedCash.obligation_plan, 'a_vista');
  assert.equal(closedCash.cash_due_date, '2026-11-20');
  assert.equal(closedCash.installment_count, undefined);
  assert.equal(closedCash.next_installment_number, undefined);
  await dismiss('OK');

  await press('Abrir IPTU parcelado');
  await press('Mais ações de IPTU parcelado');
  const splitMenu = renderer.root.findAllByProps({accessibilityLabel: 'Menu do compromisso'})[0];
  assert.equal(splitMenu.findAllByType('Pressable').map(node => node.props.accessibilityLabel)[0], 'Terminar IPTU parcelado em');
  await press('Encerrar IPTU parcelado');
  await dismiss('Confirmar encerramento de IPTU parcelado');
  await settle();
  const closedSplit = edits.at(-1).body;
  assert.equal(closedSplit.status, 'cancelado');
  assert.equal(closedSplit.obligation_plan, 'parcelado');
  assert.equal(closedSplit.cash_due_date, '2026-11-20');
  assert.equal(closedSplit.installment_count, 3);
  assert.equal(closedSplit.next_installment_number, 2);
  await dismiss('OK');

  await press('Abrir Claro');
  await press('Mais ações de Claro');
  const menu = renderer.root.findAllByProps({accessibilityLabel: 'Menu do compromisso'})[0];
  assert.equal(menu.findAllByType('Pressable').map(node => node.props.accessibilityLabel)[0], 'Terminar Claro em');
  await press('Terminar Claro em');
  const month = input('Mês final de Claro');
  await act(async () => {month.props.onChangeText('122027');});
  await press('Definir término de Claro');
  const confirm = dialogs.dialogSnapshot();
  assert.match(confirm.message, /Última cobrança em dez\/2027/);
  assert.match(confirm.message, /Vínculos e extrato permanecem/);
  assert.equal(confirm.buttons.length, 2);
  assert.equal(confirm.buttons[0].text, 'Cancelar');
  assert.equal(confirm.buttons[1].text, 'Confirmar término');
  assert.equal(confirm.buttons[0].style, 'cancel');
  assert.equal(confirm.buttons[1].style, undefined);
  await dismiss('Confirmar término');
  await settle();
  const ended = edits.at(-1).body;
  assert.equal(ended.kind, 'assinatura');
  assert.equal(ended.end_date, '2027-12-10');
  assert.equal(ended.status, 'ativo');
  assert.equal(ended.renewal_status, 'automatica');
  await dismiss('OK');

  const beforeVoice = edits.length;
  await press('+ Adicionar');
  await settle();
  await press('Aplicar rascunho de voz');
  await settle();
  assert.equal(edits.length, beforeVoice);
  assert.equal(input('Data à vista').props.value, '15/01/2027');
  assert.equal(input('Primeira parcela').props.value, '15/02/2027');
  assert.equal(input('Nome ou serviço').props.value, 'IPVA');
  await press('Salvar e sincronizar');
  await settle();
  const voiced = edits.at(-1).body;
  assert.equal(voiced.kind, 'obrigacao');
  assert.equal(voiced.renewal_status, 'nao_aplicavel');
  assert.equal(voiced.obligation_plan, 'parcelado');
  assert.equal(voiced.cash_due_date, '2027-01-15');
  assert.equal(voiced.next_due_date, '2027-02-15');
  assert.equal(voiced.installment_count, 3);
  assert.equal(voiced.next_installment_number, 1);
  assert.equal(edits.at(-1).path, '/v1/commitments');
  assert.equal(renderer.root.findAllByType('Modal').length, 0);
  console.log('Obligation edit, close, voice and end-date checks passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
