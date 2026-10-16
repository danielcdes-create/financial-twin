import { File, Paths } from 'expo-file-system';
export async function voiceFormData(uri: string, name = 'capture.m4a', mime = 'audio/mp4'): Promise<FormData> {
  const file = new File(uri);
  if (!file.exists) throw new Error('O áudio não está disponível. Escolha-o novamente.');
  if (!file.size || file.size > 20 * 1024 * 1024) throw new Error('Escolha um áudio não vazio de até 20 MiB.');
  const data = new FormData();
  data.append('file', {uri:file.uri, name, type:mime, bytes:()=>file.bytes()} as any);
  return data;
}
export async function createVoiceText(text: string): Promise<string> {
  const file = new File(Paths.cache, `voice-${Date.now()}.txt`);
  file.create(); file.write(text);
  return file.uri;
}
