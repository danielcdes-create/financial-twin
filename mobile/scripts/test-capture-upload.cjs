// Exercise the installed Expo multipart serializer with the native capture adapters.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const contents = Uint8Array.from([255,216,255,10,20,30]);
const pdfContents = new TextEncoder().encode('%PDF-1.7\nsynthetic statement');
class NativeFile {
  constructor(uri) { this.uri=uri; this.exists=true; this.contents=uri.endsWith('.pdf')?pdfContents:contents; this.size=uri.includes('big')?10*1024*1024+1:this.contents.length; this.type=uri.endsWith('.pdf')?'application/pdf':'image/jpeg'; this.name=uri.endsWith('.pdf')?'statement.pdf':'receipt.jpg'; }
  bytes() { return Promise.resolve(this.contents); }
}
class NativeFormData {
  constructor(){this.parts=[];}
  append(name,value){this.parts.push([name,value]);}
  entries(){return this.parts[Symbol.iterator]();}
}
function load(path) {
  const module={exports:{}};
  const source=ts.transpileModule(fs.readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(source,{module,exports:module.exports,require:(name)=>{
    if(name==='expo-file-system')return {File:NativeFile};
    if(name.includes('blobUtils'))return {blobToArrayBufferAsync:blob=>blob.arrayBuffer()};
    throw new Error(`Unexpected import ${name}`);
  },FormData:NativeFormData,Blob,Uint8Array,TextEncoder,encodeURIComponent,Math,Promise,Error,console});
  return module.exports;
}
(async()=>{
  const converter=load('node_modules/expo/src/winter/fetch/convertFormData.ts');
  const image=load('utils/purchaseImage.native.ts');
  const voice=load('utils/voiceFile.native.ts');
  const evidence=load('utils/uploadFile.native.ts');
  const forms=[await image.purchaseImageFormData({uri:'file:///receipt.jpg',mimeType:'image/jpeg',fileName:'receipt.jpg'}),await voice.voiceFormData('file:///audio.m4a','audio.m4a','audio/mp4')];
  const queued=new NativeFormData();await evidence.appendQueuedFile(queued,{local_uri:'file:///receipt.jpg',filename:'receipt.jpg',mime_type:'image/jpeg'});forms.push(queued);
  queued.append('batch_id','synthetic-batch-id');
  const pdf=new NativeFormData();await evidence.appendQueuedFile(pdf,{local_uri:'file:///statement.pdf',filename:'itau_extrato_092026.pdf',mime_type:'application/pdf'});forms.push(pdf);
  pdf.append('batch_id','synthetic-pdf-batch');
  for(const form of forms){
    const {body,boundary}=await converter.convertFormDataAsync(form,'test-boundary');
    assert.equal(boundary,'test-boundary');
    assert(Buffer.from(body).includes(Buffer.from(form===pdf?pdfContents:contents)),'Actual file bytes must reach multipart body');
    assert(Buffer.from(body).includes(Buffer.from('name="file"; filename="')),'File must be recognized as a file');
    assert.equal(form.parts[0][1].uri.startsWith('file:///'),true,'RN fetch fallback must also have a URI');
    if(form===queued)assert(Buffer.from(body).includes(Buffer.from('synthetic-batch-id')),'Batch identity and actual file bytes must travel together');
    if(form===pdf){
      for(const text of ['synthetic-pdf-batch','filename="itau_extrato_092026.pdf"','content-type: application/pdf'])
        assert(Buffer.from(body).includes(Buffer.from(text)), 'PDF name, MIME and batch identity must be preserved');
    }
  }
  await assert.rejects(()=>image.purchaseImageFormData({uri:'file:///big.jpg'}),/10 MiB/);
  console.log('Native captures: print, PDF, voice and queued evidence multipart bytes — passed (installed Expo serializer; no device).');
})().catch(error=>{console.error(error);process.exitCode=1;});
