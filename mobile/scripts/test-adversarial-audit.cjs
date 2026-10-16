const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const cache = new Map();
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  const moduleObject = {exports: {}};
  const sandbox = {module: moduleObject, exports: moduleObject.exports, Intl, Date, Math, Number, String, Error, JSON, require: name => {
    if (!name.startsWith('.')) throw new Error('Dependência inesperada: ' + name);
    const target = path.resolve(path.dirname(file), name);
    return load(fs.existsSync(target + '.ts') ? target + '.ts' : target);
  }};
  vm.runInNewContext(compiled, sandbox, {filename: file});
  cache.set(file, moduleObject.exports);
  return moduleObject.exports;
}

const presentation = load('utils/commitmentPresentation.ts');
const money = load('utils/moneyInput.ts');
const operation = load('utils/userOperation.ts');
const movements = load('utils/movementFilters.ts');
const queue = load('utils/queueFinalization.ts');

assert.equal(presentation.DEFAULT_COMMITMENT_CUTOFF, '2026-10-01');
presentation.setPresentationClock('2026-12-15');
assert.equal(presentation.commitmentCutoff(), '2026-12-01');
assert.equal(presentation.occurrenceState('2026-09-30', false), 'Pago (assumido)');
assert.equal(presentation.occurrenceState('2026-10-10', false), 'Pago (assumido)');
assert.equal(presentation.occurrenceState('2026-11-10', false, '2026-10-15'), 'Pago (assumido)');
assert.equal(presentation.occurrenceState('2026-12-10', false), 'Vencido · sem vínculo');
assert.equal(presentation.occurrenceState('2026-09-30', true), 'Pago vinculado');
assert.equal(presentation.reconcileCharge('2026-12-10', 'Vencido · sem vínculo', '2026-12-15'), true);
assert.equal(presentation.reconcileCharge('2026-10-10', 'Pago (assumido)', '2026-12-15'), false);
assert.equal(presentation.reconcileCharge('2026-11-10', 'Previsto', '2026-10-15'), false);
const partial = presentation.pendingOccurrences('managed', {
  status: 'ativo', kind: 'assinatura', frequency: 'mensal', renewal_status: 'automatica',
  next_due_date: '2026-12-10', amount: 40, linked_transactions: [],
  installment_overrides: {'2026-12-10': {amount: '15.00', due_date: '2026-12-12', cancelled: false}, '2027-01-10': {amount: '40.00', cancelled: true}},
}, '2026-12-15');
assert.equal(partial[0].due, '2026-12-12');
assert.equal(partial[0].amount, 15);
assert.equal(partial.some(row => row.due === '2027-01-10'), false);
assert.equal(presentation.obligationShare(100, 3, 1), 33.33);
assert.equal(presentation.obligationShare(100, 3, 2), 33.33);
assert.equal(presentation.obligationShare(100, 3, 3), 33.34);
assert.equal(presentation.anchoredDue({next_due_date: '2026-01-31', frequency: 'mensal', schedule_anchor_day: 31}, 1), '2026-02-28');
assert.equal(presentation.anchoredDue({next_due_date: '2026-01-31', frequency: 'mensal', schedule_anchor_day: 31}, 2), '2026-03-31');
assert.equal(presentation.paymentMethodLabel('cartao'), 'Cartão de crédito');
assert.equal(presentation.paymentMethodLabel('cartao_credito'), 'Cartão de crédito');
assert.equal(presentation.paymentMethodLabel('codigo_desconhecido'), '');
const octoberUtc = new Date('2026-10-01T02:30:00Z');
presentation.setPresentationClock(null);
assert.equal(presentation.civilToday(octoberUtc), '2026-09-30');
const month = presentation.civilMonthWindow(octoberUtc);
assert.equal(month.monthStart, '2026-09-01');
assert.equal(month.monthEnd, '2026-09-30');

assert.equal(money.parseMoneyInput('1.234,56'), '1234.56');
assert.equal(money.parseMoneyInput('1234.56'), '1234.56');
assert.equal(money.parseMoneyInput('1.2,34'), null);
assert.equal(money.parseMoneyInput('1e2'), null);
assert.equal(money.parseMoneyInput('-10,00'), null);
assert.equal(money.parseMoneyInput('-10,00', {signed: true}), '-10.00');

assert.equal(movements.classificationLine({natureza: 'COMPRA', categoria: 'Outros', subcategoria: 'Doação', metodo_pagamento: 'cartao_credito'}), 'Compras e Consumo · Outros › Doação · Cartão de crédito');
assert.equal(movements.classificationLine({natureza: 'TRANSFERENCIA', categoria: 'Outros', subcategoria: 'Doação', metodo_pagamento: 'cartao_credito'}), 'Transferência Neutra · Outros › Doação · Cartão de crédito');
assert.equal(movements.ownAccountTransfer({natureza: 'TRANSFERENCIA', vendedor: 'Instituição', subcategoria: 'Doação'}), false);
assert.equal(movements.ownAccountTransfer({natureza: 'TRANSFERENCIA', transfer: {from_account_key: 'origem', to_account_key: 'destino'}}), true);
const plan = movements.parcelamentoPurchaseSummary({parcelamento_purchase: true, valor: -25, parcelamento: {count: 4, total: 100}});
assert.equal(plan.count, 4);
assert.equal(plan.total, 100);
assert.equal(movements.parcelamentoListMark({parcelamento_purchase: true, installment_count: 3, valor: -25, parcelamento: {count: 4, total: 100}}), 'parcela 1/4');

const stored = [];
const db = {
  async getAllAsync() { return stored.map(row => ({...row})); },
  async runAsync(sql, ...params) {
    if (sql.startsWith('DELETE FROM event_queue')) {
      const [eventId, kind, localUri, payload, filename, mime, id] = params;
      const index = stored.findIndex(row => row.event_id === eventId && row.kind === kind && row.local_uri === localUri && row.payload === payload && row.filename === filename && row.mime_type === mime && row.id === id);
      if (index >= 0) stored.splice(index, 1);
    }
    assert.doesNotMatch(sql, /transactions|provisional/);
  },
};
const failed = {id: 7, event_id: 'evt-1', kind: 'file', local_uri: 'file://recibo.jpg', payload: '{}', filename: 'recibo.jpg', mime_type: 'image/jpeg'};
stored.push({...failed});
void (async () => {
  await queue.finalizeQueueRow(db, {...failed, payload: '{"alterado":true}'}, () => {}).then(() => { throw new Error('identidade diferente deveria falhar'); }, error => {
    assert.match(error.message, /preservado/);
  });
  assert.equal(stored.length, 1);
  await queue.finalizeQueueRow(db, failed, () => {});
  assert.equal(stored.length, 0);
  const pending = operation.runUserAction(async () => {
    void new Promise(() => {});
    return 'ack';
  });
  assert.equal(operation.userOperationSnapshot().busy, true);
  assert.equal(await pending, 'ack');
  assert.equal(operation.userOperationSnapshot().busy, false);
  const app = fs.readFileSync('App.tsx', 'utf8');
  const refresh = app.slice(app.indexOf('const refreshAfterMutation'), app.indexOf('const refreshAfterMutation') + 700);
  assert.match(refresh, /void Promise\.all/);
  assert.match(refresh, /return Promise\.resolve\(\)/);
  const board = fs.readFileSync('components/CommitmentBoard.tsx', 'utf8');
  assert.match(board, /Editar parcelas futuras/);
  assert.match(board, /source="managed"/);
  const intent = fs.readFileSync('app/+native-intent.ts', 'utf8');
  assert.match(intent, /handle-share/);
  assert.doesNotMatch(intent, /journal|pendingIntent|persist/);
  const gradle = fs.readFileSync('android/app/build.gradle', 'utf8');
  assert.match(gradle, /signingConfig signingConfigs\.debug/);
  console.log('Adversarial client probes passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
