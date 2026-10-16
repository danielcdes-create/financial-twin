// Production voice component, durable command and queue/ACK helpers; real SQLite.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const React=require('react'),{act,create}=require('react-test-renderer'),{DatabaseSync}=require('node:sqlite');
global.IS_REACT_ACT_ENVIRONMENT=true;
const sqlite=new DatabaseSync(':memory:');
sqlite.exec('CREATE TABLE voice_drafts(draft_key TEXT PRIMARY KEY,state TEXT,updated_at TEXT);CREATE TABLE event_queue(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,kind TEXT,payload TEXT,local_uri TEXT,filename TEXT,mime_type TEXT,created_at TEXT);CREATE TABLE queue_acknowledgements(event_id TEXT PRIMARY KEY,identity TEXT,result TEXT,confirmed_at TEXT);CREATE TABLE preserved_drafts(draft_id TEXT PRIMARY KEY,scope TEXT,item_id TEXT,state TEXT,updated_at TEXT);');
const cache=new Map(),files=new Map();let renderer,deleteFailure=false,insertFailure=false,stageFailure=false,fileFailure=false,fileWrites=0;
const db={getAllAsync:async(sql,...args)=>sqlite.prepare(sql).all(...args),runAsync:async(sql,...args)=>{if(deleteFailure&&sql.startsWith('DELETE FROM voice_drafts'))throw Error('Injected draft DELETE failure');if(insertFailure&&sql.startsWith('INSERT OR IGNORE INTO event_queue'))throw Error('Injected queue failure');if(stageFailure&&sql.startsWith('INSERT INTO voice_drafts')&&JSON.parse(args[1]).evidence_stage==='queued')throw Error('Injected queued stage failure');return sqlite.prepare(sql).run(...args);}};
const native={Platform:{OS:'android'},AppState:{currentState:'active',addEventListener:()=>({remove(){}})},StyleSheet:{create:x=>x},...Object.fromEntries(['View','Text','TextInput','Pressable','ActivityIndicator'].map(name=>[name,name]))};
const mocks={react:React,'react-native':native,'../utils/queueDb':{useSQLiteContext:()=>db},'expo-audio':{AudioModule:{},RecordingPresets:{HIGH_QUALITY:{}},setAudioModeAsync:async()=>{},useAudioRecorder:()=>({}),useAudioRecorderState:()=>({durationMillis:0})},'expo-document-picker':{},
 './durableFiles':{persistQueuedFile:async uri=>uri},'./voiceEvidenceFile':{voiceEvidenceFileUri:id=>'idb:'+id,ensureVoiceEvidenceFile:async(id,text)=>{if(fileFailure)throw Error('Injected file failure');if(files.has(id)){assert.equal(files.get(id),text);}else{files.set(id,text);fileWrites++;}return'idb:'+id;}},'./uploadFile':{appendQueuedFile:async()=>{}},'./pdfCpf':{isPdfCapture:()=>false},'../utils/voiceFile':{voiceFormData:async()=>new FormData()}};
function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,{module:m,exports:m.exports,console,Date,Intl,Error,Promise,Math,JSON,Map,WeakMap,Set,FormData,Blob,URL,setTimeout,clearTimeout,require:name=>{if(mocks[name])return mocks[name];if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return load(fs.existsSync(target+'.tsx')?target+'.tsx':target+'.ts');}},{filename:file});cache.set(file,m.exports);return m.exports;}
const drafts=load('utils/voiceDraft.ts'),{enqueueVoiceEvidence}=load('utils/voiceEvidence.ts'),{persistQueueAcknowledgement}=load('utils/queueAcknowledgements.ts'),{requirePersistedAck}=load('utils/syncQueue.ts'),{submitQueuedCapture}=load('utils/captureSubmission.ts');
const Voice=load('components/VoiceCapture.tsx').default;
const snapshot=text=>({clip:null,text,hasTranscript:true,appliedContext:null});
const rows=()=>db.getAllAsync('SELECT * FROM event_queue ORDER BY id');
const settle=()=>act(async()=>{for(let i=0;i<30;i++)await Promise.resolve();});
const mount=async context=>{await act(async()=>{renderer=create(React.createElement(Voice,{context,draftKey:'evidence',api:async()=>{throw Error('Interpretation cannot run for pending evidence');},upload:async()=>{},onApply:()=>{throw Error('Wrong movement callback');},onEvidenceApply:async(_draft,text,command)=>{assert.equal(text,command.text);const saved=await drafts.loadVoiceDraft(db,'evidence');assert.equal(JSON.stringify(saved.evidence),JSON.stringify(command),'Full command including immutable file identity is persisted before enqueue');assert.ok(['prepared','queued'].includes(saved.evidence_stage));await enqueueVoiceEvidence(db,command);}}));});await settle();};
const unmount=async()=>{if(renderer){await act(async()=>renderer.unmount());renderer=null;}};
const button=label=>renderer.root.findAllByType('Pressable').find(node=>node.props.accessibilityLabel===label);
const press=async label=>{await act(async()=>{const target=button(label);assert.ok(target,label);assert.equal(!!target.props.disabled,false,label);await target.props.onPress();});await settle();};
const text=()=>renderer.root.findAllByType('Text').map(node=>node.children.join('')).join('\n');
(async()=>{
 const legacy={version:1,event_id:'f3333333-3333-4333-8333-333333333333',captured_at:'2026-10-09T18:01:02.123Z',text:'Older sealed draft'};
 await drafts.saveVoiceDraft(db,'compatibility',{...snapshot(legacy.text),evidence:legacy});
 const upgraded=await drafts.sealVoiceEvidence(db,'compatibility',snapshot(legacy.text),drafts.voiceDraftGeneration(db,'compatibility'));
 assert.equal(upgraded.event_id,legacy.event_id);assert.equal(upgraded.captured_at,legacy.captured_at);assert.equal(upgraded.file.uri,'idb:'+legacy.event_id);assert.equal((await drafts.loadVoiceDraft(db,'compatibility')).evidence_stage,'prepared');
 await drafts.deleteVoiceDraft(db,'compatibility');
 await drafts.saveVoiceDraft(db,'evidence',snapshot('Gastei oito reais no café hoje'));
 await mount('document');deleteFailure=true;await press('Salvar texto como evidência');
 const first=(await rows())[0],sealed=(await drafts.loadVoiceDraft(db,'evidence')).evidence;
 assert.equal(first.event_id,sealed.event_id);assert.equal(JSON.parse(first.payload).captured_at,sealed.captured_at);assert.deepEqual(sealed.file,{uri:first.local_uri,name:first.filename,mime:first.mime_type});assert.equal((await drafts.loadVoiceDraft(db,'evidence')).evidence_stage,'queued');assert.match(text(),/Evidência salva/);assert.ok(button('Concluir envio da evidência'));assert.equal(fileWrites,1);
 await drafts.saveVoiceDraft(db,'evidence',{...snapshot(sealed.text),evidence:sealed,evidence_stage:'prepared'});assert.equal((await drafts.loadVoiceDraft(db,'evidence')).evidence_stage,'queued','Late autosave cannot downgrade queued finalization');
 await unmount();await mount('movement');assert.ok(button('Concluir envio da evidência'),'Pending evidence restores independently of target selector');
 await press('Concluir envio da evidência');assert.equal((await rows()).length,1);assert.equal(fileWrites,1);assert.equal((await drafts.loadVoiceDraft(db,'evidence')).evidence.event_id,sealed.event_id);
 // Server ACK/finalized queue then process restart: cleanup must not enqueue again.
 await persistQueueAcknowledgement(db,first,{persisted:true,event_id:first.event_id,processing:{persisted_event_id:first.event_id}});
 await db.runAsync('DELETE FROM event_queue WHERE event_id=?',first.event_id);files.delete(first.event_id);await unmount();await mount('movement');deleteFailure=false;await press('Concluir envio da evidência');assert.equal((await rows()).length,0);assert.equal(await drafts.loadVoiceDraft(db,'evidence'),null);assert.equal(fileWrites,1,'A durable ACK is checked before creating any missing file');
 await unmount();
 // Failure before file/enqueue and kill: sealed command is retained and reused.
 await drafts.saveVoiceDraft(db,'evidence',snapshot('Gastei oito reais no café hoje'));
 const second=await drafts.sealVoiceEvidence(db,'evidence',snapshot('Gastei oito reais no café hoje'),drafts.voiceDraftGeneration(db,'evidence'));
 assert.notEqual(second.event_id,sealed.event_id,'A deliberately new evidence owns a new occurrence');fileFailure=true;await mount('document');await press('Concluir envio da evidência');assert.doesNotMatch(text(),/Evidência salva/,'Prequeue error cannot announce a sent evidence');assert.equal((await drafts.loadVoiceDraft(db,'evidence')).evidence_stage,'prepared');await unmount();fileFailure=false;
 insertFailure=true;await assert.rejects(enqueueVoiceEvidence(db,second),/queue failure/);insertFailure=false;assert.equal(fileWrites,2);
 const restored=(await drafts.loadVoiceDraft(db,'evidence')).evidence;await enqueueVoiceEvidence(db,restored);assert.equal((await rows())[0].event_id,second.event_id);assert.equal(fileWrites,2);
 stageFailure=true;await mount('movement');await press('Concluir envio da evidência');assert.match(text(),/Evidência salva/);assert.equal((await drafts.loadVoiceDraft(db,'evidence')).evidence_stage,'prepared');stageFailure=false;await unmount();
 await drafts.saveVoiceDraft(db,'evidence',snapshot('Late stale autosave'));assert.equal((await drafts.loadVoiceDraft(db,'evidence')).text,second.text,'Old autosave cannot replace sealed intent');
 const current=(await rows())[0];let form;
 await submitQueuedCapture(current,async(_route,body)=>{form=body;return{};},()=>{});assert.equal(form.get('source_provider'),'mobile_voice');assert.equal(form.get('captured_at'),second.captured_at);assert.equal(form.get('event_id'),second.event_id);
 await submitQueuedCapture({...current,payload:'{}'},async(_route,body)=>{assert.equal(body.get('captured_at'),null);return{};},()=>{});
 assert.throws(()=>requirePersistedAck(current,{persisted:true,event_id:current.event_id,processing:{persisted_event_id:sealed.event_id}}),/declaração/);
 requirePersistedAck(current,{persisted:true,event_id:current.event_id,processing:{persisted_event_id:current.event_id}});
 await drafts.deleteVoiceDraft(db,'evidence');assert.equal((await rows()).length,1,'Draft deletion preserves financial queue');assert.equal(files.size,1,'Shared evidence bytes preserved');
 await drafts.saveVoiceDraft(db,'evidence',snapshot('Outra ocorrência deliberada'));
 const third=await drafts.sealVoiceEvidence(db,'evidence',snapshot('Outra ocorrência deliberada'),drafts.voiceDraftGeneration(db,'evidence'));await enqueueVoiceEvidence(db,third);
 await mount('document');await press('Editar nova captura');
 assert.equal((await drafts.loadVoiceDraft(db,'evidence')).evidence,undefined,'Explicit new edit returns to an editable draft');assert.equal((await drafts.loadVoiceDraft(db,'evidence')).text,third.text);assert.equal((await rows()).length,2,'Previous evidence stays queued');
 const priorWrites=fileWrites;await press('Salvar texto como evidência');assert.equal((await rows()).length,3,'Only explicit new capture creates a distinct command');assert.equal(fileWrites,priorWrites+1);assert.equal(await drafts.loadVoiceDraft(db,'evidence'),null);
 console.log('PASS voice cleanup failure/remount, target change, ACK restart, file/SQLite/stage failure, immutable file and command, prepared/queued recovery, legacy draft upgrade, new occurrence, strict ACK and legacy wire compatibility');
})().catch(error=>{console.error(error.stack);process.exitCode=1;}).finally(async()=>{await unmount();sqlite.close();});
