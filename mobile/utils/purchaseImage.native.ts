import { File } from 'expo-file-system';
import type { PurchaseImage } from './purchaseImage';

export async function purchaseImageFormData(image: PurchaseImage): Promise<FormData> {
  const file = new File(image.uri);
  if (!file.exists) throw new Error('O print compartilhado não está mais disponível. Escolha-o novamente.');
  if (file.size > 10 * 1024 * 1024) throw new Error('Escolha um print de até 10 MiB.');
  const data = new FormData();
  data.append('file', {
    uri: file.uri,
    name: image.fileName || 'print.png',
    type: image.mimeType || file.type || 'image/png',
    bytes: () => file.bytes(),
  } as any);
  return data;
}
