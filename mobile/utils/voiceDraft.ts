import {persistQueuedFile} from './durableFiles';
import {newOperationId} from './reviewCommand';
import {preserveConflictingDraft} from './reviewDraftGuard';
import {voiceEvidenceFileUri} from './voiceEvidenceFile';
export type VoiceEvidenceCommand = {version: 1; event_id: string; captured_at: string; text: string; file?: {uri: string; name: string; mime: 'text/plain'}};
export type VoiceEvidenceStage = 'prepared' | 'queued';
export type VoiceDraft = {clip: {uri: string; name: string; mime: string} | null; text: string; hasTranscript: boolean; appliedContext: string | null; evidence?: VoiceEvidenceCommand; evidence_stage?: VoiceEvidenceStage};
export function voiceEvidenceCommandFile(command: VoiceEvidenceCommand): NonNullable<VoiceEvidenceCommand['file']> {
  const expected = {uri: voiceEvidenceFileUri(command.event_id), name: `voice-${command.event_id}.txt`, mime: 'text/plain' as const};
  if(command.file && (command.file.uri !== expected.uri || command.file.name !== expected.name || command.file.mime !== expected.mime)) throw new Error('A identidade do arquivo da evidência mudou. Preserve a tentativa.');
  return expected;
}
type Db = {getAllAsync: <T>(sql: string, ...params: any[]) => Promise<T[]>; runAsync: (sql: string, ...params: any[]) => Promise<unknown>};
type DraftChange = {kind: 'deleted' | 'delete-failed'; generation: number};
type DraftOwner = {write: Promise<void>; generation: number; listeners: Set<(change: DraftChange) => void>};
const owners = new WeakMap<Db, Map<string, DraftOwner>>();
function owner(db: Db, key: string): DraftOwner {
  let drafts = owners.get(db);
  if (!drafts) {drafts = new Map(); owners.set(db, drafts);}
  let current = drafts.get(key);
  if (!current) {current = {write: Promise.resolve(), generation: 0, listeners: new Set()}; drafts.set(key, current);}
  return current;
}
function enqueue(current: DraftOwner, task: () => Promise<void>): Promise<void> {
  const write = current.write.catch(() => undefined).then(task);
  current.write = write;
  return write;
}
export const voiceDraftGeneration = (db: Db, key: string) => owner(db, key).generation;
export const flushVoiceDraft = (db: Db, key: string) => owner(db, key).write.catch(() => undefined);
export function subscribeVoiceDraftChanges(db: Db, key: string, listener: (change: DraftChange) => void): () => void {
  const current = owner(db, key); current.listeners.add(listener);
  return () => {current.listeners.delete(listener);};
}
export async function loadVoiceDraft(db: Db, key: string): Promise<VoiceDraft | null> {
  const current = owner(db, key);
  for (;;) {
    await current.write.catch(() => undefined);
    const generation = current.generation;
    const rows = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
    if (generation === current.generation) return rows[0] ? JSON.parse(rows[0].state) : null;
  }
}
export function saveVoiceDraft(db: Db, key: string, state: VoiceDraft, expectedGeneration = voiceDraftGeneration(db, key)): Promise<void> {
  const current = owner(db, key);
  return enqueue(current, async () => {
    if (current.generation !== expectedGeneration) return;
    const rows = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
    const previous: VoiceDraft | undefined = rows[0] ? JSON.parse(rows[0].state) : undefined;
    // An autosave from the editable state must not erase a sealed operation.
    if (previous?.evidence && (JSON.stringify(previous.evidence) !== JSON.stringify(state.evidence) || state.text.trim() !== previous.evidence.text)) return;
    // A late render from the prepared stage cannot undo confirmed queue insertion.
    const serialized = JSON.stringify(previous?.evidence_stage === 'queued' ? {...state, evidence_stage: 'queued'} : state);
    if (!state.clip && !state.text.trim()) await db.runAsync('DELETE FROM voice_drafts WHERE draft_key=?', key);
    else await db.runAsync('INSERT INTO voice_drafts(draft_key,state,updated_at) VALUES(?,?,?) ON CONFLICT(draft_key) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at', key, serialized, new Date().toISOString());
});
}
export function sealVoiceEvidence(db: Db, key: string, state: VoiceDraft, expectedGeneration: number): Promise<VoiceEvidenceCommand> {
  const current = owner(db, key);
  let command: VoiceEvidenceCommand;
  return enqueue(current, async () => {
    if (current.generation !== expectedGeneration) throw new Error('O rascunho mudou. Recupere a voz antes de enviar.');
    const rows = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
    const saved: VoiceDraft | undefined = rows[0] ? JSON.parse(rows[0].state) : undefined;
    command = saved?.evidence || {version: 1, event_id: newOperationId(), captured_at: new Date().toISOString(), text: state.text.trim()};
    if (!command.text || command.text !== state.text.trim()) throw new Error('A evidência já foi preparada com outro texto. Conclua o envio antes de iniciar uma nova captura.');
    command = {...command, file: voiceEvidenceCommandFile(command)};
    await db.runAsync('INSERT INTO voice_drafts(draft_key,state,updated_at) VALUES(?,?,?) ON CONFLICT(draft_key) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at', key, JSON.stringify({...state, text: command.text, evidence: command, evidence_stage: saved?.evidence_stage || 'prepared'}), new Date().toISOString());
  }).then(() => command!);
}
export function markVoiceEvidenceQueued(db: Db, key: string, command: VoiceEvidenceCommand): Promise<void> {
  const current = owner(db, key);
  return enqueue(current, async () => {
    const rows = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
    if (!rows[0]) throw new Error('O rascunho enviado não foi encontrado para finalizar.');
    const saved: VoiceDraft = JSON.parse(rows[0].state);
    if (JSON.stringify(saved.evidence) !== JSON.stringify(command)) throw new Error('A evidência local mudou. Preserve-a para conferência.');
    if(saved.evidence_stage === 'queued') return;
    await db.runAsync('INSERT INTO voice_drafts(draft_key,state,updated_at) VALUES(?,?,?) ON CONFLICT(draft_key) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at', key, JSON.stringify({...saved, evidence_stage:'queued'}), new Date().toISOString());
    const confirmed = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
    if(!confirmed[0] || JSON.parse(confirmed[0].state).evidence_stage !== 'queued') throw new Error('Não foi possível guardar a finalização pendente da evidência.');
  });
}
export function finalizeVoiceEvidence(db: Db, key: string, command: VoiceEvidenceCommand): Promise<void> {
  const current = owner(db, key);
  return enqueue(current, async () => {
    const rows = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
    if (!rows[0]) return;
    const saved: VoiceDraft = JSON.parse(rows[0].state);
    if (JSON.stringify(saved.evidence) !== JSON.stringify(command)) throw new Error('A evidência local mudou. Preserve-a para conferência.');
    if (saved.evidence_stage !== 'queued') throw new Error('Confirme a evidência na fila antes de finalizar o rascunho.');
    await db.runAsync('DELETE FROM voice_drafts WHERE draft_key=? AND state=?', key, rows[0].state);
    if ((await db.getAllAsync('SELECT * FROM voice_drafts WHERE draft_key=?', key)).length) throw new Error('Não foi possível finalizar o rascunho enviado.');
    const generation = ++current.generation;
    current.listeners.forEach(listener => listener({kind: 'deleted', generation}));
  });
}
export function deleteVoiceDraft(db: Db, key: string, expectedState?: string): Promise<void> {
  const current = owner(db, key), generation = ++current.generation;
  return enqueue(current, async () => {
    try {
      if (expectedState !== undefined) {
        const rows = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
        if (rows[0]?.state !== expectedState) throw new Error('O rascunho de voz mudou. Atualize a lista e confira antes de excluir.');
      }
      const result = await db.runAsync(expectedState === undefined ? 'DELETE FROM voice_drafts WHERE draft_key=?' : 'DELETE FROM voice_drafts WHERE draft_key=? AND state=?', ...(expectedState === undefined ? [key] : [key, expectedState]));
      if (expectedState !== undefined && (result as {changes?: number} | undefined)?.changes === 0) throw new Error('O rascunho de voz mudou. Atualize a lista e confira antes de excluir.');
      if (expectedState !== undefined) {
        const remaining = await db.getAllAsync<{state: string}>('SELECT * FROM voice_drafts WHERE draft_key=?', key);
        if (remaining.length) throw new Error('O rascunho de voz mudou. Atualize a lista e confira antes de excluir.');
      }
      // The recording may also belong to a queued capture or a preserved draft.
      // Deletion removes this draft's reference; shared bytes stay available.
      current.listeners.forEach(listener => listener({kind: 'deleted', generation}));
    } catch (failure) {
      current.listeners.forEach(listener => listener({kind: 'delete-failed', generation}));
      throw failure;
    }
  });
}
export function preserveVoiceDraft(db: Db, key: string, state: VoiceDraft, expectedGeneration = voiceDraftGeneration(db, key), onStart?:()=>void): Promise<void> {
  const current = owner(db, key);
  return enqueue(current, async () => {
    if(current.generation!==expectedGeneration)return;
    onStart?.();
    if(state.clip || state.text.trim())await preserveConflictingDraft(db, 'voice', key, state);
  });
}
export async function durableVoiceClip(clip: NonNullable<VoiceDraft['clip']>): Promise<NonNullable<VoiceDraft['clip']>> {
  return {...clip, uri: await persistQueuedFile(clip.uri, `voice-draft-${newOperationId()}`, 20 * 1024 * 1024)};
}
