import {ApiError} from './apiClient';
import {appendPdfCpf, isPdfCapture} from './pdfCpf';
import {appendQueuedFile} from './uploadFile';
import type {QueueRow} from './syncQueue';
export function voiceCaptureTimestamp(row: QueueRow): string | undefined {
  if (row.kind !== 'document' || !row.filename?.startsWith('voice-')) return;
  const payload = JSON.parse(row.payload);
  if (payload.voice_command_version !== 1) return;
  if (typeof payload.captured_at !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*Z$/.test(payload.captured_at) || !Number.isFinite(Date.parse(payload.captured_at)))
    throw new Error('O instante desta evidência não foi reconhecido. Preserve a tentativa para conferência.');
  return payload.captured_at;
}

export async function submitQueuedCapture(row: QueueRow, upload: (path: string, data: FormData) => Promise<any>, assertSession: () => void): Promise<any> {
  const path = row.kind === 'batch' ? '/v1/capture-batches/scan' : /\.(csv|xlsx)$/i.test(row.filename || '') ? '/v1/import' : '/v1/documents/extract';
  const body = () => {
    const data = new FormData();
    data.append('event_id', row.event_id);
    data.append('source_provider', row.filename?.startsWith('voice-') ? 'mobile_voice' : 'mobile_capture');
    const captured = voiceCaptureTimestamp(row);
    if (captured) data.append('captured_at', captured);
    if (row.kind === 'batch') data.append('batch_id', row.event_id);
    return data;
  };
  const data = body();
  await appendQueuedFile(data, row);assertSession();
  try {return await upload(path, data);}
  catch (error) {
    // 428 is an explicit password challenge, before extraction/financial persistence.
    // Ordinary PDFs never send the CPF. Retry once with the same durable file/identity.
    if (!(error instanceof ApiError) || error.status !== 428 || !isPdfCapture(row)) throw error;
    const protectedData = body();
    if (!await appendPdfCpf(protectedData, row)) throw error;
    await appendQueuedFile(protectedData, row);assertSession();
    return await upload(path, protectedData);
  }
}
