// Real voice component, shared interaction gate, alert and draft writer.
// Synthetic SQLite rows and recorder/files; no user data or microphone.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const rows = new Map(), preserved = new Map(), cache = new Map();
let renderer, writeHook, nativeAlerts = 0;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
};
const db = {
  getAllAsync: async (sql, key) => {
    if (sql.includes('voice_drafts')) return key ? (rows.has(key) ? [rows.get(key)] : []) : [...rows.values()];
    throw Error(sql);
  },
  runAsync: async (sql, ...args) => {
    if (writeHook) await writeHook(sql, args);
    if (sql.includes('preserved_drafts')) {preserved.set(args[0], args); return {changes: 1};}
    if (!sql.includes('voice_drafts')) throw Error(sql);
    const [key, state, updated_at] = args;
    if (sql.startsWith('DELETE')) {
      if (sql.includes('AND state=?') && rows.get(key)?.state !== state) return {changes: 0};
      return {changes: Number(rows.delete(key))};
    }
    rows.set(key, {draft_key: key, state, updated_at});
    return {changes: 1};
  },
};
const recorder = {uri: 'synthetic:voice', record() {}, stop: async () => {}, prepareToRecordAsync: async () => {}};
const native = {
  Platform: {OS: 'android'}, AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
  StyleSheet: {create: styles => styles}, Alert: {alert() {nativeAlerts++;}},
  ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'ActivityIndicator'].map(name => [name, name])),
};
const mocks = {
  react: React, 'react-native': native, '../utils/queueDb': {useSQLiteContext: () => db},
  'expo-audio': {AudioModule: {requestRecordingPermissionsAsync: async () => ({granted: true})}, RecordingPresets: {HIGH_QUALITY: {}}, setAudioModeAsync: async () => {}, useAudioRecorder: () => recorder, useAudioRecorderState: () => ({durationMillis: 0})},
  'expo-document-picker': {}, '../utils/voiceFile': {voiceFormData: async () => new FormData()}, '../utils/devicePrompt': {withDevicePrompt: task => task()},
  './durableFiles': {persistQueuedFile: async uri => uri},
};
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const module = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText, {
    module, exports: module.exports, console, Date, Intl, Math, JSON, Promise, Error, FormData, setTimeout, clearTimeout,
    require: name => {
      if (mocks[name]) return mocks[name];
      if (!name.startsWith('.')) return require(name);
      const next = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(next + '.tsx') ? next + '.tsx' : next + '.ts');
    },
  }, {filename: file});
  cache.set(file, module.exports);
  return module.exports;
}
const Voice = load('components/VoiceCapture.tsx').default;
const drafts = load('utils/voiceDraft.ts');
const alert = load('utils/appAlert.ts');
const operations = load('utils/userOperation.ts');
const fullDraft = text => ({clip: {uri: 'synthetic:shared-audio', name: 'voice.m4a', mime: 'audio/mp4'}, text, hasTranscript: true, appliedContext: null});
const seed = (key, state) => rows.set(key, {draft_key: key, state: JSON.stringify(state), updated_at: '2026-10-06T12:00:00Z'});
const props = key => ({draftKey: key, context: 'movement', api: async () => ({draft: {}}), upload: async () => ({text: 'Transcribed'}), onApply() {}});
const settle = async () => act(async () => {for (let i = 0; i < 20; i++) await Promise.resolve();});
const mount = async value => {await act(async () => {renderer = create(value);}); await settle();};
const unmount = async () => {if (renderer) {await act(async () => renderer.unmount()); renderer = null;}};
const field = () => renderer.root.findAll(node => node.type === 'TextInput' && node.props.accessibilityLabel === 'Transcrição da voz')[0];
const button = label => renderer.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityLabel === label)[0];
const content = () => renderer.root.findAllByType('Text').map(node => node.children.join('')).join('\n');
const press = async label => {await act(async () => {const target = button(label); assert.ok(target, label); assert.equal(!!target.props.disabled, false, label); target.props.onPress();}); await settle();};
const edit = async text => {await act(async () => field().props.onChangeText(text)); await settle();};
const confirm = async () => {await act(async () => {const dialog = alert.dialogSnapshot(); assert.ok(dialog); alert.dismissDialog(dialog.buttons.find(item => item.style === 'destructive'), dialog);}); await settle();};
const tests = [];
const test = (name, work) => tests.push({name, work});

test('Voice draft can be canceled, deleted and remounted without audio or transcript restoration', async () => {
  const key = 'voice-local'; seed(key, fullDraft('My transcript'));
  await mount(React.createElement(Voice, props(key)));
  await press('Excluir rascunho de voz');
  assert.equal(nativeAlerts, 0, 'Confirmation stays inside the Activity');
  await act(async () => alert.cancelDialog());
  assert.equal(field().props.value, 'My transcript'); assert.ok(rows.has(key));
  await press('Excluir rascunho de voz'); await confirm();
  await drafts.flushVoiceDraft(db, key); await settle();
  assert.equal(rows.has(key), false); assert.equal(!!field(), false); assert.equal(preserved.size, 0);
  await unmount(); await mount(React.createElement(Voice, props(key)));
  assert.equal(!!button('Excluir rascunho de voz'), false); assert.equal(rows.has(key), false);
});
test('Every voice context and the collapsed applied editor offer draft deletion', async () => {
  for (const context of ['purchase', 'movement', 'commitment', 'document']) {
    const key = 'context-' + context; seed(key, fullDraft('Context transcript'));
    let applied = 0;
    await mount(React.createElement(Voice, {...props(key), context, collapseAfterApply: true, onApply() {applied++;}}));
    assert.ok(button('Excluir rascunho de voz'), context);
    if (context !== 'document') {await press('Preencher campos com a voz'); assert.match(content(), /Voz aplicada/); assert.ok(button('Excluir rascunho de voz'));}
    await press('Excluir rascunho de voz'); await confirm();
    assert.equal(rows.has(key), false); assert.equal(applied, context === 'document' ? 0 : 1, 'Deletion leaves already applied fields alone');
    await unmount();
  }
});
test('Delete waits for an in-flight write and rejects delayed saves and archives from the excluded generation', async () => {
  const key = 'voice-write-race'; seed(key, fullDraft('Original'));
  await mount(React.createElement(Voice, props(key)));
  const pending = deferred(), generation = drafts.voiceDraftGeneration(db, key);
  let started = false;
  writeHook = async sql => {if (sql.startsWith('INSERT INTO voice_drafts') && !started) {started = true; await pending.promise;}};
  await edit('Edited before delete'); assert.equal(started, true);
  const removed = drafts.deleteVoiceDraft(db, key); assert.ok(rows.has(key));
  await act(async () => pending.resolve()); await act(async () => removed); await settle(); writeHook = undefined;
  await drafts.saveVoiceDraft(db, key, fullDraft('Late callback'), generation);
  await drafts.preserveVoiceDraft(db, key, fullDraft('Late archive'), generation);
  assert.equal(rows.has(key), false); assert.equal(preserved.size, 0); assert.equal(!!field(), false);
});
test('Delete failure retains the editor and audio, reports the failure and permits another edit and retry', async () => {
  const key = 'voice-delete-failure'; seed(key, fullDraft('Keep this'));
  await mount(React.createElement(Voice, props(key)));
  writeHook = async sql => {if (sql.startsWith('DELETE')) throw Error('Synthetic deletion failure');};
  await press('Excluir rascunho de voz'); await confirm();
  assert.equal(field().props.value, 'Keep this'); assert.ok(rows.has(key));
  assert.match(content(), /Não foi possível excluir.*Synthetic deletion failure/); assert.equal(operations.isUserOperationBusy(), false);
  writeHook = undefined; await edit('Edit after failure'); await drafts.flushVoiceDraft(db, key);
  assert.equal(JSON.parse(rows.get(key).state).text, 'Edit after failure');
  await press('Excluir rascunho de voz'); await confirm(); assert.equal(rows.has(key), false);
});
test('A stale listed snapshot cannot delete or overwrite a newer voice draft', async () => {
  const key = 'voice-stale-snapshot'; seed(key, fullDraft('Open editor'));
  await mount(React.createElement(Voice, props(key)));
  const listedState = rows.get(key).state;
  await drafts.saveVoiceDraft(db, key, fullDraft('Newer external text'));
  await act(async () => assert.rejects(drafts.deleteVoiceDraft(db, key, listedState), /mudou/)); await settle();
  assert.equal(JSON.parse(rows.get(key).state).text, 'Newer external text', 'Failure must not autosave stale visible content');
  assert.equal(field().props.value, 'Open editor', 'A failed deletion retains the user draft');
});
test('A delayed transcription cannot restore a draft deleted externally', async () => {
  const key = 'voice-transcribe-race'; seed(key, fullDraft('Before upload'));
  const response = deferred(); await mount(React.createElement(Voice, {...props(key), upload: () => response.promise}));
  await press('Transcrever com xAI');
  await act(async () => drafts.deleteVoiceDraft(db, key));
  await act(async () => response.resolve({text: 'Late transcript'})); await settle();
  assert.equal(rows.has(key), false); assert.equal(!!field(), false); assert.doesNotMatch(content(), /Late transcript/);
});
test('Native recorder finalization after unmount cannot recreate an explicitly deleted draft', async () => {
  const key = 'voice-recorder-race', stopped = deferred(), originalStop = recorder.stop;
  let stops = 0; recorder.stop = () => {stops++; return stopped.promise;};
  try {
    await mount(React.createElement(Voice, props(key))); await press('Gravar voz'); await unmount();
    await drafts.deleteVoiceDraft(db, key);
    await act(async () => stopped.resolve()); await settle(); await drafts.flushVoiceDraft(db, key);
    assert.equal(stops, 1); assert.equal(rows.has(key), false);
    await mount(React.createElement(Voice, props(key))); assert.equal(!!button('Excluir rascunho de voz'), false);
  } finally {recorder.stop = originalStop;}
});
test('Draft writers are isolated by database even when the logical key is identical', async () => {
  const otherRows = new Map(), otherDb = {
    getAllAsync: async (_sql, key) => otherRows.has(key) ? [otherRows.get(key)] : [],
    runAsync: async (sql, key, state) => {if (sql.startsWith('DELETE')) otherRows.delete(key); else otherRows.set(key, {state});},
  };
  const key = 'same-key'; await drafts.saveVoiceDraft(db, key, fullDraft('First DB'));
  await drafts.saveVoiceDraft(otherDb, key, fullDraft('Second DB')); await drafts.deleteVoiceDraft(db, key);
  assert.equal((await drafts.loadVoiceDraft(otherDb, key)).text, 'Second DB');
});
test('A void web delete result still verifies that the matching draft was actually removed', async () => {
  const key = 'void-delete', original = JSON.stringify(fullDraft('Listed snapshot'));
  let saved = {state: original};
  const webDb = {
    getAllAsync: async () => saved ? [{...saved}] : [],
    runAsync: async () => {saved = {state: JSON.stringify(fullDraft('Concurrent snapshot'))};},
  };
  await assert.rejects(drafts.deleteVoiceDraft(webDb, key, original), /mudou/);
  assert.equal(JSON.parse(saved.state).text, 'Concurrent snapshot');
  await drafts.flushVoiceDraft(webDb, key); // Previous failure does not block another deletion.
});
(async () => {
  let failures = 0;
  for (const {name, work} of tests) {
    rows.clear(); preserved.clear(); writeHook = undefined; alert.clearDialogs();
    try {await work(); console.log('PASS', name);} catch (failure) {failures++; console.error('FAIL', name, failure.stack);}
    finally {writeHook = undefined; await unmount(); alert.clearDialogs(); await settle();}
  }
  console.log(`${tests.length - failures}/${tests.length} voice draft deletion scenarios passed`);
  process.exitCode = Number(failures > 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
