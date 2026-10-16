// Isolated real web queue; native file metadata and HTTP are synthetic.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createHash} = require('node:crypto'), ts = require('typescript');
const {Buffer} = require('node:buffer');
const storage = new Map(), files = new Map(), cache = new Map(), requests = [], removedFiles = [];
let failingStorageKey = '', session = 1, nativeBytesReads = 0;
class NativeFile {
  constructor(uri) { this.uri = uri; this.name = uri.split('/').pop(); this.type = 'application/pdf'; }
  get exists() { return files.has(this.uri); }
  bytes() { nativeBytesReads++; throw Error('Native reconciliation must stream the URI without materializing bytes'); }
}
class NativeFormData {
  constructor() { this.fields = []; }
  append(name, value) { this.fields.push([name, value]); }
  get(name) { return this.fields.find(field => field[0] === name)?.[1] ?? null; }
}
const mocks = {
  'expo-file-system': {File: NativeFile},
  './sessionStore': {getItemAsync: async () => {throw Error('Reconciliation must not retrieve the stored CPF');}},
};
function load(file) {
  file = path.resolve(file); if (cache.has(file)) return cache.get(file);
  const m = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  }}).outputText, {
    module: m, exports: m.exports, console, FormData: NativeFormData,
    navigator: {locks: {request: async (_name, work) => work()}},
    localStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => {if (failingStorageKey === key) throw Error('Quota exceeded'); storage.set(key, value);},
    },
    require: name => {
      if (mocks[name]) return mocks[name];
      if (!name.startsWith('.')) return require(name);
      const target = path.resolve(path.dirname(file), name === './uploadFile' ? './uploadFile.native' : name);
      return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : target + '.ts');
    },
  }, {filename: file});
  cache.set(file, m.exports); return m.exports;
}
const db = load('utils/queueDb.web.tsx').webQueueDb;
const {reconcileImportedFiles} = load('utils/queueRecovery.ts');
const {readQueueAcknowledgement} = load('utils/queueAcknowledgements.ts');
const QUEUE_KEY = 'financial-twin-event-queue', STATE_KEY = 'financial-twin-queue-state', ACK_KEY='financial-twin-queue-acknowledgements';
const BATCH_ID = '33333333-3333-4333-8333-333333333333';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const list = () => db.getAllAsync('SELECT * FROM event_queue ORDER BY id');
const checkSession = () => {if (session !== 1) throw Object.assign(Error('A sessão mudou'), {status: 401});};
async function reset() {storage.clear(); files.clear(); requests.length = 0; removedFiles.length = 0; failingStorageKey = ''; session = 1;}
async function seed(eventId, content = 'original PDF bytes', extra = {}) {
  const row = {event_id: eventId, kind: 'document', payload: '{}', local_uri: 'file:///capture-queue/' + eventId,
    filename: 'extrato.pdf', mime_type: 'application/pdf', ...extra};
  files.set(row.local_uri, Buffer.from(content));
  await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)',
    row.event_id, row.kind, row.payload, row.local_uri, row.filename, row.mime_type, '2026-10-03');
  await db.runAsync('INSERT INTO queue_state', row.event_id, 2, null, 'only BRL is supported', 1);
  return (await list()).find(item => item.event_id === eventId);
}
function server(imports = new Map()) {
  return async (url, data) => {
    assert.equal(url, '/v1/capture-batches/file-status');
    assert.deepEqual(data.fields.map(field => field[0]), ['event_id', 'file'], 'Only identity and original file; no CPF/extraction');
    const event_id = data.get('event_id'), file = data.get('file');
    assert.ok(file.uri.startsWith('file:///capture-queue/'));
    const sha256 = hash(files.get(file.uri)), record = imports.get(sha256);
    requests.push({url, event_id, sha256});
    const imported = record?.status === 'committed' && record?.result?.persisted === true;
    return {imported, event_id, sha256, batch_id: imported ? record.batch_id : null};
  };
}
const importedServer = content => server(new Map([[hash(Buffer.from(content)), {batch_id: BATCH_ID, status: 'committed', result: {persisted: true}}]]));
const reconcile = (rows, upload, queueDb = db) => reconcileImportedFiles(queueDb, rows, upload, checkSession);
const strings = value => Array.from(value);
(async () => {
  await reset();
  const old = await seed('11111111-1111-4111-8111-111111111111', 'imported elsewhere');
  const distinct = await seed('22222222-2222-4222-8222-222222222222', 'different bytes with same filename', {kind: 'batch'});
  assert.deepEqual(strings(await reconcile(await list(), importedServer('imported elsewhere'))), [old.event_id]);
  assert.deepEqual(strings((await list()).map(row => row.event_id)), [distinct.event_id]);
  assert.equal(files.has(old.local_uri), true); assert.equal(files.has(distinct.local_uri), true);
  const receipt=await readQueueAcknowledgement(db,old);
  assert.equal(receipt.persisted,true);assert.equal(receipt.imported,true);assert.equal(receipt.event_id,old.event_id);
  assert.equal(receipt.sha256,hash(files.get(old.local_uri)));assert.equal(receipt.batch_id,BATCH_ID);
  assert.equal(JSON.parse(storage.get(ACK_KEY))[0].identity.includes(old.local_uri),true);
  assert.equal(removedFiles.length,0,'Reconciliation never invokes destructive file housekeeping');
  cache.delete(path.resolve('utils/queueDb.web.tsx'));
  const restartedDb=load('utils/queueDb.web.tsx').webQueueDb;
  assert.equal((await readQueueAcknowledgement(restartedDb,old)).sha256,receipt.sha256,'Receipt and original URI survive adapter reinitialization');
  assert.equal(JSON.parse(storage.get(STATE_KEY)).some(row => row.event_id === old.event_id), false);
  assert.ok(requests.some(request => request.event_id === old.event_id));

  for (const record of [undefined, {status: 'draft', result: {persisted: true}}, {status: 'discarded', result: {persisted: true}},
    {status: 'committed', result: {persisted: false}}, {status: 'committed'}]) {
    await reset(); const row = await seed('uncommitted', 'no committed import');
    const imports = new Map(record ? [[hash(files.get(row.local_uri)), {...record, batch_id: BATCH_ID}]] : []);
    assert.deepEqual(strings(await reconcile([row], server(imports))), []);
    assert.equal((await list()).length, 1); assert.equal(files.has(row.local_uri), true);
    assert.equal(JSON.parse(storage.get(STATE_KEY))[0].attempt_count, 2);
  }

  const validAck = row => ({imported: true, event_id: row.event_id, sha256: hash(files.get(row.local_uri)), batch_id: BATCH_ID});
  for (const malformed of [() => null, () => ({}), row => ({...validAck(row), imported: 'true'}), row => ({...validAck(row), event_id: 'someone-else'}),
    row => ({...validAck(row), sha256: null}), row => ({...validAck(row), sha256: 'ab'.repeat(31)}),
    row => ({...validAck(row), batch_id: null}), row => ({...validAck(row), batch_id: 'not-a-batch-uuid'})]) {
    await reset(); const row = await seed('malformed');
    assert.deepEqual(strings(await reconcile([row], async () => malformed(row))), []);
    assert.equal((await list()).length, 1); assert.equal(files.has(row.local_uri), true);
  }

  await reset(); const retry = await seed('transport-failure', 'retry bytes'), following = await seed('following', 'confirmed bytes');
  const good = importedServer('confirmed bytes');
  assert.deepEqual(strings(await reconcile([retry, following], async (url, data) => {
    if (data.get('event_id') === retry.event_id) throw Error('Network offline'); return good(url, data);
  })), [following.event_id], 'A failed conference does not block subsequent regular candidates');
  assert.equal(files.has(retry.local_uri), true); assert.equal(JSON.parse(storage.get(STATE_KEY))[0].attempt_count, 2);
  assert.deepEqual(strings(await reconcile([retry], importedServer('retry bytes'))), [retry.event_id], 'Failure remains recheckable');

  for (const invalidSession of ['switch', 'unauthorized']) {
    await reset(); const first = await seed('session-first'), next = await seed('session-next'); let calls = 0;
    await assert.rejects(reconcile([first, next], async () => {
      calls++; if (invalidSession === 'unauthorized') throw Object.assign(Error('Unauthorized'), {status: 401});
      session = 2; return validAck(first);
    }), error => error.status === 401);
    assert.equal(calls, 1); assert.equal((await list()).length, 2); assert.equal(files.has(first.local_uri), true);
  }

  await reset(); const initialSession = await seed('already-switched'); session = 2;
  await assert.rejects(reconcile([initialSession], async () => {throw Error('Must not upload');}), error => error.status === 401);
  assert.equal((await list()).length, 1); assert.equal(files.has(initialSession.local_uri), true);

  await reset(); const rerouted = await seed('rerouted');
  assert.deepEqual(strings(await reconcile([rerouted], async () => {
    await db.runAsync("UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document'", rerouted.event_id); return validAck(rerouted);
  })), []);
  assert.equal((await list())[0].kind, 'batch'); assert.equal(files.has(rerouted.local_uri), true);

  await reset(); const atomic = await seed('atomic-guard');
  const racingDb = {...db, runAsync: async (sql, ...params) => {
    if (sql.startsWith('DELETE FROM event_queue')) await db.runAsync("UPDATE event_queue SET kind='batch' WHERE event_id=? AND kind='document'", atomic.event_id);
    return db.runAsync(sql, ...params);
  }};
  assert.deepEqual(strings(await reconcile([atomic], async () => validAck(atomic), racingDb)), [], 'Guarded delete protects a route changed after revalidation');
  assert.equal((await list())[0].kind, 'batch'); assert.equal(files.has(atomic.local_uri), true);
  assert.equal(JSON.parse(storage.get(STATE_KEY))[0].event_id, atomic.event_id);

  await reset(); const discarded = await seed('reused-event'); let replacement;
  assert.deepEqual(strings(await reconcile([discarded], async () => {
    const ack = validAck(discarded); await db.runAsync('DELETE FROM event_queue WHERE event_id=?', discarded.event_id);
    replacement = await seed(discarded.event_id, 'replacement bytes', {local_uri: 'file:///capture-queue/replacement'}); return ack;
  })), []);
  assert.notEqual(replacement.id, discarded.id); assert.equal((await list())[0].local_uri, replacement.local_uri);
  assert.equal(files.has(discarded.local_uri), true); assert.equal(files.has(replacement.local_uri), true);

  await reset(); const shared = await seed('shared-file', 'shared');
  const sharedOther = await seed('shared-other', 'shared', {local_uri: shared.local_uri, kind: 'native_event'});
  assert.deepEqual(strings(await reconcile([shared], importedServer('shared'))), [shared.event_id]);
  assert.equal((await list())[0].event_id, sharedOther.event_id); assert.equal(files.has(shared.local_uri), true);

  await reset(); const unavailable = await seed('missing-native-file'); files.delete(unavailable.local_uri);
  assert.deepEqual(strings(await reconcile([unavailable], server())), []); assert.equal(requests.length, 0); assert.equal((await list()).length, 1);
  const ignored = await seed('text-event', 'text', {kind: 'event', mime_type: 'text/plain', filename: 'event.txt'});
  assert.deepEqual(strings(await reconcile([ignored], async () => {throw Error('Non-PDF must not upload');})), []);

  await reset(); const quota = await seed('quota-failure'); failingStorageKey = QUEUE_KEY;
  assert.deepEqual(strings(await reconcile([quota], async () => validAck(quota))), []); failingStorageKey = '';
  assert.equal((await list()).length, 1); assert.equal(files.has(quota.local_uri), true); assert.equal(removedFiles.length, 0);
  assert.equal(JSON.parse(storage.get(STATE_KEY))[0].event_id, quota.event_id);

  await reset(); const stateQuota = await seed('state-write-failure'); failingStorageKey = STATE_KEY;
  await reconcile([stateQuota], async () => validAck(stateQuota)); failingStorageKey = '';
  assert.equal(files.has(stateQuota.local_uri), true, 'Any incomplete local cleanup preserves the original bytes'); assert.equal(removedFiles.length, 0);
  assert.equal((await readQueueAcknowledgement(db,stateQuota)).imported,true,'Confirmed import remains acknowledged even if state cleanup fails');
  await reset(); const receiptQuota=await seed('receipt-write-failure');failingStorageKey=ACK_KEY;
  assert.deepEqual(strings(await reconcile([receiptQuota],async()=>validAck(receiptQuota))),[]);failingStorageKey='';
  assert.equal((await list()).length,1);assert.equal(files.has(receiptQuota.local_uri),true,'Unpersisted receipt preserves the pending row and original bytes');
  assert.equal(nativeBytesReads, 0);
  console.log('Queue reconciliation: exact-hash ACK, distinct filenames/bytes, uncommitted drafts, malformed ACKs, offline retry, session barriers, guarded races and storage retention — passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
