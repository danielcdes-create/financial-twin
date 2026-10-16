import {queuedBlob} from './durableFiles';
export async function voiceFormData(uri: string, name = 'capture.webm', mime = 'audio/webm'): Promise<FormData> {
  const blob = await queuedBlob(uri);
  if (!blob.size || blob.size > 20 * 1024 * 1024) throw new Error('Escolha um áudio não vazio de até 20 MiB.');
  const data = new FormData();
  data.append('file', blob, name);
  return data;
}
export async function createVoiceText(text: string): Promise<string> {
  return URL.createObjectURL(new Blob([text], {type:'text/plain;charset=utf-8'}));
}
