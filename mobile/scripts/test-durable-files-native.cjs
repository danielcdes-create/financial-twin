const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

const files = new Map();
let copies = 0;
class Directory {
  constructor(...parts) { this.uri = parts.map(part => part.uri || part).join('/'); }
  create() {}
}
class File {
  constructor(...parts) { this.uri = parts.map(part => part.uri || part).join('/'); }
  get exists() { return files.has(this.uri); }
  get size() { return files.get(this.uri)?.size || 0; }
  async copy(target) {
    copies++;
    // SDK 57 resolves only after native IO; the target is unavailable before then.
    await new Promise(resolve => setTimeout(resolve, 10));
    const original = files.get(this.uri);
    files.set(target.uri, {...original, size: this.uri.includes('truncated') ? 1 : original.size});
    if (this.uri.includes('denied')) throw new Error('Native copy failed');
  }
  delete() { files.delete(this.uri); }
}
function load(file) {
  const module = {exports: {}};
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  vm.runInNewContext(source, {module, exports: module.exports, require: name => {
    if (name === 'expo-file-system') return {File, Directory, Paths: {document: {uri: 'file:///documents'}}};
    throw new Error(`Unexpected import ${name}`);
  }});
  return module.exports;
}

(async () => {
  const durable = load('utils/durableFiles.native.ts');
  const folder = 'file:///documents/capture-queue/';
  for (const [name, bytes] of [['print.jpg', Buffer.from([255,216,255,1,2,3])], ['statement.pdf', Buffer.from('%PDF-1.7\nstatement')]]) {
    const uri = `file:///cache/${name}`;
    files.set(uri, {size: bytes.length, bytes});
    const saved = await durable.persistQueuedFile(uri, name);
    assert.equal(saved, folder + name);
    assert.deepEqual(files.get(saved).bytes, bytes);
    assert(files.has(uri), 'The selected original must remain intact');
    await durable.removeQueuedFile(saved);
    assert(!files.has(saved));
    assert(files.has(uri));
  }
  assert.equal(copies, 2);
  await assert.rejects(durable.persistQueuedFile('file:///missing.jpg', 'missing'), /origem indisponível/);
  files.set('file:///empty.pdf', {size: 0});
  await assert.rejects(durable.persistQueuedFile('file:///empty.pdf', 'empty'), /vazio|lido/);
  files.set('file:///oversize.pdf', {size: 20 * 1024 * 1024 + 1});
  await assert.rejects(durable.persistQueuedFile('file:///oversize.pdf', 'oversize'), /20 MiB/);
  files.set('file:///batch-oversize.pdf', {size: 10 * 1024 * 1024 + 1});
  await assert.rejects(durable.persistQueuedFile('file:///batch-oversize.pdf', 'batch-oversize', 10 * 1024 * 1024), /10 MiB/);
  assert.equal(copies, 2, 'Invalid files must be rejected before copy');
  for (const name of ['truncated.jpg', 'denied.pdf']) {
    files.set(`file:///cache/${name}`, {size: 8, bytes: Buffer.from('original')});
    await assert.rejects(durable.persistQueuedFile(`file:///cache/${name}`, name));
    assert(!files.has(folder + name), 'Failed or partial copies must be removed');
    assert(files.has(`file:///cache/${name}`), 'Failure must preserve the original');
  }
  const existing = {size: 7, bytes: Buffer.from('pending')};
  files.set(folder + 'already-queued', existing);
  await assert.rejects(durable.persistQueuedFile('file:///cache/print.jpg', 'already-queued'));
  assert.deepEqual(files.get(folder + 'already-queued'), existing, 'Never replace another queued file');
  await durable.removeQueuedFile('file:///cache/print.jpg');
  assert(files.has('file:///cache/print.jpg'), 'Cleanup must stay inside the capture queue');

  const selection = load('utils/captureSelection.ts');
  for (const kind of ['batch', 'document']) {
    const pdf = selection.capturePickerOptions(kind, 'pdf');
    assert.equal(pdf.type, 'application/pdf');
    assert.equal(pdf.copyToCacheDirectory, true);
    assert.equal(selection.capturePickerOptions(kind, 'pdf', 'android').copyToCacheDirectory, false, 'Never copy a PDF on Android onActivityResult/UI thread');
    assert.equal(pdf.base64, false, 'Web picker must not inflate the PDF into base64');
    assert.equal(pdf.multiple, false);
    assert.deepEqual(Array.from(selection.capturePickerOptions(kind, 'image').type), ['image/jpeg', 'image/png']);
    assert.equal(selection.captureMimeType('ITAU_EXTRATO_092026.PDF', undefined), 'application/pdf');
    assert.equal(selection.captureMimeType('print.jpg', 'application/octet-stream'), 'image/jpeg');
  }
  assert(Array.from(selection.capturePickerOptions('statement').type).includes('text/csv'));
  assert.equal(selection.captureFileLimit('batch'), 10 * 1024 * 1024);
  assert.equal(selection.captureFileLimit('document'), 20 * 1024 * 1024);
  files.set('content://bank/statement.pdf', {size: 8, bytes: Buffer.from('original')});
  const contentUri = await durable.persistQueuedFile('content://bank/statement.pdf', 'content-provider');
  assert.deepEqual(files.get(contentUri).bytes, Buffer.from('original'), 'Persist Android provider bytes asynchronously without a picker cache copy');
  console.log('Native capture: await copy for JPEG/PDF, preserve originals, reject partial/failed/oversized files, PDF picker and MIME fallback — passed (simulated native IO).');
})().catch(error => {console.error(error); process.exitCode = 1;});
