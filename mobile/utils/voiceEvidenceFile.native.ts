import {Directory, File, Paths} from 'expo-file-system';
export const voiceEvidenceFileUri=(id:string)=>new File(Paths.document,'capture-queue',id).uri;

export async function ensureVoiceEvidenceFile(id: string, text: string): Promise<string> {
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error('Identidade da evidência inválida.');
  const folder = new Directory(Paths.document, 'capture-queue');
  folder.create({idempotent: true, intermediates: true});
  const target = new File(folder, id);
  if (target.exists) {
    if (target.textSync() !== text) throw new Error('O arquivo desta evidência contém outro texto. Preserve-o para conferência.');
    return target.uri;
  }
  // Complete the owned temporary file before its atomic move into the queue.
  const temporary = new File(folder, `voice-${id}.pending`);
  temporary.create({overwrite: true});
  temporary.write(text);
  if (temporary.textSync() !== text) throw new Error('A gravação da evidência não foi confirmada.');
  try {await temporary.move(target);}
  catch (error) {if (!target.exists || target.textSync() !== text) throw error;}
  if (!target.exists || target.textSync() !== text) throw new Error('A evidência não está disponível para envio.');
  return target.uri;
}
