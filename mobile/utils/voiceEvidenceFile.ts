import {persistQueuedFile, queuedBlob} from './durableFiles';
export const voiceEvidenceFileUri=(id:string)=>`idb:${id}`;

// A retry owns exactly the same immutable UTF-8 bytes, including after restart.
export async function ensureVoiceEvidenceFile(id: string, text: string): Promise<string> {
  const uri = voiceEvidenceFileUri(id);
  try {
    if (await (await queuedBlob(uri)).text() !== text) throw new Error('O arquivo desta evidência contém outro texto. Preserve-o para conferência.');
    return uri;
  } catch (error) {
    if ((error as {status?: number})?.status !== 410) throw error;
  }
  const source = URL.createObjectURL(new Blob([text], {type: 'text/plain;charset=utf-8'}));
  try {return await persistQueuedFile(source, id);}
  catch (error) {
    // Another finalizer may have completed the same immutable file first.
    try {if (await (await queuedBlob(uri)).text() === text) return uri;} catch {/* Preserve the first failure. */}
    throw error;
  } finally {URL.revokeObjectURL(source);}
}
