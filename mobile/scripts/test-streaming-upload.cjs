const {Buffer} = require('node:buffer');
// Production capture/API/queue code plus the installed RN/Expo FormData code.
// OS networking is simulated. Any JS file read is forbidden in this test.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('typescript'), babel = require('@babel/core');
const cache = new Map(), requests = [], responses = [], files = new Map();
let lastXHR, bytesRead = 0;
class NativeFile {
  constructor(uri) {this.uri = uri; this.name = 'durable-uuid'; this.type = 'application/octet-stream';}
  get exists() {return files.has(this.uri);}
  bytes() {bytesRead++; throw new Error('Uploading must never read a PDF into JS');}
}
const rnModule = {exports: {}};
const rnCode = babel.transformSync(fs.readFileSync('node_modules/react-native/Libraries/Network/FormData.js', 'utf8'), {
  configFile: false, babelrc: false, plugins: ['@babel/plugin-transform-flow-strip-types','@babel/plugin-transform-modules-commonjs'],
}).code;
vm.runInNewContext(rnCode, {module: rnModule, exports: rnModule.exports, encodeURIComponent});
const NativeFormData = rnModule.exports.default;
class XHR {
  constructor() {lastXHR = this; this.headers = new Headers(); this.aborted = false;}
  open(method, url) {this.method = method; this.url = url;}
  setRequestHeader(name, value) {this.headers.set(name, value);}
  getAllResponseHeaders() {return Object.entries(this.responseHeaders || {}).map(([name,value])=>`${name}: ${value}`).join('\r\n');}
  send(body) {
    const parts = body.getParts(); // Installed React Native native-bridge contract.
    for (const part of parts) if (part.uri) {
      assert(files.has(part.uri));
      assert.equal('bytes' in part, false, 'No bytes function may cross the native bridge');
    }
    requests.push({url: this.url, headers: this.headers, parts});
    const response = responses.shift();
    if (!response) return; // Pending request for abort/timeout tests.
    queueMicrotask(() => {
      if (this.aborted) return;
      if (response.networkError) {this.onerror(); return;}
      this.status = response.status; this.responseHeaders=response.headers; this.responseText = JSON.stringify(response.body); this.onload();
    });
  }
  abort() {this.aborted = true; this.onabort?.();}
}
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}}; cache.set(file, module.exports);
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(code, {module, exports:module.exports, Error, TypeError, Date, URL, Headers, Blob, FormData:NativeFormData,
    XMLHttpRequest:XHR, AbortController, setTimeout, clearTimeout, queueMicrotask,
    fetch: () => {throw new Error('File upload must bypass Expo fetch serialization');}, require:name => {
      if (name === 'expo-file-system') return {File: NativeFile};
      if (name === './sessionStore') return {getItemAsync: async () => '01234567890'};
      const relative = path.resolve(path.dirname(file), name);
      return load(relative + (name === './uploadFile' ? '.native.ts' : '.ts'));
    }}, {filename:file});
  cache.set(file,module.exports);return module.exports;
}
const field = (request, name) => request.parts.find(part => part.fieldName === name);
(async () => {
  load('node_modules/expo/src/winter/FormData.ts').installFormDataPatch(NativeFormData);
  const {uploadFetch} = load('utils/uploadTransport.native.ts');
  const {createApiClient} = load('utils/apiClient.ts');
  const {submitQueuedCapture} = load('utils/captureSubmission.ts');
  const {createQueueSync} = load('utils/syncQueue.ts');
  const {AuthState} = load('utils/authState.ts');
  const state = new AuthState(); state.activate('synthetic-token', Date.now() + 60_000);
  const api = createApiClient({baseUrl:'https://synthetic.invalid',token:state.getToken,requireSession:true,uploadFetch,
    fetch:()=>{throw new Error('Upload used the ordinary transport');},uploadTimeoutMs:1000});
  state.subscribe(api.reset);
  const row = {id:1, event_id:'synthetic-batch',kind:'batch',payload:'{}',local_uri:'file:///documents/capture-queue/uuid',filename:'extrato.pdf',mime_type:'application/pdf'};
  const original = Buffer.from('%PDF-1.7\nsynthetic encrypted statement'); files.set(row.local_uri,original);
  responses.push({status:428,body:{detail:'Informe CPF'}}, {status:200,body:{persisted:true,draft_persisted:true,event_id:row.event_id}});
  const result = await submitQueuedCapture(row,api.upload,()=>assert(state.getToken()));
  assert.equal(result.draft_persisted,true);
  assert.equal(requests.length,2); assert.equal(field(requests[0],'pdf_cpf'),undefined);
  assert.equal(field(requests[1],'pdf_cpf').string,'01234567890');
  for (const request of requests) {
    assert.equal(request.headers.get('authorization'),'Bearer synthetic-token');
    assert.equal(request.headers.get('content-type'),null,'Native networking supplies the multipart boundary');
    assert.equal(field(request,'file').uri,row.local_uri);
    assert.match(field(request,'file').headers['content-disposition'],/filename="extrato.pdf"/);
    assert.equal(field(request,'file').headers['content-type'],'application/pdf');
    assert.equal(field(request,'batch_id').string,row.event_id);
    assert.equal(field(request,'event_id').string,row.event_id,'All uploads carry the immutable local operation ID');
  }
  assert.equal(bytesRead,0); assert.equal(files.get(row.local_uri),original);
  responses.push({status:409,headers:{'X-Operation-Pending':'1'},body:{detail:'Operação ainda em andamento'}});
  await assert.rejects(submitQueuedCapture(row,api.upload,()=>{}),error=>error.status===409&&error.retryable===true);
  // The real queue must preserve the original for network failure, 422 and bad ACK.
  for (const response of [{networkError:true},{status:422,body:{detail:'PDF inválido'}},
      {status:200,body:{persisted:true,draft_persisted:false,event_id:row.event_id}}]) {
    let removed = 0, failed = 0;
    responses.push(response);
    const sync = createQueueSync({prepare:async()=>true,list:async()=>[row],send:item=>submitQueuedCapture(item,api.upload,()=>{}),
      remove:async()=>{removed++;},done:()=>{},recordFailure:async()=>{failed++;},changed:()=>{}});
    await sync(); assert.equal(removed,0); assert.equal(failed,1); assert.equal(files.get(row.local_uri),original);
  }
  // Manual lock cancels a native transfer and discards a late response.
  const pending = submitQueuedCapture(row,api.upload,()=>{});
  await new Promise(resolve=>setTimeout(resolve,0));
  const canceled = assert.rejects(pending,error=>error.name==='AbortError');
  state.lock(); await canceled; assert.equal(lastXHR.aborted,true);
  const count = requests.length;
  await assert.rejects(submitQueuedCapture(row,api.upload,()=>{}),/Entre/); assert.equal(requests.length,count);
  state.activate('synthetic-token',Date.now()+60_000);
  const short = createApiClient({baseUrl:'https://synthetic.invalid',token:state.getToken,requireSession:true,uploadFetch,uploadTimeoutMs:5});
  await assert.rejects(submitQueuedCapture(row,short.upload,()=>{}),/Tempo esgotado/);
  assert.equal(lastXHR.aborted,true);assert.equal(bytesRead,0);
  console.log('PASS: native URI upload with installed RN/Expo FormData, no JS file reads, filename/MIME/identity/CPF challenge, queue retention, lock cancellation and timeout. No physical device.');
})().catch(error=>{console.error(error);process.exitCode=1;});
