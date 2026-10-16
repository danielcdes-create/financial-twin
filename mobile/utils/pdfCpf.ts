import * as SecureStore from './sessionStore';
import type {QueueRow} from './syncQueue';

const KEY = 'financial-twin-pdf-cpf';

export function normalizePdfCpf(value: string): string | null {
  const digits = value.replace(/[.\-\s]/g, '');
  return /^[0-9]{11}$/.test(digits) ? digits : null;
}

export async function getPdfCpf(): Promise<string | null> {
  const value = await SecureStore.getItemAsync(KEY);
  return value ? normalizePdfCpf(value) : null;
}

export async function setPdfCpf(value: string): Promise<void> {
  const digits = normalizePdfCpf(value);
  if (!digits) throw new Error('Informe o CPF completo, com 11 dígitos.');
  await SecureStore.setItemAsync(KEY, digits);
}

export async function clearPdfCpf(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY);
}

export function isPdfCapture(row: QueueRow): boolean {
  return (row.kind === 'document' || row.kind === 'batch') &&
    (row.mime_type === 'application/pdf' || /\.pdf$/i.test(row.filename || ''));
}

export async function appendPdfCpf(data: FormData, row: QueueRow): Promise<boolean> {
  if (!isPdfCapture(row)) return false;
  const cpf = await getPdfCpf();
  if (!cpf) return false;
  data.append('pdf_cpf', cpf);
  return true;
}
