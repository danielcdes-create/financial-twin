import { File } from 'expo-file-system';

import type { QueueRow } from './syncQueue';

export async function appendQueuedFile(data: FormData, row: QueueRow): Promise<void> {
  if (!row.local_uri) throw Object.assign(new Error('O arquivo da captura não foi encontrado na fila.'), { status: 410 });
  const file = new File(row.local_uri);
  if (!file.exists) throw Object.assign(new Error('O arquivo salvo no aparelho não está mais disponível. Escolha-o novamente.'), { status: 410 });
  // URI metadata is streamed by uploadTransport.native. Keep bytes() for Expo
  // serializer compatibility, but never invoke it on the native upload path.
  data.append('file', {
    uri: file.uri,
    name: row.filename || file.name,
    type: row.mime_type || file.type || 'application/octet-stream',
    bytes: () => file.bytes(),
  } as any);
}
