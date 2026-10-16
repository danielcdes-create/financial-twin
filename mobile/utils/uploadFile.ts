import type { QueueRow } from './syncQueue';
import { queuedBlob } from './durableFiles';

export async function appendQueuedFile(data: FormData, row: QueueRow): Promise<void> {
  if (!row.local_uri) throw Object.assign(new Error('O arquivo da captura não foi encontrado na fila.'), { status: 410 });
  data.append('file', await queuedBlob(row.local_uri), row.filename || 'capture');
}
