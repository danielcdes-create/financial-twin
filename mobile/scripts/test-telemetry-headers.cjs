const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = path.resolve('utils/telemetry.ts');
const moduleValue = { exports: {} };
const recorded = [];
const db = {
  async runAsync(_sql, ...params) {
    recorded.push({
      event_id: params[0],
      timestamp: params[1],
      category: params[2],
      name: params[3],
      duration_ms: params[4],
      status: params[5],
      tab: params[6],
      trace_id: params[7],
      metadata: JSON.parse(params[8]),
    });
  },
};
const code = ts.transpileModule(fs.readFileSync(source, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, {
  module: moduleValue,
  exports: moduleValue.exports,
  console,
  performance,
  URL,
  TextEncoder,
  TextDecoder,
}, { filename: source });
const { telemetry } = moduleValue.exports;
void telemetry.configure({ db });
const ascii = /^[\t\x20-\x7e]*$/;

const initial = telemetry.createRequestHeaders().headers;
assert.equal(initial['X-Client-Tab'], 'Inicio');
assert.match(initial['X-Client-Tab'], ascii);
assert.match(initial['X-Trace-Id'], ascii);

for (const tab of ['Movimentações', 'Revisão', 'Decisões', 'Cartões']) {
  telemetry.startTabTransition(tab);
  const headers = telemetry.createRequestHeaders().headers;
  assert.match(headers['X-Client-Tab'], ascii, tab);
  assert.equal(headers['X-Client-Tab'].includes('í') || headers['X-Client-Tab'].includes('õ') || headers['X-Client-Tab'].includes('ç'), false);
}

const ranResult = telemetry.trackUserAction('Processando…', () => {
  const headers = telemetry.createRequestHeaders().headers;
  assert.equal(headers['X-Client-Action'], 'Processando...');
  assert.match(headers['X-Client-Action'], ascii);
  return 'ok';
}, false);
assert.equal(ranResult, 'ok');
assert.equal(telemetry.getCurrentAction(), '');

telemetry.trackUserAction('Voltando…', () => {
  assert.equal(telemetry.createRequestHeaders().headers['X-Client-Action'], 'Voltando...');
}, false);

let blockedExecuted = false;
const blockedResult = telemetry.trackUserAction('Duplo toque…', () => {
  blockedExecuted = true;
  return 'unexpected';
}, true);
assert.equal(blockedResult, undefined, 'Blocked user action returns undefined');
assert.equal(blockedExecuted, false, 'Blocked user action callback must not execute');
assert.equal(telemetry.getCurrentAction(), '', 'Blocked user action must not pollute active action header');

const actionEvents = recorded.filter(ev => ev.category === 'action');
assert.equal(actionEvents.filter(ev => ev.status === 'ok').length, 2);
const blockedEvents = actionEvents.filter(ev => ev.status === 'blocked');
assert.equal(blockedEvents.length, 1, 'Blocked user action increments blocked telemetry count');
assert.equal(blockedEvents[0].name, 'Duplo toque…');
assert.equal(blockedEvents[0].metadata.reason, 'interaction_gate_busy');
const summary = telemetry.getSummary();
assert.equal(summary.find(row => row.key === 'action:Duplo toque…')?.count, 1);

console.log('PASS telemetry headers stay ASCII for OkHttp and trackUserAction blocked flag verified');
