import type { DocumentPickerOptions } from 'expo-document-picker';

export type CaptureFileKind = 'document' | 'statement' | 'batch';
export type CaptureFileFormat = 'image' | 'pdf';

export function capturePickerOptions(kind: CaptureFileKind, format?: CaptureFileFormat, platform = 'web'): DocumentPickerOptions {
  return {
    type: kind === 'statement' ? [
      'text/csv',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/vnd.ms-excel',
    ] : format === 'pdf' ? 'application/pdf' : format === 'image' ? ['image/jpeg', 'image/png'] : ['image/jpeg', 'image/png', 'application/pdf'],
    // Android copies in onActivityResult on the UI thread when this is true.
    // Instead, persist the content:// URI with the asynchronous File.copy().
    copyToCacheDirectory: platform !== 'android',
    base64: false,
    multiple: false,
  };
}

export function captureFileLimit(kind: CaptureFileKind): number {
  return (kind === 'document' ? 20 : 10) * 1024 * 1024;
}

export function captureMimeType(filename: string, mimeType?: string): string {
  const extension = filename.split('.').pop()?.toLowerCase();
  const known: Record<string, string> = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', pdf: 'application/pdf',
    csv: 'text/csv', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  };
  return known[extension || ''] || mimeType || 'application/octet-stream';
}
