const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = path.resolve('utils/telemetry.ts');
const moduleValue = { exports: {} };
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

telemetry.trackUserAction('Processando…', () => {
  const headers = telemetry.createRequestHeaders().headers;
  assert.equal(headers['X-Client-Action'], 'Processando...');
  assert.match(headers['X-Client-Action'], ascii);
});
telemetry.trackUserAction('Voltando…', () => {
  assert.equal(telemetry.createRequestHeaders().headers['X-Client-Action'], 'Voltando...');
});

console.log('PASS telemetry headers stay ASCII for OkHttp');
