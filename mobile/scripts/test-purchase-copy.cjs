// Render Cabe na minha vida and assert the copy a person actually sees.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const cache = new Map();
const native = {
  useWindowDimensions: () => ({width: 390, height: 800, fontScale: 1}),
  Platform: {OS: 'android'},
  AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  Keyboard: {dismiss() {}},
  BackHandler: {addEventListener: () => ({remove() {}})},
  StyleSheet: {create: styles => styles},
  Alert: {alert() {}},
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'ActivityIndicator', 'ScrollView', 'Image'].map(name => [name, name])),
};
const mocks = {
  react: React,
  'react-native': native,
  'expo-image-picker': {},
  'expo-sharing': {isAvailableAsync: async () => true, shareAsync: async () => {}},
  'react-native-view-shot': {captureRef: async () => 'synthetic:card'},
  'expo-audio': {AudioModule: {requestRecordingPermissionsAsync: async () => ({granted: true})}, RecordingPresets: {HIGH_QUALITY: {}}, setAudioModeAsync: async () => {}, useAudioRecorder: () => ({uri: 'synthetic:voice'}), useAudioRecorderState: () => ({durationMillis: 0})},
  'expo-document-picker': {},
  '../utils/purchaseImage': {purchaseImageFormData: async () => new FormData()},
  '../utils/devicePrompt': {withDevicePrompt: task => task()},
  '../utils/queueDb': {useSQLiteContext: () => ({})},
  '../utils/voiceFile': {voiceFormData: async () => new FormData()},
  '../utils/voiceDraft': {loadVoiceDraft: async () => null, saveVoiceDraft: async () => {}, preserveVoiceDraft: async () => {}, durableVoiceClip: async clip => clip, voiceDraftGeneration: () => 0, subscribeVoiceDraftChanges: () => () => {}, deleteVoiceDraft: async () => {}, flushVoiceDraft: async () => {}},
};
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText,
    {module, exports: module.exports, console, Date, Intl, Math, JSON, Promise, Error, URLSearchParams, AbortController, FormData, setTimeout, clearTimeout, document: {createElement: () => ({click() {}})},
      require: name => {
        if (mocks[name]) return mocks[name];
        if (!name.startsWith('.')) return require(name);
        const next = path.resolve(path.dirname(file), name);
        return load(fs.existsSync(next + '.tsx') ? next + '.tsx' : fs.existsSync(next + '.ts') ? next + '.ts' : next);
      }}, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}

const Purchase = load('components/PurchaseCheck.tsx').default;
const dialogs = load('utils/appAlert.ts');
let renderer;
const mount = element => act(async () => {renderer = create(element, {createNodeMock: () => ({})});});
const settle = (ms = 20) => act(async () => {await new Promise(resolve => setTimeout(resolve, ms));});
const texts = () => renderer.root.findAllByType('Text').map(node => [node.props.children].flat(Infinity).filter(item => typeof item === 'string' || typeof item === 'number').join(''));
const visible = () => {
  const inputs = renderer.root.findAllByType('TextInput').flatMap(node => [node.props.accessibilityLabel, node.props.placeholder, node.props.value]);
  const labels = renderer.root.findAllByType('Pressable').map(node => node.props.accessibilityLabel);
  return [...texts(), ...inputs, ...labels].filter(Boolean).join('\n');
};
const field = label => renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === label);
const button = label => {
  const node = renderer.root.findAllByType('Pressable').find(item => item.props.accessibilityLabel === label || item.findAllByType('Text').some(child => child.props.children === label));
  assert.ok(node, 'Button: ' + label);
  return node;
};
const input = (label, value) => act(async () => {assert.ok(field(label), 'Field: ' + label); field(label).props.onChangeText(value);});
const press = label => act(async () => {const node = button(label); assert.equal(node.props.disabled, false, label + ' enabled'); await node.props.onPress();});
const checkbox = label => {
  const node = renderer.root.findAllByType('Pressable').find(item => item.props.accessibilityRole === 'checkbox' && item.props.accessibilityLabel === label);
  assert.ok(node, 'Checkbox: ' + label);
  return node;
};
const checked = label => checkbox(label).props.accessibilityState.checked;
const pressExact = label => act(async () => {
  const node = renderer.root.findAllByType('Pressable').find(item => item.props.accessibilityLabel === label);
  assert.ok(node, 'Button: ' + label);
  await node.props.onPress();
});
const forbid = text => {
  for (const token of ['AAAA-MM-DD', 'parcela(s)', 'horizonte', 'assumido', 'não alcançada']) assert.equal(text.includes(token), false, token);
  assert.equal(/\d{4}-\d{2}-\d{2}/.test(text), false, 'ISO date printed on the screen');
};
const saoPauloIso = date => { const map = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(date).map(part => [part.type, part.value])); return `${map.year}-${map.month}-${map.day}`; };
const [year, month, day] = saoPauloIso(new Date()).split('-').map(Number);
const futureIso = new Date(Date.UTC(year, month - 1, day + 10)).toISOString().slice(0, 10);
const futureDisplay = futureIso.split('-').reverse().join('/');
const horizon = {start: '2026-10-15', end: '2027-01-13', days: 90, minimum_months: 3};
const context = {horizon, flows: [
  {id: 'commitment:card-azul:2026-11-10', date: '2026-11-10', amount: '-50.00', label: 'Mensalidade', source_ref: 'commitment:card-azul:2026-11-10'},
  {id: 'commitment:card-azul:2026-12-10', date: '2026-12-10', amount: '-50.00', label: 'Mensalidade', source_ref: 'commitment:card-azul:2026-12-10'},
  {id: 'commitment:installment:plan-ml:1-2:2026-11-05', date: '2026-11-05', amount: '-80.00', label: 'Mercado Livre', source_ref: 'commitment:installment:plan-ml:1-2:2026-11-05'},
  {id: 'commitment:installment:plan-ml:2-3:2026-12-05', date: '2026-12-05', amount: '-80.00', label: 'Mercado Livre', source_ref: 'commitment:installment:plan-ml:2-3:2026-12-05'},
  {id: 'commitment:claro:2026-01-31', date: '2026-01-31', amount: '-182.80', label: 'Claro', source_ref: 'commitment:claro:2026-01-31'},
], suggested_flows: [
  {id: 'obligation:fin', date: '2026-11-20', amount: '-10.00', label: 'financiamento · 2026-11', source_ref: 'obligation:fin'},
  {id: 'obligation:fin-dec', date: '2026-12-20', amount: '-10.00', label: 'financiamento · 2026-12', source_ref: 'obligation:fin-dec'},
], gaps: [], excluded_sources: []};
const scenario = (name, total, minimum, outside, breach = null) => ({name, purchase_total: total, minimum_balance: minimum, outside_horizon_total: outside, first_reserve_breach: breach});
const resultFor = count => ({
  price: '300.00', installments: count, work_hours: null, horizon,
  goal: count === 1
    ? {baseline_date: null, purchase_date: null, delay_days: null}
    : count === 2
      ? {baseline_date: '2027-06-01', purchase_date: null, delay_days: null}
      : count === 4
        ? null
        : {baseline_date: '2026-10-15', purchase_date: '2026-01-31', delay_days: 30},
  cash_scenarios: count === 3
    ? {status: 'insufficient_data', scenarios: []}
    : count === 4
      ? {status: 'scenario', scenarios: [scenario('baseline', '0.00', '500.00', '0.00'), scenario('buy_now', '400.00', '100.00', '0.00'), scenario('wait', '400.00', '150.00', '0.00'), scenario('reduce', '320.00', '180.00', '0.00')]}
      : {status: 'scenario', scenarios: [scenario('baseline', '0.00', '1000.00', '0.00'), scenario('buy_now', '300.00', '700.00', '0.00', '2026-01-31'), scenario('wait', '300.00', '800.00', '100.00'), scenario('reduce', '240.00', '760.00', '0.00')]},
  gaps: [
    'Informe renda líquida e horas trabalhadas no mês para calcular horas de trabalho.',
    count === 3 || count === 4 ? '' : 'A meta não foi alcançada no limite de dez anos destas premissas.',
    count === 3 ? 'Informe saldo inicial conhecido ou uma premissa explícita.' : '',
  ].filter(Boolean),
});

(async () => {
  const bodies = [];
  let backs = 0;
  const api = async (route, init = {}) => {
    if (String(route).includes('purchase-context')) return context;
    bodies.push(JSON.parse(init.body));
    return resultFor(JSON.parse(init.body).installments);
  };
  await mount(React.createElement(Purchase, {api, upload: async () => ({}), onBack() {backs += 1;}, ready: true}));
  await settle(400);
  const opened = visible();
  assert.match(opened, /15\/10\/2026/);
  assert.match(opened, /31\/01\/2026/);
  assert.match(opened, /Mensalidade/);
  assert.match(opened, /10\/11\/2026/);
  assert.match(opened, /10\/12\/2026/);
  assert.match(opened, /Claro/);
  assert.match(opened, /50,00/);
  assert.match(opened, /182,80/);
  assert.equal(opened.includes('ficaram fora da lista individual'), false);
  assert.equal(opened.includes('marque a fatura correspondente'), false);
  assert.equal(opened.includes('Fatura do cartão'), false);
  assert.equal(checked('Mensalidade, 10/11/2026'), true);
  assert.equal(checked('Mensalidade, 10/12/2026'), true);
  assert.equal(checked('Mercado Livre, 05/11/2026'), true);
  assert.equal(checked('Mercado Livre, 05/12/2026'), true);
  assert.equal(checked('Claro, 31/01/2026'), true);
  assert.equal(checked('Financiamento · 11/2026, 20/11/2026'), false);
  assert.equal(checked('Financiamento · 12/2026, 20/12/2026'), false);
  await pressExact('Limpar seleção de Mensalidade');
  assert.equal(checked('Mensalidade, 10/11/2026'), false);
  assert.equal(checked('Mensalidade, 10/12/2026'), false);
  assert.equal(checked('Mercado Livre, 05/11/2026'), true);
  assert.equal(checked('Claro, 31/01/2026'), true);
  await pressExact('Selecionar todo o Compromisso de Mensalidade');
  assert.equal(checked('Mensalidade, 10/11/2026'), true);
  assert.equal(checked('Mensalidade, 10/12/2026'), true);
  await pressExact('Limpar seleção de Mercado Livre');
  assert.equal(checked('Mercado Livre, 05/11/2026'), false);
  assert.equal(checked('Mercado Livre, 05/12/2026'), false);
  await pressExact('Selecionar todo o Compromisso de Mercado Livre');
  assert.equal(checked('Mercado Livre, 05/11/2026'), true);
  assert.equal(checked('Mercado Livre, 05/12/2026'), true);
  await pressExact('Selecionar todo o Compromisso de Financiamento');
  assert.equal(checked('Financiamento · 11/2026, 20/11/2026'), true);
  assert.equal(checked('Financiamento · 12/2026, 20/12/2026'), true);
  await pressExact('Limpar seleção de Financiamento');
  assert.equal(checked('Financiamento · 11/2026, 20/11/2026'), false);
  assert.equal(checked('Financiamento · 12/2026, 20/12/2026'), false);
  await pressExact('Limpar seleção');
  assert.equal(checked('Mensalidade, 10/11/2026'), false);
  assert.equal(checked('Mensalidade, 10/12/2026'), false);
  assert.equal(checked('Mercado Livre, 05/11/2026'), false);
  assert.equal(checked('Claro, 31/01/2026'), false);
  assert.equal(checked('Financiamento · 11/2026, 20/11/2026'), false);
  await pressExact('Selecionar Tudo');
  assert.equal(checked('Mensalidade, 10/11/2026'), true);
  assert.equal(checked('Mensalidade, 10/12/2026'), true);
  assert.equal(checked('Mercado Livre, 05/11/2026'), true);
  assert.equal(checked('Claro, 31/01/2026'), true);
  assert.equal(checked('Financiamento · 11/2026, 20/11/2026'), true);
  assert.equal(checked('Financiamento · 12/2026, 20/12/2026'), true);
  await pressExact('Limpar seleção de Financiamento');
  assert.equal(checked('Financiamento · 11/2026, 20/11/2026'), false);
  assert.equal(checked('Financiamento · 12/2026, 20/12/2026'), false);
  assert.match(field('Data do primeiro pagamento').props.value, /^\d{2}\/\d{2}\/\d{4}$/);
  forbid(opened);
  await input('Data do primeiro pagamento', futureDisplay);
  await settle(400);
  assert.equal(field('Data do primeiro pagamento').props.value, futureDisplay);
  await input('Preço total confirmado (R$)', '300,00');
  await press('Ver impacto da compra');
  await settle();
  const one = visible();
  assert.match(one, /1 parcela/);
  assert.equal(one.includes('3 parcelas'), false);
  assert.match(one, /Comprar agora/);
  assert.match(one, /Esperar 30 dias/);
  assert.match(one, /Alternativa 20% mais barata/);
  assert.match(one, /O preço é/);
  assert.match(one, /O menor saldo no período é/);
  assert.match(one, /Depois deste período ainda há/);
  assert.match(one, /O período já inclui todas as parcelas desta compra/);
  assert.equal(one.includes('R$ 0,00 em parcelas'), false);
  assert.match(one, /Dentro de dez anos, com estes aportes, a meta ainda não tem data/);
  assert.match(one, /Horas de trabalho não calculadas/);
  assert.equal(one.includes('Informe renda líquida'), false);
  assert.equal(one.includes('A meta não foi alcançada'), false);
  forbid(one);
  const priceStyle = renderer.root.findAllByType('Text').find(node => node.props.children === '1 parcela').props.style;
  const noteStyle = renderer.root.findAllByType('Text').find(node => String(node.props.children).includes('Horas de trabalho não calculadas')).props.style;
  const alternativeStyle = renderer.root.findAllByType('Text').find(node => String(node.props.children).startsWith('O preço é')).props.style;
  assert.notEqual(priceStyle, noteStyle);
  assert.notEqual(alternativeStyle, noteStyle);
  assert.equal(bodies[0].first_payment, futureIso);
  assert.equal(bodies[0].installments, 1);
  assert.equal(bodies[0].price, '300.00');
  await input('Preço total confirmado (R$)', '310,00');
  assert.equal(visible().includes('Sua análise'), false, 'Editing an input clears the previous result');
  await input('Número de parcelas', '3');
  await settle(400);
  await press('Ver impacto da compra');
  await settle();
  const three = visible();
  assert.match(three, /3 parcelas/);
  assert.match(three, /30 dias de adiamento da meta/);
  assert.match(three, /Sem esta compra, a meta ficaria para 15\/10\/2026/);
  assert.match(three, /Com a compra, fica para 31\/01\/2026/);
  assert.match(three, /Não calculada: falta o saldo inicial/);
  assert.match(three, /O menor saldo não foi calculado: o saldo inicial ficou em branco/);
  assert.match(three, /15\/10\/2026/);
  assert.match(three, /31\/01\/2026/);
  forbid(three);
  const both = `${one}\n${three}`;
  assert.match(both, /1 parcela/);
  assert.match(both, /3 parcelas/);
  assert.equal(bodies[1].installments, 3);
  assert.equal(bodies[1].first_payment, futureIso);
  assert.equal(bodies[1].monthly_net_income, null);
  assert.equal(bodies[1].opening_balance, null);
  await input('Número de parcelas', '2');
  await settle(400);
  await press('Ver impacto da compra');
  await settle();
  const partial = visible();
  assert.match(partial, /01\/06\/2027/);
  assert.match(partial, /Sem esta compra, a meta ficaria para 01\/06\/2027/);
  assert.match(partial, /Com a compra, não há data dentro de dez anos/);
  assert.equal(partial.includes('ainda não tem data'), false);
  assert.equal(partial.includes('A meta não foi alcançada'), false);
  forbid(partial);
  assert.equal(bodies[2].installments, 2);
  await input('Número de parcelas', '4');
  await settle(400);
  await press('Ver impacto da compra');
  await settle();
  const covered = visible();
  assert.equal(covered.includes('R$ 0,00 em parcelas'), false);
  assert.match(covered, /O período já inclui todas as parcelas desta compra/);
  assert.equal(covered.includes('Depois deste período ainda há'), false);
  await press('‹ Voltar');
  assert.equal(backs, 0, 'Back keeps unsaved input on the screen');
  assert.equal(dialogs.dialogSnapshot().title, 'Rascunho nesta tela');
  console.log('Purchase copy: civil dates, parcel phrases, and the three alternatives - passed');
  await act(async () => renderer.unmount());
})().catch(error => {console.error(error); process.exitCode = 1;});
