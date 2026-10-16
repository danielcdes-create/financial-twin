const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm'), ts = require('typescript');
const values = new Map();
let fail = false;
class ApiError extends Error {constructor(message, status) {super(message); this.status = status;}}
class FormData {constructor() {this.parts = [];} append(key, value) {this.parts.push([key, value]);}}
const store = {getItemAsync: async key => values.get(key) ?? null,
  setItemAsync: async (key, value) => {if (fail) throw Error('Storage unavailable'); values.set(key, value);},
  deleteItemAsync: async key => values.delete(key)};
let cpfModule;
function load(file) {
  const module = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText,
    {module, exports: module.exports, FormData, require: name => {
      if (name === './sessionStore') return store;
      if (name === './apiClient') return {ApiError};
      if (name === './pdfCpf') return cpfModule;
      if (name === './uploadFile') return {appendQueuedFile: async (data, row) => data.append('file', row.local_uri)};
      throw Error(name);
    }});
  return module.exports;
}
(async () => {
  const cpf = cpfModule = load('utils/pdfCpf.ts');
  assert.equal(await cpf.getPdfCpf(), null);
  await assert.rejects(cpf.setPdfCpf('123'), /11 dígitos/);
  assert.equal(values.size, 0);
  await cpf.setPdfCpf('012.345.678-90');
  assert.equal(await cpf.getPdfCpf(), '01234567890');
  fail = true; await assert.rejects(cpf.setPdfCpf('98765432100')); fail = false;
  assert.equal(await cpf.getPdfCpf(), '01234567890', 'Storage failure must preserve previous CPF');
  for (const [kind, name, count] of [['batch', 'statement.pdf', 1], ['document', 'receipt.PDF', 1], ['batch', 'print.jpg', 0], ['event', 'statement.pdf', 0]]) {
    const row = {kind, filename: name, payload: '{}', local_uri: 'idb:test'};
    const before = JSON.stringify(row), data = {parts: [], append(key, value) {this.parts.push([key, value]);}};
    await cpf.appendPdfCpf(data, row);
    assert.equal(data.parts.length, count);
    if (count) assert.deepEqual(data.parts[0], ['pdf_cpf', '01234567890']);
    assert.equal(JSON.stringify(row), before, 'CPF must never enter the persisted queue row/payload');
  }
  const submit = load('utils/captureSubmission.ts').submitQueuedCapture;
  const row = {kind: 'batch', filename: 'statement.pdf', payload: '{}', local_uri: 'idb:durable', event_id: 'unchanged-batch'};
  const submitted = [], snapshot = JSON.stringify(row);
  await submit(row, async (_path, body) => {
    submitted.push(body);
    if (submitted.length === 1) throw new ApiError('PDF protegido: informe seu CPF.', 428);
    return {persisted: true};
  }, () => {});
  assert.equal(submitted.length, 2);
  assert(!submitted[0].parts.some(([key]) => key === 'pdf_cpf'), 'Never send CPF before password challenge');
  assert(submitted[1].parts.some(([key, value]) => key === 'pdf_cpf' && value === '01234567890'));
  for (const body of submitted) assert(body.parts.some(([key, value]) => key === 'batch_id' && value === 'unchanged-batch'));
  assert.equal(JSON.stringify(row), snapshot);
  let attempts = 0;
  await assert.rejects(submit(row, async () => {attempts++; throw new ApiError('O CPF informado não abriu este PDF.', 428);}, () => {}));
  assert.equal(attempts, 2, 'Try CPF once per explicit challenge; no password guessing loop');
  let plainBody;
  await submit(row, async (_path, body) => {plainBody = body; return {persisted: true};}, () => {});
  assert(!plainBody.parts.some(([key]) => key === 'pdf_cpf'), 'Ordinary PDF must not transmit CPF');
  await cpf.clearPdfCpf();
  assert.equal(await cpf.getPdfCpf(), null);
  const data = {parts: [], append(key, value) {this.parts.push([key, value]);}};
  await cpf.appendPdfCpf(data, {kind: 'batch', filename: 'statement.pdf'});
  assert.equal(data.parts.length, 0);
  console.log('PDF CPF: protected storage, leading zero, challenge-only one retry, stable identity, queue privacy and removal — passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
