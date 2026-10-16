export type PurchaseImage = { uri: string; mimeType?: string | null; fileName?: string | null; fileSize?: number | null };

export async function purchaseImageFormData(image: PurchaseImage): Promise<FormData> {
  if (image.fileSize && image.fileSize > 10 * 1024 * 1024) throw new Error('Escolha um print de até 10 MiB.');
  const response = await fetch(image.uri);
  if (!response.ok) throw new Error('Não foi possível ler o print.');
  const data = new FormData();
  data.append('file', await response.blob(), image.fileName || 'print.png');
  return data;
}
