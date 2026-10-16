const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const moduleObject = {exports: {}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('utils/commitmentLists.ts', 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, {module: moduleObject, exports: moduleObject.exports, Error, Promise});
(async () => {
  const load = moduleObject.exports.loadCommitmentLists;
  let result = await load(async requestPath => {if (requestPath.includes('future-commitments')) throw new Error('502'); return {items: [{path: requestPath}]};});
  assert.equal(result.lists.managed.length, 1); assert.equal(result.lists.installments.length, 1);
  assert.equal(result.lists.obligations, undefined); assert.equal(result.errors.length, 1);
  result = await load(async requestPath => requestPath === '/v1/commitments' ? {} : {items: []});
  assert.equal(result.lists.managed, undefined); assert.equal(result.errors.length, 1);
  assert.equal(result.lists.obligations.length, 0);
  result = await load(async () => ({items: []})); assert.equal(result.errors.length, 0);
  assert.equal(Object.keys(result.lists).length, 3);
  const calls = [];
  result = await load(async requestPath => {calls.push(requestPath); if (requestPath === '/v1/commitments') return {items: [{id: 1}], has_more: true, next_offset: 1}; if (requestPath === '/v1/commitments?offset=1') return {items: [{id: 2}], has_more: false}; return {items: []};});
  assert.deepEqual(JSON.parse(JSON.stringify(result.lists.managed)), [{id: 1}, {id: 2}]);
  assert.ok(calls.includes('/v1/commitments?offset=1'));
  result = await load(async requestPath => requestPath.startsWith('/v1/commitments') ? {items: [{id: 1}], has_more: true, next_offset: 0} : {items: []});
  assert.equal(result.lists.managed, undefined); assert.equal(result.errors.length, 1);
  console.log('Commitments: partial outage preserves successful lists; malformed response is not empty history; retry converges — passed.');
})().then(renderRedesign).catch(error => {console.error(error); process.exitCode = 1;});

const section = process.argv[2] || 'all';
const wanted = name => section === 'all' || section === name;
let layoutWidth = 390;
let lastFlatList = null;
let renderer = null;
const failures = [];
const db = {getAllAsync: async () => [], runAsync: async () => {}};
const native = {
  useWindowDimensions: () => ({width: layoutWidth, height: 800, fontScale: 1}),
  Platform: {OS: 'android'}, StyleSheet: {create: value => value},
  Alert: {alert() {throw new Error('Native alert must not be used');}},
  BackHandler: {addEventListener: () => ({remove() {}})},
  AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  Keyboard: {dismiss() {}},
  FlatList: props => {lastFlatList = props; return React.createElement('View', {accessibilityLabel: 'Cronograma virtualizado'}, React.createElement('Text', null, String(props.data.length)));},
  Modal: ({children}) => React.createElement('Modal', null, children),
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'Image', 'ActivityIndicator', 'ScrollView'].map(name => [name, name])),
};
const routeCalls = [];
let routeParams = {month: '2026-11', periodo: '6'};
const mocks = {
  react: React, 'react-native': native, '../utils/queueDb': {useSQLiteContext: () => db}, './VoiceCapture': {__esModule: true, default: () => null},
  'expo-image-picker': {}, 'expo-sharing': {}, 'react-native-view-shot': {}, '../utils/devicePrompt': {},
  'expo-router': {useLocalSearchParams: () => routeParams, usePathname: () => '/', router: {push(href) {routeCalls.push(href);}, replace(href) {routeCalls.push(href);}, back() {routeCalls.push('back');}, canGoBack: () => true}},
  'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView', useSafeAreaInsets: () => ({top: 0, bottom: 0})},
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
const assertCutoff = require('node:assert/strict');
assertCutoff.equal(presentation.occurrenceState('2026-09-30', false, '2026-12-01'), 'Pago (assumido)');
assertCutoff.equal(presentation.occurrenceState('2026-10-10', false, '2026-12-01'), 'Vencido · sem vínculo');
assertCutoff.equal(presentation.occurrenceState('2026-10-11', false, '2026-10-11'), 'Previsto');
assertCutoff.equal(presentation.occurrenceState('2026-10-11', true, '2026-12-01'), 'Pago vinculado');
assertCutoff.deepEqual(presentation.pendingOccurrences('installment', {installments: [{status: 'projetada', valor: 10, data_vencimento: '2026-10-11', payment_link: {transaction_id: 'mov-1'}}, {status: 'projetada', valor: 10, data_vencimento: '2026-11-11'}]}, '2026-10-14').map(row => row.due), ['2026-11-11']);
assertCutoff.deepEqual(presentation.pendingOccurrences('obligation', {items: [{id: 'parcela-1', due_date: '2026-10-11', amount: 10, payment_link: {transaction_id: 'mov-1'}}, {id: 'parcela-2', due_date: '2026-11-11', amount: 10}]}, '2026-10-14').map(row => row.due), ['2026-11-11']);
const openCharge = presentation.nextOpenDue({next_due_date: '2026-10-10', frequency: 'mensal', kind: 'assinatura', renewal_status: 'automatica', linked_transactions: []}, '2026-10-11');
assertCutoff.equal(openCharge.due, '2026-10-10');
assertCutoff.equal(openCharge.state, 'Vencido · sem vínculo');
assertCutoff.equal(presentation.obligationShare(100, 3, 1), 33.33);
assertCutoff.equal(presentation.obligationShare(100, 3, 3), 33.34);
const monthDetail = load('utils/monthDetail.ts');
const profileHelpers = load('utils/commitmentProfile.ts');
const movementHelpers = load('utils/movementFilters.ts');
assertCutoff.equal(presentation.paymentMethodLabel('cartao'), 'Cartão de crédito');
assertCutoff.equal(presentation.paymentMethodLabel('cartao_credito'), 'Cartão de crédito');
assertCutoff.equal(presentation.paymentMethodLabel('debito'), 'Débito');
assertCutoff.equal(presentation.paymentMethodLabel('codigo_novo'), '');
assertCutoff.equal(profileHelpers.settlementLabel({paymentMethod: 'cartao'}), 'Cartão de crédito');
assertCutoff.equal(profileHelpers.paymentCodeLabel('cartao_credito'), 'Cartão de crédito');
assertCutoff.equal(movementHelpers.paymentLabel('cartao'), 'Cartão de crédito');
assertCutoff.equal(movementHelpers.paymentLabel('cartao_credito'), 'Cartão de crédito');
assertCutoff.equal(movementHelpers.paymentLabel('codigo_novo'), presentation.paymentMethodLabel('codigo_novo'));
assertCutoff.equal(movementHelpers.paymentLabel('codigo_novo'), '');
assertCutoff.equal(movementHelpers.classificationLine({natureza: 'COMPRA', categoria: 'Outros', metodo_pagamento: 'codigo_novo'}).includes('codigo'), false);
assertCutoff.equal(monthDetail.chargePaymentLabel({payment_method: 'cartao'}), 'Cartão de crédito');
assertCutoff.equal(monthDetail.chargePaymentLabel({payment_method: 'cartao_credito'}), 'Cartão de crédito');
const taxShare = {status: 'ativo', kind: 'obrigacao', obligation_plan: 'parcelado', installment_count: 3, next_installment_number: 3, purchase_date: '2026-08-10', next_due_date: '2026-11-10', amount: 100, frequency: 'mensal', renewal_status: 'nao_aplicavel', linked_transactions: []};
const assumedShare = presentation.settledRows('managed', taxShare, '2026-12-01');
assertCutoff.equal(assumedShare.length, 1);
assertCutoff.equal(assumedShare[0].due, '2026-09-10');
assertCutoff.equal(assumedShare[0].amount, 33.33);
assertCutoff.equal(assumedShare[0].state, 'Pago (assumido)');
const linkedShare = presentation.settledRows('managed', {...taxShare, linked_transactions: [{due_date: '2026-09-10', transaction_id: 'tx-1'}]}, '2026-12-01');
assertCutoff.equal(linkedShare.length, 1);
assertCutoff.equal(linkedShare[0].amount, 33.33);
assertCutoff.equal(linkedShare[0].state, 'Pago vinculado');
assertCutoff.notEqual(linkedShare[0].amount, 100);
const occurrenceLink = {status: 'ativo', kind: 'assinatura', frequency: 'mensal', renewal_status: 'automatica', purchase_date: '2026-08-10', next_due_date: '2026-11-10', amount: 40, linked_transactions: [{occurrence_due_date: '2026-09-10', paid_date: '2026-09-12', actual_amount: '40'}]};
const occurrenceRows = presentation.settledRows('managed', occurrenceLink, '2026-12-01');
assertCutoff.equal(occurrenceRows.filter(row => row.state === 'Pago vinculado').length, 1);
assertCutoff.equal(occurrenceRows.filter(row => row.state === 'Pago vinculado')[0].amount, 40);
assertCutoff.equal(occurrenceRows.some(row => row.state === 'Pago (assumido)' && row.due === '2026-09-10'), false);
assertCutoff.equal(presentation.pendingOccurrences('managed', occurrenceLink, '2026-12-01').some(row => row.due === '2026-09-10'), false);
assertCutoff.equal(presentation.nextOpenDue({next_due_date: '2026-09-10', frequency: 'mensal', kind: 'obrigacao', obligation_plan: 'a_vista', renewal_status: 'nao_aplicavel', linked_transactions: []}, '2026-12-01'), null);
const octoberCash = presentation.nextOpenDue({next_due_date: '2026-10-10', frequency: 'mensal', kind: 'obrigacao', obligation_plan: 'a_vista', renewal_status: 'nao_aplicavel', linked_transactions: []}, '2026-12-01');
assertCutoff.equal(octoberCash.due, '2026-10-10');
assertCutoff.equal(octoberCash.state, 'Vencido · sem vínculo');
const splitCharge = presentation.nextOpenDue({next_due_date: '2026-09-10', frequency: 'mensal', kind: 'obrigacao', obligation_plan: 'parcelado', installment_count: 3, next_installment_number: 1, renewal_status: 'nao_aplicavel', linked_transactions: []}, '2026-12-01');
assertCutoff.equal(splitCharge.due, '2026-10-10');
assertCutoff.equal(splitCharge.state, 'Vencido · sem vínculo');
assertCutoff.equal(splitCharge.steps, 1);
const monthEnd = {status: 'ativo', kind: 'parcelamento', frequency: 'mensal', renewal_status: 'nao_aplicavel', next_due_date: '2026-01-31', schedule_anchor_day: 31, schedule_anchor_end: true, installment_count: 3, next_installment_number: 1, amount: 10, linked_transactions: []};
assertCutoff.deepEqual([0, 1, 2].map(step => presentation.anchoredDue(monthEnd, step)), ['2026-01-31', '2026-02-28', '2026-03-31']);
const restored = {status: 'ativo', kind: 'assinatura', frequency: 'mensal', renewal_status: 'automatica', next_due_date: '2026-02-28', schedule_anchor_day: 31, schedule_anchor_end: true, amount: 10, linked_transactions: []};
assertCutoff.equal(presentation.nextOpenDue(restored, '2026-10-14').due, '2026-10-31');
const afterShortMonth = presentation.pendingOccurrences('managed', {...restored, next_due_date: '2026-09-30', future_cutoff: '2026-12-01'}, '2026-10-14');
assertCutoff.equal(afterShortMonth.map(row => row.due).join(','), '2026-10-31,2026-11-30');
assertCutoff.equal(presentation.nextOpenDue({...restored, future_cutoff: '2026-03-01'}, '2026-03-01'), null);
assertCutoff.equal(presentation.nextOpenDue({...restored, linked_transactions: [{due_date: '2026-02-28', occurrence_key: 'occ', settled_next: false}]}, '2026-03-01'), null);
presentation.setPresentationClock(null);
const saopauloNight = presentation.civilMonthWindow(new Date('2026-10-01T02:30:00Z'));
assertCutoff.equal(saopauloNight.yearStart, '2026-01-01');
assertCutoff.equal(saopauloNight.monthStart, '2026-09-01');
assertCutoff.equal(saopauloNight.monthEnd, '2026-09-30');
presentation.setPresentationClock('2026-10-01');
assertCutoff.equal(presentation.civilMonthWindow(new Date('2026-01-01T00:00:00Z')).monthStart, '2026-10-01');
presentation.setPresentationClock(null);
console.log('PASS anchoredDue month-end, future_cutoff, scheduleConflict, civilMonthWindow');
const Manager = load('components/CommitmentManager.tsx').default;
const Projection = load('components/CommitmentProjection.tsx').default;
const Editor = load('components/FutureCommitmentEditor.tsx').default;
const dialogs = load('utils/appAlert.ts');
const settle = (ms = 20) => act(async () => {await new Promise(resolve => setTimeout(resolve, ms));});
const text = () => renderer.root.findAllByType('Text').map(node => [].concat(node.props.children).flat(Infinity).filter(item => typeof item === 'string' || typeof item === 'number').join('')).join('\n');
const buttons = label => renderer.root.findAllByType('Pressable').filter(node => node.props.accessibilityLabel === label || node.findAllByType('Text').some(child => child.props.children === label));
const button = label => {const found = buttons(label); assert.ok(found.length, 'Button: ' + label); return found[0];};
const press = label => act(async () => {const node = button(label); assert.ok(!node.props.disabled, label + ' enabled'); const result = node.props.onPress(); if (result && result.then) await result;});
const dismiss = async label => {
  const current = dialogs.dialogSnapshot();
  assert.ok(current, 'Dialog open');
  const chosen = current.buttons.find(item => item.text === label);
  assert.ok(chosen, 'Dialog button: ' + label);
  await act(async () => {dialogs.dismissDialog(chosen, current);});
  await settle();
};
const ScheduleHost = load('components/ScheduleOverlay.tsx').ScheduleOverlayProvider;
const ChoiceHost = load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider;
function screen(api, extra = {}) {
  return React.createElement(ChoiceHost, null, React.createElement(ScheduleHost, null, React.createElement(Manager, {api, upload: async () => ({}), accounts: [], cards: extra.cards || [], money: String, onUpdated() {}, ...extra})));
}
function obligationsApi(items, writes) {
  return async (requestPath, init = {}) => {
    const clean = String(requestPath).split('?')[0];
    const parts = clean.split('/');
    writes.push({path: clean, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null});
    if (clean === '/v1/future-commitments/obligations') return {items};
    if (clean === '/v1/commitments' || clean === '/v1/installments') return {items: []};
    if (clean.startsWith('/v1/commitment-projection')) return {items: [{month: '2026-10', total: '0', installments: '0', subscriptions: '0', other: '0', estimated: '0', bill_balance: '0', items: []}], months: 1, start_month: '2026-10', end_month: '2026-10', last_installment_month: null, warnings: [], total: '0', as_of: '2026-10-11', automatic_horizon_capped: false};
    const identity = decodeURIComponent(parts[4] || '');
    if (clean.endsWith('/undo')) return {restored: true, source: 'obligation', identity};
    if (init.method === 'DELETE') return {persisted: true, source: 'obligation', identity, delete_request_id: identity + '-delete'};
    return {preview_token: 'a'.repeat(64), editable_count: 1, rows: [{row_id: 1, amount: '59.80', due_date: '2026-11-01', editable: true}], source: 'obligation', identity};
  };
}
async function check(name, work) {
  if (!wanted(name)) return;
  lastFlatList = null;
  dialogs.clearDialogs();
  try {await work(); console.log('PASS', name);}
  catch (error) {failures.push(name); console.error('FAIL', name, error);}
  finally {if (renderer) {await act(async () => renderer.unmount()); renderer = null;} await settle(); dialogs.clearDialogs();}
}
async function renderRedesign() {
  await check('obligation-settled', async () => {
    presentation.setPresentationClock('2026-12-01');
    try {
      const tax = {commitment_id: 'ipva', name: 'IPVA sintetico', revision: 1, kind: 'obrigacao', obligation_plan: 'parcelado', installment_count: 3, next_installment_number: 3, purchase_date: '2026-08-10', next_due_date: '2026-11-10', cash_due_date: '2026-09-10', amount: 100, frequency: 'mensal', payment_method: 'cartao', renewal_status: 'nao_aplicavel', status: 'ativo', linked_transactions: []};
      await act(async () => {renderer = create(screen(async requestPath => String(requestPath).split('?')[0] === '/v1/commitments' ? {items: [tax]} : {items: []}));});
      await settle();
      assert.match(text(), /Cartão de crédito/);
      assert.doesNotMatch(text(), /\bcartao\b/);
      await press('Abrir IPVA sintetico');
      await press('Pagas (1)');
      assert.match(text(), /10\/09\/2026 · 33\.33 · Pago \(assumido\)/);
      await press('Ver cronograma de IPVA sintetico');
      await press('Pagas');
      const paidRow = lastFlatList.data.find(item => item.kind === 'row' && item.state === 'Pago (assumido)');
      assert.equal(paidRow.due, '2026-09-10');
      assert.equal(paidRow.amount, 33.33);
      assert.match(JSON.stringify(lastFlatList.renderItem({item: paidRow, index: 1})), /Pago \(assumido\)/);
    } finally {
      presentation.setPresentationClock(null);
    }
  });

  await check('grouping', async () => {
    const series = Array.from({length: 200}, (_, index) => ({id: `finance-${index}`, tipo: 'financiamento', external_key: `obligation|financiamento-imobiliario|parcela-${index + 1}`, due_date: index === 199 ? '2090-01-01' : '2026-11-01', amount: '10.00', name: `financiamento · 2026-11`}));
    const frozen = JSON.stringify(series);
    const other = [{id: 'mortgage-a', tipo: 'financiamento', origem_id: 'mortgage-a', descricao: 'financiamento', due_date: '2026-12-01', amount: '10'}, {id: 'mortgage-b', tipo: 'financiamento', origem_id: 'mortgage-b', descricao: 'financiamento', due_date: '2026-12-01', amount: '10'}];
    const writes = [];
    layoutWidth = 390;
    await act(async () => {renderer = create(screen(obligationsApi(series, writes)));});
    await settle();
    assert.equal(buttons('Abrir Financiamento').length, 1);
    assert.equal(buttons('Ver cronograma de Financiamento').length, 0);
    assert.equal(JSON.stringify(series), frozen);
    await press('Abrir Financiamento');
    assert.equal(buttons('Editar compromisso Financiamento').length, 1);
    await press('Mais ações de Financiamento');
    assert.equal(buttons('Encerrar Financiamento').length, 1);
    assert.equal(buttons('Remover parcelas futuras de Financiamento').length, 1);
    assert.equal(buttons('Editar futuras de Financiamento').length, 1);
    await press('Mais ações de Financiamento');
    await press('Ver cronograma de Financiamento');
    await settle();
    const scheduleRows = lastFlatList.data.filter(item => item.kind === 'row');
    assert.equal(scheduleRows.length, 200);
    assert.ok(lastFlatList.data.some(item => item.kind === 'year' && item.year === '2026'));
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Cronograma de Financiamento'})[0].props.style.position, 'absolute');
    assert.ok(lastFlatList.initialNumToRender < scheduleRows.length);
    assert.equal(lastFlatList.windowSize, 5);
    assert.equal(lastFlatList.scrollEnabled, true);
    assert.equal(lastFlatList.style.maxHeight, undefined);
    const toggleYear = async year => {
      const header = lastFlatList.data.find(item => item.kind === 'year' && item.year === year);
      await act(async () => {lastFlatList.renderItem({item: header, index: 0}).props.onPress();});
    };
    await toggleYear('2026');
    assert.ok(lastFlatList.data.some(item => item.kind === 'year' && item.year === '2026'));
    assert.equal(lastFlatList.data.filter(item => item.kind === 'row' && String(item.due).startsWith('2026')).length, 0);
    await toggleYear('2026');
    assert.ok(lastFlatList.data.filter(item => item.kind === 'row' && String(item.due).startsWith('2026')).length > 0);
    assert.match(text(), /Restante 2000[\s\S]*200 parcelas · até 01\/2090/);
    assert.match(text(), /Dívida · /);
    assert.doesNotMatch(text(), /600 parcelas|soma das parcelas previstas/);
    assert.doesNotMatch(text(), /próxima R\$|próxima 10/);
    assert.doesNotMatch(text(), /Encerrar no app é registro/);
    const rendered = JSON.stringify(lastFlatList.renderItem({item: lastFlatList.data.find(item => String(item.due || '').includes('2090')), index: 0}));
    assert.match(rendered, /2090|01\/01\/2090/);
    assert.doesNotMatch(rendered, /Previsto/);
    assert.equal(writes.filter(item => item.method === 'DELETE').length, 0);
    await act(async () => renderer.unmount()); renderer = null;
    await act(async () => {renderer = create(screen(obligationsApi(other, [])));});
    await settle();
    assert.equal(buttons('Abrir Financiamento').length, 2);
    const managed = {commitment_id: 'energy', name: 'Energia', revision: 1, kind: 'assinatura', purchase_date: '2026-10-01', next_due_date: '2026-12-01', amount: 100, amount_mode: 'last_three_bills', amount_estimate: {requires_review: false}, frequency: 'mensal', payment_method: 'debito', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-12-01'}]};
    const plans = [
      {id: 'one', descricao: 'FARMACIA CENTRO', display_name: 'Farmácia', numero_parcelas: 6, data_compra: '2026-01-01', card_label: '4242 •••• 4242', installments: [{status: 'projetada', valor: 10}]},
      {id: 'six', descricao: 'Compra sintética', numero_parcelas: 6, data_compra: '2026-01-01', card_label: 'Cartão exemplo 4242', installments: Array.from({length: 6}, () => ({status: 'projetada', valor: 10}))},
    ];
    await act(async () => renderer.update(screen(async requestPath => requestPath === '/v1/commitments' ? {items: [managed]} : requestPath === '/v1/installments' ? {items: plans} : {items: []})));
    await settle();
    assert.match(text(), /Débito/); assert.doesNotMatch(text(), /\bdebito\b/);
    assert.match(text(), /Conta · /); assert.doesNotMatch(text(), /Assinatura · mensal/);
    assert.match(text(), /~/); assert.doesNotMatch(text(), /Estimado/);
    assert.match(text(), /Farmácia/); assert.match(text(), /FARMACIA CENTRO/);
    await press('Abrir Farmácia');
    assert.equal(buttons('Editar compromisso Farmácia').length, 1);
    await press('Mais ações de Farmácia');
    assert.equal(buttons('Encerrar Farmácia').length, 1);
    assert.equal(buttons('Editar futuras de Farmácia').length, 1);
    await press('Voltar de Detalhe de Farmácia');
    assert.match(text(), /•••• 4242/); assert.doesNotMatch(text(), /4242 •••• 4242/);
    assert.match(text(), /Cartão exemplo 4242/);
    assert.match(text(), /Restante 10[\s\S]*1 parcela/); assert.match(text(), /Restante 60[\s\S]*6 parcelas/);
    assert.doesNotMatch(text(), /Valor aproximado/);
    assert.doesNotMatch(text(), /0 pagamento/);
    assert.doesNotMatch(text(), /Editar compromisso/);
    assert.doesNotMatch(text(), /Vincular movimentação paga/);
    await press('Abrir Energia');
    assert.match(text(), /Estimado/);
    assert.match(text(), /Restante/);
    assert.match(text(), /Termina em/);
    assert.match(text(), /— \(sem data final\)/);
    await press('Ver cronograma de Energia');
    await settle();
    assert.match(text(), /sem data final/);
    assert.doesNotMatch(text(), /Projeção de 12 meses/);
    assert.match(text(), /pagas · /);
    assert.match(text(), /a pagar/);
    await press('Fechar cronograma de Energia');
    await press('Voltar de Detalhe de Energia');
    const cancelled = {commitment_id: 'gone', name: 'ChatGPT', revision: 1, kind: 'assinatura', purchase_date: '2024-01-01', next_due_date: '2024-02-01', amount: 20, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'cancelado', cancelled_at: '2026-03-01', schedule: [], linked_transactions: []};
    const zeroPlan = {id: 'zero', descricao: 'Compra encerrada', numero_parcelas: 2, data_compra: '2026-01-01', card_label: '•••• 1111', installments: [{status: 'paga', valor: 40, data_vencimento: '2026-02-01'}, {status: 'projetada', valor: 0}]};
    const services = {...managed, commitment_id: 'services', name: 'Servicos essenciais', amount_mode: 'fixed', amount_estimate: null};
    await act(async () => renderer.update(screen(async requestPath => requestPath === '/v1/commitments' ? {items: [managed, cancelled, services]} : requestPath === '/v1/installments' ? {items: [...plans, zeroPlan]} : {items: []})));
    await settle();
    assert.match(text(), /Serviços essenciais/);
    assert.match(text(), /Concluídos e cancelados \(2\)/);
    assert.doesNotMatch(text(), /ChatGPT/);
    assert.doesNotMatch(text(), /Compra encerrada/);
    await press('Concluídos e cancelados');
    assert.match(text(), /ChatGPT/);
    assert.match(text(), /cancelado em 01\/03\/2026/);
    assert.match(text(), /Compra encerrada/);
    assert.match(text(), /último valor 40/);
    assert.doesNotMatch(text(), /concluído em 01\/01\/2026/);
    assert.equal(buttons('Vincular movimentação paga').length, 0);
    const inactivePrimary = renderer.root.findAllByType('Pressable').filter(node => String(node.props.accessibilityLabel || '').includes('ChatGPT') && [node.props.style].flat(Infinity).some(style => style && style.backgroundColor === '#49C5AA'));
    assert.equal(inactivePrimary.length, 0);
  });

  await check('undo', async () => {
    const writes = [];
    const api = async (requestPath, init = {}) => {
      writes.push({path: String(requestPath).split('?')[0], method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null});
      if (init.method === 'DELETE') return {persisted: true, source: 'installment', identity: 'plan', delete_request_id: 'delete-1'};
      if (String(requestPath).endsWith('/undo')) return {restored: true, source: 'installment', identity: 'plan'};
      return {preview_token: 'b'.repeat(64), editable_count: 2, source: 'installment', identity: 'plan', rows: [{row_id: 1, amount: '10.00', due_date: '2026-10-20', editable: true}, {row_id: 2, amount: '20.50', due_date: '2026-11-20', editable: true}]};
    };
    await act(async () => {renderer = create(React.createElement(Editor, {source: 'installment', identity: 'plan', name: 'Sintético', api, onUpdated() {}}));});
    await press('Remover parcelas futuras de Sintético');
    await settle();
    assert.match(text(), /Remover 2 parcelas futuras de Sintético — R\$ 30,50/);
    assert.equal(writes.filter(item => item.method === 'DELETE').length, 0);
    await press('Confirmar remoção');
    await settle();
    assert.equal(writes.filter(item => item.method === 'DELETE').length, 1);
    await dismiss('OK');
    await press('Desfazer exclusão de futuras');
    await settle();
    const undo = writes.find(item => item.path.endsWith('/undo'));
    assert.equal(undo.method, 'POST');
    assert.equal(undo.body.delete_request_id, 'delete-1');
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const projection = {items: [{month: '2026-10', total: '0', installments: '0', subscriptions: '0', other: '0', estimated: '0', bill_balance: '0', items: []}], months: 1, start_month: '2026-10', end_month: '2026-10', last_installment_month: null, warnings: [], total: '0', as_of: '2026-10-11', automatic_horizon_capped: false};
    const routed = (writes, lists) => async (requestPath, init = {}) => {
      const clean = String(requestPath).split('?')[0];
      const parts = clean.split('/');
      writes.push({path: clean, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null});
      if (clean === '/v1/commitments') return {items: lists.managed()};
      if (clean === '/v1/installments') return {items: lists.installments()};
      if (clean === '/v1/future-commitments/obligations') return {items: lists.obligations()};
      if (clean.startsWith('/v1/commitment-projection')) return projection;
      const source = parts[3];
      const identity = decodeURIComponent(parts[4] || '');
      if (clean.endsWith('/undo')) {lists.restore(source, identity); return {restored: true, source, identity};}
      if (init.method === 'DELETE') {lists.remove(source, identity); return {persisted: true, source, identity, delete_request_id: identity + '-delete'};}
      return {preview_token: 'a'.repeat(64), editable_count: 1, rows: [{row_id: 1, amount: '59.80', due_date: '2026-12-01', editable: true}], source, identity};
    };
    const series = [1, 2, 3].map(number => ({id: `row-${number}`, tipo: 'financiamento', external_key: 'obligation|financiamento-imobiliario|parcela-' + number, due_date: `2026-12-0${number}`, amount: '59.80'}));
    const removed = new Set();
    const seriesWrites = [];
    await act(async () => {renderer = create(screen(routed(seriesWrites, {
      managed: () => [], installments: () => [], obligations: () => series.filter(item => !removed.has(item.id)),
      remove: (_source, identity) => removed.add(identity), restore: (_source, identity) => removed.delete(identity),
    })));});
    await settle();
    await press('Abrir Financiamento');
    await press('Mais ações de Financiamento');
    assert.doesNotMatch(text(), /Remover 3 parcelas futuras/);
    assert.equal(seriesWrites.filter(item => item.method === 'DELETE').length, 0);
    await press('Remover parcelas futuras de Financiamento');
    assert.match(dialogs.dialogSnapshot().message, /Remover 3 parcelas futuras de Financiamento — R\$ 179,40/);
    assert.match(dialogs.dialogSnapshot().message, /não quita nem altera o contrato/);
    assert.equal(seriesWrites.filter(item => item.method === 'DELETE').length, 0);
    await dismiss('Confirmar remoção de Financiamento');
    await settle();
    assert.equal(seriesWrites.filter(item => item.method === 'DELETE').length, 3);
    assert.ok(seriesWrites.filter(item => item.path === '/v1/future-commitments/obligations').length >= 2);
    assert.equal(buttons('Ver cronograma de Financiamento').length, 0);
    await dismiss('OK');
    assert.equal(buttons('Desfazer exclusão de Financiamento').length, 1);
    await press('Desfazer exclusão de Financiamento');
    await settle();
    assert.equal(seriesWrites.filter(item => item.path.endsWith('/undo') && item.method === 'POST').length, 3);
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const managed = {commitment_id: 'stream', name: 'Streaming', revision: 1, kind: 'assinatura', purchase_date: '2026-01-01', next_due_date: '2026-12-01', amount: 20, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-12-01'}], linked_transactions: []};
    let managedGone = false;
    const managedWrites = [];
    await act(async () => {renderer = create(screen(routed(managedWrites, {
      managed: () => managedGone ? [] : [managed], installments: () => [], obligations: () => [],
      remove: source => {if (source === 'managed') managedGone = true;}, restore: source => {if (source === 'managed') managedGone = false;},
    })));});
    await settle();
    await press('Abrir Streaming');
    await press('Mais ações de Streaming');
    await press('Remover parcelas futuras de Streaming');
    await settle();
    assert.match(dialogs.dialogSnapshot().message, /não cancela o serviço no fornecedor/);
    await dismiss('Confirmar remoção');
    await settle();
    assert.equal(buttons('Mais ações de Streaming').length, 0);
    await dismiss('OK');
    await press('Desfazer exclusão de Streaming');
    await settle();
    const managedUndo = managedWrites.find(item => item.path.endsWith('/undo'));
    assert.equal(managedUndo.method, 'POST');
    assert.equal(managedUndo.path, '/v1/future-commitments/managed/stream/undo');
    assert.equal(managedUndo.body.delete_request_id, 'stream-delete');
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const plan = {id: 'plan-1', descricao: 'Compra sintética', numero_parcelas: 2, data_compra: '2026-01-01', card_label: '•••• 4242', installments: [{status: 'projetada', valor: 10}, {status: 'projetada', valor: 10}]};
    let planClosed = false;
    const planWrites = [];
    await act(async () => {renderer = create(screen(routed(planWrites, {
      managed: () => [], obligations: () => [],
      installments: () => [planClosed ? {...plan, installments: plan.installments.map(part => ({...part, status: 'cancelada'}))} : plan],
      remove: source => {if (source === 'installment') planClosed = true;}, restore: source => {if (source === 'installment') planClosed = false;},
    })));});
    await settle();
    await press('Abrir Compra sintética');
    await press('Mais ações de Compra sintética');
    await press('Remover parcelas futuras de Compra sintética');
    await settle();
    await dismiss('Confirmar remoção');
    await settle();
    assert.equal(renderer.root.findAllByType('Pressable').filter(node => node.props.accessibilityLabel === 'Desfazer exclusão de futuras').length, 0);
    assert.equal(buttons('Desfazer exclusão de Compra sintética').length, 1);
    await dismiss('OK');
    await press('Desfazer exclusão de Compra sintética');
    await settle();
    const planUndo = planWrites.find(item => item.path.endsWith('/undo'));
    assert.equal(planUndo.path, '/v1/future-commitments/installment/plan-1/undo');
    assert.equal(planUndo.body.delete_request_id, 'plan-1-delete');
  });

  await check('reconcile', async () => {
    const writes = [];
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
    const overdue = {commitment_id: 'overdue', name: 'Conta vencida', revision: 1, kind: 'assinatura', purchase_date: '2020-01-01', next_due_date: today, amount: 80, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: today}], linked_transactions: []};
    const upcoming = {commitment_id: 'next', name: 'Streaming', revision: 1, kind: 'assinatura', purchase_date: '2026-01-01', next_due_date: '2026-12-01', amount: 20, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-12-01'}], linked_transactions: []};
    const paid = {...upcoming, commitment_id: 'paid', name: 'Pago', next_due_date: '2026-12-15', linked_transactions: [{due_date: '2026-12-15', transaction_id: 'paid-link', actual_amount: '20'}]};
    const candidate = {id: 'pay-1', source_kind: 'transaction', source_token: 'token-1', merchant_raw: 'Conta sintetica', valor: -80, data_evento: '2020-01-10T12:00:00Z'};
    const distant = {id: 'pay-far', source_kind: 'transaction', source_token: 'token-far', merchant_raw: 'Padaria distante', valor: -12, data_evento: '2026-01-15T15:00:00Z', linkable: true};
    const api = async (requestPath, init = {}) => {
      if (init.method === 'POST') {writes.push({path: String(requestPath), ...JSON.parse(init.body)}); return {persisted: true, revision: 2};}
      if (String(requestPath).endsWith('/payment-candidates')) return {items: [candidate], commitment_revision: 1};
      if (String(requestPath).includes('/statement-search')) return {items: [candidate, distant], commitment_revision: 1, total: 2, truncated: false};
      if (requestPath === '/v1/commitments') return {items: [overdue, upcoming, paid]};
      if (requestPath === '/v1/installments') return {items: [{id: 'loan', descricao: 'Financiamento', display_name: 'Financiamento', installments: [{status: 'projetada', valor: 900, data_vencimento: `${today.slice(0, 8)}11`}, {status: 'projetada', valor: 900, data_vencimento: '2026-12-11'}]}]};
      return {items: []};
    };
    await act(async () => {renderer = create(screen(api));});
    await settle();
    const reconcileBox = renderer.root.findAllByProps({accessibilityLabel: 'A conciliar'})[0];
    const reconcileText = reconcileBox.findAllByType('Text').map(node => [].concat(node.props.children).flat(Infinity).filter(item => typeof item === 'string' || typeof item === 'number').join('')).join('\n');
    assert.match(reconcileText, /A conciliar \(2\)/);
    assert.match(reconcileText, /Financiamento/);
    assert.match(reconcileText, /Conta vencida/);
    assert.doesNotMatch(reconcileText, /Streaming/);
    assert.equal(buttons('Conciliar Conta vencida').length, 1);
    assert.equal(buttons('Conciliar Financiamento').length, 1);
    await press('Conciliar Financiamento');
    await settle();
    assert.match(text(), /Mais prováveis/);
    assert.equal(renderer.root.findAllByType('TextInput').some(node => node.props.accessibilityLabel === 'Buscar por nome ou valor'), true);
    assert.doesNotMatch(text(), /não grava no plano/);
    await press('Conta sintetica');
    await press('Vincular este lançamento');
    await settle();
    assert.equal(writes.length, 1);
    assert.match(writes[0].path, /\/v1\/future-commitments\/installment\/loan\/payment$/);
    assert.equal(writes[0].transaction_id, 'pay-1');
    assert.equal(writes[0].due_date, `${today.slice(0, 8)}11`);
    await dismiss('OK');
    await settle();
    assert.doesNotMatch(reconcileText, /Confirmar/);
    assert.doesNotMatch(reconcileText, /Estimado/);
    if (`${today.slice(0, 8)}11` < today) assert.match(text(), /Vencido · sem vínculo/);
    else assert.doesNotMatch(text(), /Vencido · sem vínculo/);
    assert.doesNotMatch(text(), /Pago \(assumido\)/);
    assert.equal(buttons('Abrir Pago').length, 1);
    assert.equal(writes.length, 1);
    await press('Abrir Conta vencida');
    await press('Ver cronograma de Conta vencida');
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
    const scheduleHost = renderer.root.findAllByProps({accessibilityLabel: 'Cronograma de Conta vencida'})[0];
    assert.equal(scheduleHost.props.style.position, 'absolute');
    const dueRow = lastFlatList.data.find(item => item.kind === 'row' && item.due === today);
    assert.match(JSON.stringify(lastFlatList.renderItem({item: dueRow, index: 1})), new RegExp('Vincular parcela ' + today + ' de Conta vencida'));
    assert.match(text(), /a pagar/);
    await press('Pagas');
    assert.ok(lastFlatList.data.some(item => item.statusText === 'Pago (assumido)'));
    await press('A pagar');
    assert.ok(lastFlatList.data.some(item => item.kind === 'row' && item.due === today));
    await press('Fechar cronograma de Conta vencida');
    await press('Vincular movimentação de Conta vencida');
    await settle();
    const linkScreen = renderer.root.findAllByProps({accessibilityLabel: 'Vincular pagamento'})[0];
    assert.ok(linkScreen);
    assert.equal(linkScreen.findAllByProps({accessibilityLabel: 'Fechar'}).length, 0);
    assert.match(text(), /Mais prováveis/);
    assert.equal(renderer.root.findAllByType('TextInput').some(node => node.props.accessibilityLabel === 'Buscar por nome ou valor' && node.props.placeholder === 'Buscar por nome ou valor'), true);
    assert.doesNotMatch(text(), /Buscar no extrato/);
    assert.equal(writes.length, 1);
    await press('Conta sintetica');
    await press('Vincular este lançamento');
    await settle();
    assert.equal(writes.length, 2);
    assert.match(writes[1].path, /\/v1\/commitments\/overdue\/payment$/);
    assert.equal(writes[1].transaction_id, 'pay-1');
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const ambiguous = {...candidate, requires_occurrence_selection: true, occurrence_options: [{due_date: '2020-01-10'}, {due_date: '2020-01-17'}], next_occurrence_due_date: '2020-01-10'};
    const ambiguousWrites = [];
    await act(async () => {renderer = create(screen(async (requestPath, init = {}) => {
      if (init.method) ambiguousWrites.push(init.method);
      if (String(requestPath).endsWith('/payment-candidates')) return {items: [ambiguous, {...ambiguous, id: 'pay-2'}], commitment_revision: 1};
      if (String(requestPath).includes('/statement-search')) return {items: [ambiguous, {...ambiguous, id: 'pay-2'}], commitment_revision: 1, total: 2, truncated: false};
      if (requestPath === '/v1/commitments') return {items: [overdue]};
      return {items: []};
    }));});
    await settle();
    await press('Abrir Conta vencida');
    await press('Vincular movimentação de Conta vencida');
    await settle();
    assert.equal(dialogs.dialogSnapshot(), null);
    assert.equal(ambiguousWrites.length, 0);
    assert.match(text(), /Conta sintetica/);
  });

  await check('statement-link', async () => {
    const writes = [];
    const Link = load('components/StatementCommitmentLink.tsx').default;
    const item = {id: 'tx-1', source_token: 'tok', valor: -40, data_evento: '2026-10-01T15:00:00Z', merchant_raw: 'Padaria distante', movement_source: 'ledger'};
    const commitment = {commitment_id: 'energy', name: 'Energia', revision: 3, amount: 50, status: 'ativo', kind: 'assinatura', next_due_date: '2026-12-01'};
    let linked = null;
    const api = async (requestPath, init = {}) => {
      if (init.method === 'POST') {writes.push(JSON.parse(init.body)); return {persisted: true};}
      if (requestPath === '/v1/commitments') return {items: [commitment, {commitment_id: 'old', name: 'Encerrado', status: 'cancelado', amount: 1, revision: 1}]};
      return {items: []};
    };
    await act(async () => {renderer = create(React.createElement(Link, {item, api, money: String, onLinked: value => {linked = value;}}));});
    await settle();
    assert.equal(buttons('Vincular a compromisso').length, 1);
    await press('Vincular a compromisso');
    await settle();
    assert.equal(buttons('Vincular a Energia').length, 1);
    assert.equal(buttons('Vincular a Encerrado').length, 0);
    await press('Vincular a Energia');
    await settle();
    assert.match(dialogs.dialogSnapshot().message, /Diferença/);
    await dismiss('Vincular');
    await settle();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].transaction_id, 'tx-1');
    assert.equal(writes[0].expected_revision, 3);
    assert.equal(writes[0].expected_source_token, 'tok');
    assert.equal(linked.name, 'Energia');
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const linkedItem = {...item, commitment: {id: 'energy', name: 'Energia', kind: 'assinatura'}};
    const unlinkWrites = [];
    await act(async () => {renderer = create(React.createElement(Link, {item: linkedItem, api: async (requestPath, init = {}) => {
      if (init.method === 'POST') {unlinkWrites.push({path: requestPath, body: JSON.parse(init.body)}); return {agenda_restored: true, warning: null};}
      if (requestPath === '/v1/commitments') return {items: [commitment]};
      return {items: []};
    }, money: String, onLinked() {}, onUnlinked() {}}));});
    await settle();
    assert.match(text(), /Vinculado a Energia/);
    assert.equal(buttons('Trocar lançamento').length, 1);
    await press('Desvincular pagamento');
    await settle();
    await dismiss('Desvincular');
    await settle();
    assert.equal(unlinkWrites.length, 1);
    assert.match(unlinkWrites[0].path, /payment-unlink$/);
    assert.equal(unlinkWrites[0].body.transaction_id, 'tx-1');
    assert.equal(unlinkWrites[0].body.expected_revision, 3);
    await dismiss('OK');
  });

  await check('link-undo', async () => {
    const writes = [];
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
    const overdue = {commitment_id: 'overdue', name: 'Conta vencida', revision: 1, kind: 'assinatura', purchase_date: '2020-01-01', next_due_date: today, amount: 80, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: today}], linked_transactions: []};
    const candidate = {id: 'pay-1', source_kind: 'transaction', source_token: 'token-1', merchant_raw: 'Conta sintetica', valor: -80, data_evento: '2020-01-10T12:00:00Z'};
    const api = async (requestPath, init = {}) => {
      if (init.method === 'POST') {
        const body = JSON.parse(init.body);
        writes.push({path: String(requestPath), body});
        if (String(requestPath).endsWith('/payment-unlink')) return {agenda_restored: true, warning: null, revision: 1};
        return {persisted: true, revision: 2};
      }
      if (String(requestPath).endsWith('/payment-candidates')) return {items: [candidate], commitment_revision: 1};
      if (String(requestPath).includes('/statement-search')) return {items: [candidate], commitment_revision: 1, total: 1, truncated: false};
      if (requestPath === '/v1/commitments') return {items: [overdue]};
      return {items: []};
    };
    await act(async () => {renderer = create(screen(api));});
    await settle();
    await press('Abrir Conta vencida');
    await press('Vincular movimentação de Conta vencida');
    await settle();
    await press('Conta sintetica');
    await press('Vincular este lançamento');
    await settle();
    await dismiss('OK');
    await settle();
    assert.equal(buttons('Desfazer vínculo').length, 1);
    await press('Desfazer vínculo');
    await settle();
    const undone = writes.find(entry => entry.path.endsWith('/payment-unlink'));
    assert.ok(undone, 'unlink posted');
    assert.equal(undone.body.transaction_id, 'pay-1');
    assert.equal(undone.body.expected_revision, 2);
    await dismiss('OK');
  });

  await check('chart-accounts', async () => {
    const row = {month: '2026-10', total: '50', installments: '20', subscriptions: '30', other: '0', estimated: '30', bill_balance: '0', items: [
      {source_ref: 'parcel', name: 'Parcela', category: 'installments', amount: '20', estimated: false},
      {source_ref: 'energy', name: 'Energia', category: 'subscriptions', amount: '30', estimated: true},
    ]};
    await act(async () => {renderer = create(React.createElement(Projection, {api: async () => ({items: [row], months: 1, start_month: '2026-10', end_month: '2026-10', last_installment_month: null, warnings: [], total: '50', as_of: '2026-10-11', automatic_horizon_capped: false, summary: {this_month: '50', next_12: null, installments: '20', subscriptions: '30', other: '0', bill_balance: '0', window_months: 1}}), money: String}));});
    await settle();
    assert.doesNotMatch(text(), /Contas 30/);
    assert.doesNotMatch(text(), /Parcelas 20/);
    assert.match(text(), /Contas/);
    assert.match(text(), /Parcelas/);
    assert.doesNotMatch(text(), /Assinaturas/);
    assert.doesNotMatch(text(), /Dívidas 0/);
    assert.match(text(), /\n50\n|\n50$/m);
    await press('out/2026: 50 pendente no mês');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Contas 30'}).length, 1);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Parcelas 20'}).length, 1);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Assinaturas 30'}).length, 0);
  });

  await check('form-mask', async () => {
    const api = async requestPath => {
      if (requestPath === '/v1/commitments' || requestPath === '/v1/installments' || requestPath === '/v1/obligations') return {items: []};
      if (String(requestPath).startsWith('/v1/commitment-projection')) return {items: [{month: '2026-10', total: '0', installments: '0', subscriptions: '0', accounts: '0', other: '0', estimated: '0', bill_balance: '0', items: []}], months: 1, start_month: '2026-10', end_month: '2026-10', last_installment_month: null, warnings: [], total: '0', as_of: '2026-10-11', automatic_horizon_capped: false, summary: {this_month: '0', next_12: null, installments: '0', subscriptions: '0', other: '0', bill_balance: '0', window_months: 1}};
      return {items: []};
    };
    await act(async () => {renderer = create(screen(api));});
    await settle();
    await press('+ Adicionar');
    await settle();
    const date = renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === 'Data da compra / contratação');
    assert.equal(date.props.placeholder, 'dd/mm/aaaa');
    await act(async () => {date.props.onChangeText('31102026');});
    const updated = renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === 'Data da compra / contratação');
    assert.equal(updated.props.value, '31/10/2026');
    assert.doesNotMatch(text(), /Observações/);
    await press('Avançado');
    assert.match(text(), /Observações/);
    const notes = renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === 'Observações (opcional)');
    assert.equal(notes.props.multiline, true);
  });

  await check('totals', async () => {
    const rows = Array.from({length: 12}, (_, index) => ({month: index < 3 ? `2026-${String(index + 10).padStart(2, '0')}` : `2027-${String(index - 2).padStart(2, '0')}`, total: '1', installments: '20', subscriptions: '30', other: '5000', estimated: '0', bill_balance: '500', items: []}));
    const summary = {this_month: '7', next_12: '999', installments: '20', subscriptions: '30', other: '5000', bill_balance: '500', window_months: 12};
    await act(async () => {renderer = create(React.createElement(Projection, {api: async () => ({items: rows, months: 12, start_month: '2026-10', end_month: '2027-09', last_installment_month: '2026-11', warnings: [], total: '12', as_of: '2026-10-11', automatic_horizon_capped: false, summary}), money: String}));});
    await settle();
    assert.match(text(), /Falta pagar este mês/);
    assert.match(text(), /Próximos 12 meses/);
    assert.match(text(), /\n7\n|\n7$/m);
    assert.match(text(), /999/);
    assert.doesNotMatch(text(), /Parcelas 20/);
    assert.doesNotMatch(text(), /Dívidas 5000/);
    assert.doesNotMatch(text(), /Contas 0/);
    const compact = button('out/2026: 1 pendente no mês');
    const compactHeight = [compact.props.style].flat(Infinity).filter(Boolean).map(style => style.height).find(height => typeof height === 'number');
    assert.ok(compactHeight >= 100 && compactHeight <= 110, 'month row is about 100 to 110 px');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 0);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Fechar composição do mês'}).length, 0);
    await press('out/2026: 1 pendente no mês');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Parcelas 20'}).length, 1);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Assinaturas 30'}).length, 1);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Dívidas 5000'}).length, 1);
    assert.match(text(), /Faturas conhecidas em aberto: 500/);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Fechar composição do mês'}).length, 0);
    const openedMonth = renderer.root.findByProps({accessibilityLabel: 'Tela do mês'});
    assert.notEqual(openedMonth.props.style.position, 'absolute');
    await press('Voltar à lista de meses');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 0);
    const widths = renderer.root.findAllByType('View').map(node => node.props.style && node.props.style.width).filter(width => typeof width === 'string' && width.endsWith('%')).map(width => Number.parseFloat(width));
    assert.ok(widths.length >= 3);
    assert.ok(widths.every(width => width >= 8));
    await act(async () => renderer.unmount()); renderer = null;
    const short = rows.slice(0, 3).map(row => ({...row, total: '4', bill_balance: '0'}));
    let requested = '';
    await act(async () => {renderer = create(React.createElement(Projection, {api: async requestPath => {requested = requestPath; const count = Number(new URLSearchParams(String(requestPath).split('?')[1] || '').get('months') || 3); return {items: short.slice(0, count).concat(count > 3 ? rows.slice(3, count) : []), months: count, start_month: '2026-10', end_month: '2026-12', last_installment_month: null, warnings: [], total: '12', as_of: '2026-10-11', automatic_horizon_capped: false, summary: count >= 12 ? {this_month: '4', next_12: '1440', installments: '0', subscriptions: '0', other: '0', bill_balance: '0', window_months: 12} : {this_month: '4', next_12: null, installments: '0', subscriptions: '0', other: '0', bill_balance: '0', window_months: count}};}, money: String}));});
    await settle();
    assert.match(text(), /Toque em 12 meses/);
    assert.doesNotMatch(text(), /1440/);
    assert.equal(button('Tudo').props.accessibilityState.selected, true);
    assert.equal(buttons('Aplicar período').length, 0);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 0);
    const period = renderer.root.findAllByProps({accessibilityLabel: 'Período do gráfico'})[0];
    assert.equal(period.props.horizontal, true);
    assert.ok(period.findAllByType('Pressable').some(node => node.findAllByType('Text').some(child => child.props.children === 'Tudo')));
    await press('out/2026: 4 pendente no mês');
    const composition = renderer.root.findByProps({accessibilityLabel: 'Tela do mês'});
    assert.notEqual(composition.props.style.position, 'absolute');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Fechar composição do mês'}).length, 0);
    await press('Voltar à lista de meses');
    await press('12 meses');
    await settle();
    assert.match(requested, /months=12/);
    assert.match(text(), /1440/);
  });

  await check('layout', async () => {
    layoutWidth = 320;
    const series = Array.from({length: 200}, (_, index) => ({id: `finance-${index}`, tipo: 'financiamento', external_key: `obligation|financiamento-imobiliario|parcela-${index + 1}`, due_date: '2026-11-01', amount: '10'}));
    const api = obligationsApi(series, []);
    await act(async () => {renderer = create(screen(api));});
    await settle();
    assert.match(text(), /Falta pagar este mês|Lista de compromissos/);
    await press('Abrir Financiamento');
    const primary = button('Ver cronograma de Financiamento');
    const styles = [primary.props.style].flat(Infinity).filter(Boolean);
    assert.ok(styles.some(style => style.minHeight >= 48));
    await press('Ver cronograma de Financiamento');
    await settle();
    assert.equal(lastFlatList.data.filter(item => item.kind === 'row').length, 200);
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Cronograma de Financiamento'})[0].props.style.position, 'absolute');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Detalhe do compromisso'}).length, 0);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de compromissos'}).length, 1);
    layoutWidth = 1100;
    await act(async () => renderer.update(screen(api)));
    await settle();
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de compromissos'}).length, 1);
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
  });

  await check('close', async () => {
    const many = Array.from({length: 13}, (_, index) => ({id: `big-${index}`, tipo: 'financiamento', external_key: 'obligation|financiamento-grande|parcela-' + (index + 1), due_date: `2027-01-${String((index % 28) + 1).padStart(2, '0')}`, amount: '10.00'}));
    const removed = new Set();
    const writes = [];
    const api = async (requestPath, init = {}) => {
      const clean = String(requestPath).split('?')[0];
      const parts = clean.split('/');
      writes.push({path: clean, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null});
      if (clean === '/v1/future-commitments/obligations') return {items: many.filter(item => !removed.has(item.id))};
      if (clean === '/v1/commitments' || clean === '/v1/installments' || clean.startsWith('/v1/commitment-projection')) return {items: []};
      const identity = decodeURIComponent(parts[4] || '');
      if (clean.endsWith('/undo')) {removed.delete(identity); return {restored: true, source: 'obligation', identity};}
      if (init.method === 'DELETE') {removed.add(identity); return {persisted: true, source: 'obligation', identity, delete_request_id: identity + '-delete'};}
      return {preview_token: 'c'.repeat(64), editable_count: 1, rows: [{row_id: 1, amount: '10.00', due_date: '2027-01-01', editable: true}], source: 'obligation', identity};
    };
    await act(async () => {renderer = create(screen(api));});
    await settle();
    await press('Abrir Financiamento');
    await press('Mais ações de Financiamento');
    assert.doesNotMatch(text(), /Remover 13 parcelas futuras/);
    await press('Remover parcelas futuras de Financiamento');
    assert.match(dialogs.dialogSnapshot().message, /Remover 13 parcelas futuras/);
    assert.equal(writes.filter(item => item.method === 'DELETE').length, 0);
    await dismiss('Continuar com a remoção de Financiamento');
    assert.equal(writes.filter(item => item.method === 'DELETE').length, 0);
    await dismiss('Confirmar remoção de Financiamento');
    await settle();
    assert.equal(writes.filter(item => item.method === 'DELETE').length, 13);
    await dismiss('OK');
    await press('Desfazer exclusão de Financiamento');
    await settle();
    assert.equal(writes.filter(item => item.path.endsWith('/undo')).length, 13);
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const managed = {commitment_id: 'stream', name: 'Streaming', revision: 4, kind: 'assinatura', purchase_date: '2026-01-01', next_due_date: '2026-12-01', amount: 20, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-12-01'}], linked_transactions: [{transaction_id: 'kept', due_date: '2026-11-01', actual_amount: '20'}]};
    const edits = [];
    await act(async () => {renderer = create(screen(async (requestPath, init = {}) => {
      if (init.method === 'PUT') {edits.push(JSON.parse(init.body)); return {commitment_id: 'stream', status: 'cancelado'};}
      if (requestPath === '/v1/commitments') return {items: [managed]};
      return {items: []};
    }));});
    await settle();
    await press('Abrir Streaming');
    await press('Mais ações de Streaming');
    await press('Encerrar Streaming');
    assert.match(dialogs.dialogSnapshot().message, /não cancela o serviço no fornecedor/);
    assert.equal(edits.length, 0);
    await dismiss('Confirmar encerramento de Streaming');
    await settle();
    assert.equal(edits.length, 1);
    assert.equal(edits[0].status, 'cancelado');
    assert.equal(edits[0].revision, 4);
    assert.equal(edits[0].linked_transactions, undefined);
    await dismiss('OK');
    await act(async () => renderer.unmount()); renderer = null;
    const loan = {commitment_id: 'loan', name: 'Casa', revision: 2, kind: 'parcelamento', purchase_date: '2026-01-01', next_due_date: '2026-12-01', amount: 100, frequency: 'mensal', installment_count: 12, next_installment_number: 3, payment_method: 'boleto', renewal_status: 'nao_aplicavel', status: 'ativo', schedule: [], linked_transactions: []};
    const loanEdits = [];
    await act(async () => {renderer = create(screen(async (requestPath, init = {}) => {
      if (init.method === 'PUT') {loanEdits.push(JSON.parse(init.body)); return {commitment_id: 'loan', status: 'cancelado'};}
      if (requestPath === '/v1/commitments') return {items: [loan]};
      if (String(requestPath).startsWith('/v1/future-commitments/managed/')) return {preview_token: 'd'.repeat(64), editable_count: 2, source: 'managed', identity: 'loan', rows: [{row_id: 1, amount: '100.00', due_date: '2026-12-01', editable: true}, {row_id: 2, amount: '100.00', due_date: '2027-01-01', editable: true}]};
      return {items: []};
    }));});
    await settle();
    await press('Abrir Casa');
    await press('Mais ações de Casa');
    assert.equal(buttons('Terminar Casa em').length, 0);
    await press('Encerrar Casa');
    assert.match(dialogs.dialogSnapshot().message, /não quita nem altera o contrato/);
    assert.doesNotMatch(dialogs.dialogSnapshot().message, /não cancela o serviço no fornecedor/);
    await dismiss('Confirmar encerramento de Casa');
    await settle();
    assert.equal(loanEdits[0].status, 'cancelado');
    assert.equal(loanEdits[0].kind, 'parcelamento');
    await dismiss('OK');
    await press('Mais ações de Casa');
    await press('Remover parcelas futuras de Casa');
    await settle();
    assert.match(dialogs.dialogSnapshot().message, /não quita nem altera o contrato/);
    assert.doesNotMatch(dialogs.dialogSnapshot().message, /não cancela o serviço no fornecedor/);
  });

  await check('detail-open', async () => {
    const item = {commitment_id: 'energy', name: 'Energia', revision: 1, kind: 'assinatura', purchase_date: '2026-09-10', next_due_date: '2026-10-10', amount: 178.8, amount_mode: 'last_three_bills', amount_estimate: {requires_review: false}, frequency: 'mensal', payment_method: 'debito', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-10-10'}], linked_transactions: []};
    const api = async requestPath => requestPath === '/v1/commitments' ? {items: [item]} : {items: []};
    await act(async () => {renderer = create(screen(api));});
    await settle();
    assert.match(text(), /10\/10\/2026/);
    assert.doesNotMatch(text(), /Pago \(assumido\)/);
    await press('Abrir Energia');
    assert.match(text(), /Próxima cobrança: 10\/11\/2026/);
    assert.match(text(), /1 parcela vencida sem vínculo \(10\/10\/2026\)/);
    assert.match(text(), /A pagar/);
    assert.match(text(), /Vencida · sem vínculo/);
    assert.doesNotMatch(text(), /Próxima cobrança: 10\/10\/2026/);
    assert.match(text(), /Média das 3 últimas contas/);
    assert.equal(buttons('Ver cronograma de Energia').length, 1);
    assert.equal(buttons('Encerrar Energia').length, 0);
    await press('Mais ações de Energia');
    const menu = renderer.root.findAllByProps({accessibilityLabel: 'Menu do compromisso'})[0];
    assert.equal(menu.props.style.position, 'absolute');
    assert.equal(menu.findAllByType('Pressable').map(node => node.props.accessibilityLabel)[0], 'Terminar Energia em');
    assert.equal(buttons('Encerrar Energia').length, 1);
    assert.equal(buttons('Remover parcelas futuras de Energia').length, 1);
    await press('Pagas (1)');
    assert.match(text(), /10\/09\/2026/);
    assert.match(text(), /Pago \(assumido\)/);
    assert.doesNotMatch(text(), /10\/10\/2026 · .*Pago \(assumido\)/);
    await press('Mais ações de Energia');
    await press('Voltar de Detalhe de Energia');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Detalhe de Energia'}).length, 0);
    assert.equal(buttons('Abrir Energia').length, 1);
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
  });

  await check('order', async () => {
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
    const overdue = {commitment_id: 'overdue', name: 'Conta vencida', revision: 1, kind: 'assinatura', purchase_date: '2020-01-01', next_due_date: today, amount: 80, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: today}], linked_transactions: []};
    const projection = {items: [{month: '2026-10', total: '80', installments: '0', subscriptions: '80', other: '0', estimated: '0', bill_balance: '0', items: []}], months: 12, start_month: '2026-10', end_month: '2027-09', last_installment_month: null, warnings: [], total: '80', as_of: '2026-10-11', automatic_horizon_capped: false, summary: {this_month: '80', next_12: '960', installments: '0', subscriptions: '80', other: '0', bill_balance: '0', window_months: 12}};
    const candidate = {id: 'pay-1', source_kind: 'transaction', source_token: 'token-1', merchant_raw: 'Conta sintetica', valor: -80, data_evento: '2020-01-10T12:00:00Z'};
    await act(async () => {renderer = create(screen(async requestPath => {
      if (String(requestPath).endsWith('/payment-candidates')) return {items: [candidate], commitment_revision: 1};
      if (requestPath === '/v1/commitments') return {items: [overdue]};
      if (String(requestPath).startsWith('/v1/commitment-projection')) return projection;
      return {items: []};
    }));});
    await settle();
    const body = text();
    const at = label => body.indexOf(label);
    assert.ok(at('Falta pagar este mês') >= 0 && at('Falta pagar este mês') < at('Compromissos por mês'));
    assert.ok(at('Compromissos por mês') < at('\nTudo\n'));
    assert.ok(at('\nTudo\n') < body.lastIndexOf('Conta vencida'));
    assert.match(body, /Conta vencida/);
    assert.match(body, /A conciliar \(1\)/);
    assert.match(body, /Conciliar/);
  });

  await check('terminar-em', async () => {
    const item = {commitment_id: 'claro', name: 'Claro', revision: 3, kind: 'assinatura', purchase_date: '2024-01-10', next_due_date: '2026-11-10', schedule_anchor_day: 10, amount: 178.8, frequency: 'mensal', payment_method: 'debito', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-11-10'}], linked_transactions: []};
    const edits = [];
    await act(async () => {renderer = create(screen(async (requestPath, init = {}) => {
      if (init.method === 'PUT') {edits.push(JSON.parse(init.body)); return {commitment_id: 'claro', revision: 4, status: 'ativo'};}
      if (init.method === 'DELETE') throw new Error('Terminar em não apaga o horizonte.');
      if (requestPath === '/v1/commitments') return {items: [item]};
      return {items: []};
    }));});
    await settle();
    await press('Abrir Claro');
    await press('Mais ações de Claro');
    await press('Terminar Claro em');
    const month = renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === 'Mês final de Claro');
    assert.equal(month.props.placeholder, 'mm/aaaa');
    await act(async () => {month.props.onChangeText('122027');});
    assert.equal(renderer.root.findAllByType('TextInput').find(node => node.props.accessibilityLabel === 'Mês final de Claro').props.value, '12/2027');
    await press('Definir término de Claro');
    assert.match(dialogs.dialogSnapshot().message, /Última cobrança em dez\/2027/);
    assert.match(dialogs.dialogSnapshot().message, /Vínculos e extrato permanecem/);
    assert.notEqual(dialogs.dialogSnapshot().buttons.find(button => button.text === 'Confirmar término').style, 'destructive');
    await dismiss('Confirmar término');
    await settle();
    assert.equal(edits.length, 1);
    assert.equal(edits[0].end_date, '2027-12-10');
    assert.equal(edits[0].status, 'ativo');
    assert.equal(edits[0].revision, 3);
    await dismiss('OK');
  });

  await check('obrigacao-pontual', async () => {
    const item = {commitment_id: 'ipva', name: 'IPVA', revision: 1, kind: 'obrigacao', obligation_plan: 'parcelado', installment_count: 3, next_installment_number: 1, purchase_date: '2026-10-11', next_due_date: '2026-11-10', cash_due_date: '2026-11-20', amount: 100, frequency: 'mensal', payment_method: 'pix', renewal_status: 'nao_aplicavel', status: 'ativo', schedule: [], linked_transactions: []};
    const writes = [];
    const api = async (requestPath, init = {}) => {
      if (init.method === 'POST') {writes.push(JSON.parse(init.body)); return {commitment_id: 'novo', revision: 1};}
      if (requestPath === '/v1/commitments') return {items: [item]};
      return {items: []};
    };
    await act(async () => {renderer = create(screen(api));});
    await settle();
    assert.match(text(), /Dívidas/);
    assert.match(text(), /33\.33/);
    assert.doesNotMatch(text(), /Pago \(assumido\)/);
    await press('Abrir IPVA');
    assert.match(text(), /Obrigação pontual/);
    assert.match(text(), /Hipotético · 3 parcelas/);
    assert.match(text(), /Parcelamento hipotético/);
    assert.match(text(), /33\.34/);
    assert.match(text(), /Próxima cobrança: 10\/11\/2026/);
    await press('Mais ações de IPVA');
    assert.equal(buttons('Terminar IPVA em').length, 1);
    await press('Voltar de Detalhe de IPVA');
    await press('+ Adicionar');
    await settle();
    await press('Obrigação pontual');
    assert.match(text(), /Data à vista/);
    assert.match(text(), /Valor total \(R\$\)/);
    assert.doesNotMatch(text(), /Frequência/);
    assert.doesNotMatch(text(), /Próximo compromisso/);
    const fill = async (label, value) => {
      const node = renderer.root.findAllByType('TextInput').find(entry => entry.props.accessibilityLabel === label);
      assert.ok(node, label);
      await act(async () => {node.props.onChangeText(value);});
    };
    await fill('Nome ou serviço', 'IPTU');
    await fill('Valor total (R$)', '10000');
    await fill('Data à vista', '20112026');
    await press('Pix');
    await press('Salvar e sincronizar');
    await settle();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].kind, 'obrigacao');
    assert.equal(writes[0].obligation_plan, 'a_vista');
    assert.equal(writes[0].amount, 100);
    assert.equal(writes[0].cash_due_date, '2026-11-20');
    assert.equal(writes[0].next_due_date, '2026-11-20');
    assert.equal(writes[0].frequency, 'mensal');
    assert.equal(writes[0].installment_count, null);
    assert.equal(writes[0].renewal_status, 'nao_aplicavel');
    await dismiss('OK');
  });

  await check('pending-v6', async () => {
    const asOf = '2026-10-12';
    presentation.setPresentationClock(asOf);
    try {
      const parcel = {commitment_id: 'd', name: 'Loja', revision: 1, kind: 'parcelamento', status: 'ativo', purchase_date: '2026-10-01', next_due_date: '2026-10-20', amount: 25, frequency: 'mensal', payment_method: 'cartao', installment_count: 4, next_installment_number: 1, renewal_status: 'nao_aplicavel', linked_transactions: []};
      const open = presentation.commitmentFigures('managed', parcel, asOf);
      const openCopy = presentation.restanteCopy(open, String);
      assert.equal(openCopy.line, 'Restante 100 · 4 parcelas · até 01/2027');
      assert.equal(presentation.formatCharge(open.chargeAmount, open.suffix, false, String), '25 /mês');
      const linked = {...parcel, linked_transactions: [{due_date: '2026-10-20', transaction_id: 't'}]};
      const after = presentation.commitmentFigures('managed', linked, asOf);
      assert.equal(after.sum, 75);
      assert.equal(after.count, 3);
      assert.equal(presentation.restanteCopy(after, String).detail, '75 · 3 parcelas');
      const endless = {commitment_id: 'f', name: 'Conta F', revision: 1, kind: 'assinatura', status: 'ativo', purchase_date: '2026-01-10', next_due_date: '2026-10-10', amount: 80, amount_mode: 'last_three_bills', amount_estimate: {requires_review: false}, frequency: 'mensal', payment_method: 'debito', renewal_status: 'automatica', linked_transactions: []};
      const noEnd = presentation.commitmentFigures('managed', endless, asOf);
      const noEndCopy = presentation.restanteCopy(noEnd, String);
      assert.equal(noEndCopy.line, 'Restante — · sem data final');
      assert.equal(noEnd.endless, true);
      assert.equal(presentation.formatCharge(noEnd.chargeAmount, noEnd.suffix, true, String), '~ 80 /mês');
      const ended = {...endless, commitment_id: 'f-end', name: 'Conta F fim', end_date: '2026-12-10'};
      const withEnd = presentation.commitmentFigures('managed', ended, asOf);
      assert.equal(withEnd.pending.map(row => row.due).join(','), '2026-10-10,2026-11-10,2026-12-10');
      assert.equal(presentation.restanteCopy(withEnd, String).line, 'Restante ~ 240 · 3 parcelas · até 12/2026');
      const afterEnd = presentation.commitmentFigures('managed', {...ended, commitment_id: 'after-end', next_due_date: '2026-11-10', end_date: '2026-10-10'}, asOf);
      assert.equal(afterEnd.pending.length, 0);
      assert.equal(afterEnd.sum, 0);
      const onEnd = presentation.commitmentFigures('managed', {...ended, commitment_id: 'on-end', next_due_date: '2026-10-10', end_date: '2026-10-10'}, asOf);
      assert.equal(onEnd.pending.map(row => row.due).join(','), '2026-10-10');
      assert.equal(onEnd.sum, 80);
      const late = presentation.commitmentFigures('managed', {commitment_id: 'e', name: 'E', kind: 'parcelamento', status: 'ativo', purchase_date: '2026-10-01', next_due_date: '2026-10-05', amount: 30, frequency: 'mensal', payment_method: 'pix', installment_count: 3, next_installment_number: 1, renewal_status: 'nao_aplicavel', linked_transactions: []}, '2026-10-20');
      assert.equal(late.sum, 90);
      assert.equal(late.count, 3);
      assert.equal(late.pending[0].kind, 'vencida');
      const old = presentation.pendingOccurrences('managed', {kind: 'obrigacao', obligation_plan: 'a_vista', status: 'ativo', next_due_date: '2026-09-25', amount: 70, frequency: 'mensal', renewal_status: 'nao_aplicavel', installment_count: null, linked_transactions: []}, asOf);
      assert.equal(old.length, 0);
      const api = async requestPath => {
        if (requestPath === '/v1/commitments') return {items: [parcel, endless, ended]};
        if (String(requestPath).startsWith('/v1/commitment-projection')) return {items: [{month: '2026-10', total: '150', installments: '100', subscriptions: '50', accounts: '0', other: '0', estimated: '0', bill_balance: '0', items: []}, {month: '2026-11', total: '220', installments: '0', subscriptions: '220', accounts: '0', other: '0', estimated: '0', bill_balance: '0', items: []}], months: 12, start_month: '2026-10', end_month: '2027-09', last_installment_month: null, warnings: [], total: '150', as_of: asOf, automatic_horizon_capped: false, summary: {this_month: '150', overdue: '100', upcoming: '50', next_12: '400', installments: '100', subscriptions: '300', other: '0', bill_balance: '0', window_months: 12}};
        return {items: []};
      };
      await act(async () => {renderer = create(screen(api));});
      await settle();
      const body = text();
      assert.match(body, /Falta pagar este mês/);
      assert.match(body, /Vencidas 100/);
      assert.match(body, /A vencer 50/);
      assert.match(body, /pendente no mês/);
      assert.match(body, /Restante 100[\s\S]*4 parcelas · até 01\/2027/);
      assert.match(body, /Restante —[\s\S]*sem data final/);
      assert.match(body, /Restante ~ 240[\s\S]*3 parcelas · até 12\/2026/);
      assert.doesNotMatch(body, /Estimado/);
      assert.doesNotMatch(body, /600/);
      await press('Abrir Loja');
      assert.match(text(), /100 · 4 parcelas/);
      assert.match(text(), /01\/2027/);
      await press('Ver cronograma de Loja');
      await settle();
      const scheduleSum = lastFlatList.data.filter(item => item.kind === 'row').reduce((sum, row) => sum + Number(row.amount), 0);
      assert.equal(scheduleSum, open.sum);
      assert.match(text(), /total restante 100/);
    } finally {
      presentation.setPresentationClock(null);
    }
  });

  await check('month-screen', async () => {
    const previousWidth = layoutWidth;
    layoutWidth = 320;
    presentation.setPresentationClock('2026-10-14');
    const luz = {commitment_id: 'luz', name: 'Luz', revision: 1, kind: 'assinatura', status: 'ativo', purchase_date: '2026-01-01', next_due_date: '2026-10-05', amount: 20, frequency: 'mensal', payment_method: 'pix', renewal_status: 'automatica', linked_transactions: []};
    const catalog = {
      '2026-10': {total: '60', installments: '10', subscriptions: '20', accounts: '0', other: '30', estimated: '0', bill_balance: '0', items: [
        {source_ref: 'obligation:taxa', name: 'Taxa', category: 'other', amount: '30', estimated: false, due_date: '2026-10-02', payment_method: 'boleto'},
        {source_ref: 'commitment:luz', name: 'Luz', category: 'subscriptions', amount: '20', estimated: false, due_date: '2026-10-05', payment_method: 'pix'},
        {source_ref: 'commitment:curso', name: 'Curso', category: 'installments', amount: '10', estimated: false, due_date: '2026-10-19', occurrence_number: 2, occurrence_total: 4, card_label: 'Visa 1111'},
      ]},
      '2026-11': {total: '12', installments: '0', subscriptions: '0', accounts: '12', other: '0', estimated: '12', bill_balance: '0', items: [
        {source_ref: 'commitment:agua', name: 'Agua', category: 'accounts', amount: '12', estimated: true, due_date: '2026-11-08', payment_method: 'debito'},
      ]},
      '2026-12': {total: '0', installments: '0', subscriptions: '0', accounts: '0', other: '0', estimated: '0', bill_balance: '0', items: []},
      '2027-01': {total: '18', installments: '0', subscriptions: '18', accounts: '0', other: '0', estimated: '0', bill_balance: '0', items: Array.from({length: 18}, (_, index) => ({source_ref: `commitment:n${index}`, name: `Item ${String(index + 1).padStart(2, '0')}`, category: 'subscriptions', amount: '1', estimated: false, due_date: `2027-01-${String(index + 1).padStart(2, '0')}`, payment_method: 'pix'}))},
    };
    const rowFor = month => ({month, ...(catalog[month] || {total: '0', installments: '0', subscriptions: '0', accounts: '0', other: '0', estimated: '0', bill_balance: '0', items: []})});
    const projection = count => {
      const items = Array.from({length: count}, (_, index) => {const cursor = 2026 * 12 + 9 + index; return rowFor(`${Math.floor(cursor / 12)}-${String((cursor % 12) + 1).padStart(2, '0')}`);});
      return {items, months: count, start_month: items[0].month, end_month: items.at(-1).month, last_installment_month: null, warnings: [], total: '0', as_of: '2026-10-14', automatic_horizon_capped: false, summary: {this_month: '60', overdue: '30', upcoming: '30', next_12: count >= 12 ? '90' : null, installments: '10', subscriptions: '38', other: '30', bill_balance: '0', window_months: Math.min(12, count)}};
    };
    const api = async requestPath => {
      const clean = String(requestPath).split('?')[0];
      if (clean === '/v1/commitments') return {items: [luz]};
      if (String(requestPath).startsWith('/v1/commitment-projection')) {
        const requested = new URLSearchParams(String(requestPath).split('?')[1] || '').get('months');
        return projection(requested ? Number(requested) : 8);
      }
      return {items: []};
    };
    const flat = node => [node.props.style].flat(Infinity).filter(Boolean);
    const minHeightOf = node => flat(node).reduce((max, style) => Math.max(max, Number(style.minHeight) || 0), 0);
    const monthScreen = () => renderer.root.findByProps({accessibilityLabel: 'Tela do mês'});
    const localBack = load('utils/localBack.ts');
    try {
      await act(async () => {renderer = create(screen(api));});
      await settle();
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 0);
      assert.equal(buttons('Fechar').length, 0);
      assert.equal(buttons('Conciliar Luz').length, 1, 'T8 starts with A conciliar on the page');
      const palette = load('utils/monthDetail.ts');
      const series = palette.chartSeries;
      const track = palette.CHART_TRACK;
      const channel = (hex, index) => {const value = parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16) / 255; return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;};
      const contrast = (left, right) => {const lum = hex => 0.2126 * channel(hex, 0) + 0.7152 * channel(hex, 1) + 0.0722 * channel(hex, 2); const a = lum(left), b = lum(right); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);};
      assert.equal(new Set(series.map(item => item.color)).size, 4);
      const debts = series.find(item => item.label === 'Dívidas').color.toUpperCase();
      assert.notEqual(debts, '#D7AC69');
      assert.notEqual(debts, '#E7B15A');
      for (const item of series) assert.ok(contrast(item.color, track) >= 3, item.label + ' ' + contrast(item.color, track));
      const background = node => [node.props.style].flat(Infinity).filter(Boolean).map(style => style.backgroundColor).find(color => typeof color === 'string' && color.startsWith('#'));
      const painted = () => renderer.root.findAllByType('View').map(background).filter(Boolean);
      for (const item of series) assert.ok(painted().filter(color => color === item.color).length >= 2, item.label);
      assert.ok(painted().includes(track));
      const list = () => renderer.root.findByProps({accessibilityLabel: 'Lista de meses'});
      const chartScroll = load('utils/chartScroll.ts');
      const page = {y: -1, scrollTo(options) { this.y = options.y; }};
      chartScroll.bindChartScroller(page);
      assert.equal(buttons('+ Adicionar').length, 1);
      assert.ok(minHeightOf(button('out/2026: 60 pendente no mês')) >= 48);
      await act(async () => list().props.onScroll({nativeEvent: {contentOffset: {y: 240, x: 0}}}));
      await press('out/2026: 60 pendente no mês');
      assert.equal(page.y, 0, 'opening a month parks the page at the top');
      assert.equal(chartScroll.chartOffset(), 240, 'the scrolled offset stays saved');
      assert.equal(buttons('+ Adicionar').length, 0);
      assert.equal(renderer.root.findAllByType('Text').filter(node => node.props.children === 'Compromissos').length, 0);
      const opened = monthScreen();
      const screenColors = opened.findAllByType('View').map(background).filter(Boolean);
      assert.ok(screenColors.includes(series.find(item => item.label === 'Parcelas').color));
      assert.ok(screenColors.includes(series.find(item => item.label === 'Assinaturas').color));
      assert.ok(screenColors.includes(series.find(item => item.label === 'Dívidas').color));
      assert.ok(screenColors.includes(track));
      assert.equal(screenColors.includes('#D7AC69'), false);
      assert.equal(screenColors.includes('#7DCEA0'), false);
      assert.equal(opened.props.accessibilityValue.text, '/compromissos/mes/2026-10?periodo=all');
      assert.match(text(), /out\/2026/);
      assert.equal(opened.findAllByType('ScrollView').length, 0, 'T7 embedded month has no nested scroller');
      assert.equal(opened.findAll(node => flat(node).some(style => style.position === 'absolute' || style.position === 'fixed')).length, 0);
      assert.equal(renderer.root.findAllByType('Modal').length, 0);
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Fechar composição do mês'}).length, 0);
      assert.equal(buttons('Fechar').length, 0);
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de meses'}).length, 0, 'T8 month list is off the screen');
      assert.equal(buttons('Conciliar Luz').length, 0, 'T8 A conciliar is off the screen');
      assert.ok(minHeightOf(button('Voltar à lista de meses')) >= 48);
      assert.ok(minHeightOf(button('Mês anterior')) >= 48);
      assert.ok(minHeightOf(button('Mês seguinte')) >= 48);
      assert.equal(button('Mês anterior').props.disabled, true);
      assert.equal(button('Mês seguinte').props.disabled, false);
      const categoryLabels = opened.findAll(node => typeof node.props.accessibilityLabel === 'string' && /^(Parcelas|Assinaturas|Contas|Dívidas) /.test(node.props.accessibilityLabel)).map(node => node.props.accessibilityLabel);
      assert.deepEqual(categoryLabels.sort(), ['Assinaturas 20', 'Dívidas 30', 'Parcelas 10']);
      assert.equal(categoryLabels.reduce((sum, label) => sum + Number(label.split(' ').at(-1)), 0), 60);
      assert.match(text(), /\n60\n/);
      assert.match(text(), /Pendente no mês · 3 compromissos/);
      assert.doesNotMatch(text(), /Categoria:/);
      assert.doesNotMatch(text(), /Inclui .*contas variáveis/);
      const names = opened.findAllByType('Pressable').filter(node => String(node.props.accessibilityLabel || '').startsWith('Abrir cobrança ')).map(node => node.props.accessibilityLabel);
      assert.deepEqual(names, ['Abrir cobrança Taxa', 'Abrir cobrança Luz', 'Abrir cobrança Curso']);
      assert.match(text(), /02\/10 · Dívida · Boleto/);
      assert.match(text(), /05\/10 · Assinatura · Pix/);
      assert.match(text(), /19\/10 · Parcela 2\/4 · Visa 1111/);
      assert.equal(opened.findAll(node => node.props.children === 'Vencido · sem vínculo').length, 2);
      await press('Abrir cobrança Luz');
      await settle();
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Detalhe de Luz'}).length, 1);
      await act(async () => {assert.equal(localBack.dispatchLocalBack(), true);});
      await settle();
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Detalhe de Luz'}).length, 0);
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 1);
      await press('Voltar à lista de meses');
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 0);
      assert.equal(page.y, 240, 'back restores the page offset');
      assert.equal(buttons('+ Adicionar').length, 1);
      assert.equal(list().props.contentOffset.y, 240);
      const october = button('out/2026: 60 pendente no mês');
      assert.equal(october.props.accessibilityState.selected, true);
      assert.ok(flat(october).some(style => style.borderColor === '#49C5AA'));
      const rowHeight = flat(october).map(style => style.height).find(height => typeof height === 'number');
      assert.ok(rowHeight >= 100 && rowHeight <= 110);
      assert.doesNotMatch(list().findAllByType('Text').map(node => [].concat(node.props.children).join('')).join('\n'), /Parcelas \d|Assinaturas \d|Contas \d|Dívidas \d/);
      await press('out/2026: 60 pendente no mês');
      await press('Mês seguinte');
      assert.match(text(), /nov\/2026/);
      assert.match(text(), /Previsto no mês · 1 compromisso/);
      assert.match(text(), /~ 12/);
      assert.match(text(), /Inclui 12 de contas variáveis estimadas pela média atual/);
      assert.equal(monthScreen().findAll(node => node.props.children === 'Vencido · sem vínculo').length, 0);
      assert.equal(button('Mês anterior').props.disabled, false);
      await press('Mês seguinte');
      assert.match(text(), /Nenhum compromisso neste mês/);
      assert.match(text(), /dez\/2026/);
      await press('Voltar à lista de meses');
      await act(async () => list().props.onScroll({nativeEvent: {contentOffset: {y: 180, x: 0}}}));
      await press('out/2026: 60 pendente no mês');
      await act(async () => {assert.equal(localBack.dispatchLocalBack(), true);});
      await settle();
      assert.equal(list().props.contentOffset.y, 180);
      assert.equal(button('out/2026: 60 pendente no mês').props.accessibilityState.selected, true);
      await press('6 meses');
      await settle();
      await press('out/2026: 60 pendente no mês');
      assert.equal(monthScreen().props.accessibilityValue.text, '/compromissos/mes/2026-10?periodo=6');
      assert.equal(button('Mês anterior').props.disabled, true);
      for (let step = 0; step < 5; step += 1) await press('Mês seguinte');
      assert.match(text(), /mar\/2027/);
      assert.equal(button('Mês seguinte').props.disabled, true);
      assert.equal(button('Mês anterior').props.disabled, false);
      await press('Voltar à lista de meses');
      await press('Tudo');
      await settle();
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de meses'})[0].findAllByType('Pressable').length, 8);
      await press('mai/2027: 0 previstos');
      assert.equal(button('Mês seguinte').props.disabled, true);
      assert.equal(monthScreen().props.accessibilityValue.text, '/compromissos/mes/2027-05?periodo=all');
      await press('Voltar à lista de meses');
      await act(async () => list().props.onScroll({nativeEvent: {contentOffset: {y: 640, x: 0}}}));
      await press('jan/2027: 18 previstos');
      const longMonth = monthScreen();
      assert.equal(page.y, 0, 'a long month also starts at the top of the page');
      assert.equal(chartScroll.chartOffset(), 640);
      assert.equal(buttons('+ Adicionar').length, 0);
      assert.equal(longMonth.findAllByType('Pressable').filter(node => String(node.props.accessibilityLabel || '').startsWith('Abrir cobrança ')).length, 18);
      assert.equal(longMonth.findAllByType('ScrollView').length, 0);
      assert.equal(longMonth.findAll(node => flat(node).some(style => style.position === 'absolute' || style.position === 'fixed')).length, 0);
      assert.doesNotMatch(text(), /A conciliar/);
      await press('Voltar à lista de meses');
      layoutWidth = 1100;
      await act(async () => renderer.update(screen(api)));
      await settle();
      page.scrollTo({y: 480, animated: false});
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de meses'}).length, 1);
      await press('out/2026: 60 pendente no mês');
      assert.equal(page.y, 480, 'a wide month pane leaves the page offset alone');
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de meses'}).length, 1);
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Tela do mês'}).length, 1);
      await act(async () => renderer.unmount()); renderer = null;
      layoutWidth = 320;
      routeParams = {month: '2026-11', periodo: '6'};
      mocks['../../../utils/sessionClient'] = {api: {json: async requestPath => projection(Number(new URLSearchParams(String(requestPath).split('?')[1] || '').get('months') || 6))}};
      const Route = load('app/compromissos/mes/[month].tsx').default;
      await act(async () => {renderer = create(React.createElement(Route));});
      await settle();
      const routed = monthScreen();
      assert.equal(routed.props.accessibilityValue.text, '/compromissos/mes/2026-11?periodo=6');
      assert.match(text(), /nov\/2026/);
      assert.equal(routed.findAllByType('ScrollView').length, 1);
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de meses'}).length, 0);
      layoutWidth = 1100;
      await act(async () => renderer.update(React.createElement(Route)));
      await settle();
      assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Lista de meses'}).length, 1);
      const wideMonth = monthScreen();
      assert.equal(wideMonth.props.style.position, undefined);
      assert.equal(flat(wideMonth).some(style => style.position === 'absolute' || style.position === 'fixed'), false);
      assert.equal(button('Mês seguinte').props.disabled, false);
    } finally {
      presentation.setPresentationClock(null);
      layoutWidth = previousWidth;
    }
  });

  await check('profile', async () => {
    const filters = load('utils/movementFilters.ts');
    const bank = 'Conta · Banco';
    const card = 'Cartao Sintetico 1111';
    const statement = {metodo_pagamento: 'cartao_credito', descricao_original: 'LOJA1000', vendedor: 'Loja Sintetica', categoria: 'Saude', subcategoria: 'farmacia', installment_plan: {name: 'Loja Sintetica', card_label: card}};
    assert.equal(Object.hasOwn(statement, 'cartao_label'), false);
    assert.equal(filters.statementInstrument(statement, bank), card);
    assert.equal(filters.statementSettlementLabel(statement, bank).includes(bank), false);
    const assinatura = {commitment_id: 'sub', name: 'ASSINATURA1000', display_name: 'Marca Assinatura', categoria: 'Saude', subcategoria: 'farmacia', revision: 1, kind: 'assinatura', purchase_date: '2026-10-01', next_due_date: '2026-11-02', amount: 10, frequency: 'mensal', payment_method: 'cartao_credito', card_label: card, renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-11-02'}]};
    const conta = {commitment_id: 'bill', name: 'Conta Sintetica', revision: 1, kind: 'assinatura', amount_mode: 'last_three_bills', amount_estimate: {requires_review: false}, purchase_date: '2026-10-01', next_due_date: '2026-11-05', amount: 30, frequency: 'mensal', payment_method: 'debito', renewal_status: 'automatica', status: 'ativo', schedule: [{due_date: '2026-11-05'}]};
    const parcela = {id: 'plan-loja', descricao: 'LOJA1000', display_name: 'Loja Sintetica', categoria: 'Saude', subcategoria: 'farmacia', natureza: 'COMPRA', payment_method: 'cartao_credito', frequency: 'mensal', amount: 29.9, card_id: 'card-sintetico', account_id: 'acc-sintetica', numero_parcelas: 2, data_compra: '2026-10-01', card_label: card, installments: [{status: 'projetada', valor: 10, data_vencimento: '2026-11-02'}, {status: 'projetada', valor: 10, data_vencimento: '2026-12-02'}]};
    const financing = [
      {id: 'next-only', tipo: 'financiamento', external_key: 'sintetico|loja|parcela-1', due_date: '2026-11-08', amount: '20.00', descricao: 'DIVIDA1000', raw_name: 'DIVIDA1000', display_name: 'Divida Sintetica', categoria: 'Moradia', subcategoria: 'financiamento'},
      {id: 'later-only', tipo: 'financiamento', external_key: 'sintetico|loja|parcela-2', due_date: '2026-12-08', amount: '20.00', descricao: 'DIVIDA1000', raw_name: 'DIVIDA1000'},
    ];
    const writes = [];
    const api = async (requestPath, init = {}) => {
      const clean = String(requestPath).split('?')[0];
      if (init.method === 'PUT') writes.push({path: clean, body: JSON.parse(init.body)});
      if (clean === '/v1/commitments') return {items: [assinatura, conta]};
      if (clean === '/v1/installments') return {items: [parcela]};
      if (clean === '/v1/future-commitments/obligations') return {items: financing};
      if (clean.startsWith('/v1/commitment-projection')) return {items: [], months: 1, start_month: '2026-10', end_month: '2026-10', last_installment_month: null, warnings: [], total: '0', as_of: '2026-10-14', automatic_horizon_capped: false};
      if (clean === '/v1/commitment-profile') return {persisted: true};
      return {items: []};
    };
    const selectedChoice = label => {
      const node = button('Selecionar ' + label);
      return node.findAllByType('Text').map(child => [].concat(child.props.children).filter(item => typeof item === 'string').join('')).find(value => value && value !== '⌄') || '';
    };
    presentation.setPresentationClock('2026-10-14');
    await act(async () => {renderer = create(screen(api, {accounts: [{id: 'acc-sintetica', instituicao: 'Banco Sintetico', tipo: 'conta_corrente', status: 'ativa'}]}));});
    await settle();
    assert.match(text(), /Marca Assinatura/);
    assert.match(text(), /ASSINATURA1000/);
    assert.match(text(), /Loja Sintetica/);
    assert.match(text(), /LOJA1000/);
    assert.match(text(), /Divida Sintetica/);
    assert.match(text(), /DIVIDA1000/);
    for (const name of ['Marca Assinatura', 'Conta Sintetica', 'Loja Sintetica', 'Divida Sintetica']) {
      await press('Abrir ' + name);
      assert.equal(buttons('Editar compromisso ' + name).length, 1, name);
      await press('Voltar de Detalhe de ' + name);
    }
    await press('Abrir Loja Sintetica');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Classificação do compromisso'}).length, 0);
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Pagamento do compromisso'}).length, 0);
    assert.doesNotMatch(text(), /Saude › farmacia/);
    assert.match(text(), /Próxima cobrança: 02\/11\/2026/);
    const paymentLabels = renderer.root.findAllByType('Text').filter(node => node.props.children === 'Pagamento');
    assert.equal(paymentLabels.length, 1);
    const paymentFact = paymentLabels[0].parent.findAllByType('Text').find(node => node.props.children !== 'Pagamento');
    assert.equal(paymentFact.props.children, card);
    assert.equal(String(paymentFact.props.children).includes(bank), false);
    await press('Editar compromisso Loja Sintetica');
    await settle();
    const planEditor = renderer.root.findByProps({accessibilityLabel: 'Editor do compromisso inteiro'});
    assert.equal(planEditor.props.accessibilityValue.text, 'plan-loja');
    assert.equal(selectedChoice('Natureza'), 'Compras e Consumo');
    assert.equal(selectedChoice('Meio de pagamento'), 'Cartão de crédito');
    assert.equal(selectedChoice('Frequência'), 'Mensal');
    assert.equal(selectedChoice('Cartão'), card);
    assert.equal(selectedChoice('Conta'), 'Banco Sintetico');
    assert.equal(selectedChoice('Categoria'), 'Saude');
    assert.equal(renderer.root.findByProps({accessibilityLabel: 'Valor do compromisso'}).props.value, 'R$ 29,90');
    const editorText = planEditor.findAllByType('Text').map(node => [].concat(node.props.children).filter(item => typeof item === 'string').join('')).join('\n');
    assert.doesNotMatch(editorText, /COMPRA|cartao_credito|\bmensal\b|card-sintetico|acc-sintetica/);
    const original = planEditor.findByProps({accessibilityLabel: 'Descrição original'});
    assert.equal(planEditor.findAllByType('TextInput').some(node => node.props.accessibilityLabel === 'Descrição original'), false);
    assert.notEqual(original.props.editable, true);
    assert.equal(original.props.children, 'LOJA1000');
    const editorColors = planEditor.findAll(node => node.props && node.props.style).flatMap(node => [node.props.style].flat(Infinity).filter(Boolean).map(style => style.color)).filter(Boolean);
    assert.ok(editorColors.includes('#C5D5D2'));
    assert.equal(editorColors.includes('#16302E'), false);
    console.log('editor text color #C5D5D2');
    assert.equal(buttons('Apenas a próxima parcela em aberto').length, 1);
    assert.equal(buttons('Todas as parcelas futuras em aberto').length, 1);
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
    await press('Salvar compromisso');
    await settle();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].path, '/v1/commitment-profile');
    const body = writes[0].body;
    assert.equal(body.natureza, 'COMPRA');
    assert.equal(body.metodo_pagamento, 'cartao_credito');
    assert.equal(body.frequency, 'mensal');
    assert.equal(body.card_id, 'card-sintetico');
    assert.equal(body.account_id, 'acc-sintetica');
    assert.equal(body.amount, '29.90');
    assert.equal(body.identity, 'plan-loja');
    assert.equal(body.scope, 'next');
    assert.equal(JSON.stringify(body).includes('Compras e Consumo'), false);
    assert.equal(JSON.stringify(body).includes('Cartão de crédito'), false);
    assert.equal(JSON.stringify(body).includes('Mensal'), false);
    assert.equal(JSON.stringify(body).includes(card), false);
    assert.equal(JSON.stringify(body).includes('Banco Sintetico'), false);
    assert.equal(JSON.stringify(body).includes('R$'), false);
    await dismiss('OK');
    await press('Voltar de Detalhe de Loja Sintetica');
    await press('Abrir Divida Sintetica');
    assert.equal(renderer.root.findAllByProps({accessibilityLabel: 'Classificação do compromisso'}).length, 0);
    assert.doesNotMatch(text(), /Moradia › financiamento/);
    await press('Editar compromisso Divida Sintetica');
    const seriesEditor = renderer.root.findByProps({accessibilityLabel: 'Editor do compromisso inteiro'});
    assert.equal(seriesEditor.props.accessibilityValue.text, 'sintetico|loja');
    assert.notEqual(seriesEditor.props.accessibilityValue.text, 'next-only');
    assert.equal(buttons('Apenas a próxima parcela em aberto').length, 1);
    assert.equal(buttons('Todas as parcelas futuras em aberto').length, 1);
    assert.equal(renderer.root.findAllByType('Modal').length, 0);
    presentation.setPresentationClock(null);
  });

  if (failures.length) {console.error(failures.join(', ')); process.exitCode = 1;}
  else console.log('Commitment redesign checks passed for the requested section.');
}
