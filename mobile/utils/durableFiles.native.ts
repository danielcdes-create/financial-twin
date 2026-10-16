import { Directory, File, Paths } from 'expo-file-system';
export async function persistQueuedFile(uri:string,id:string,maxBytes=20*1024*1024):Promise<string> {
  const folder=new Directory(Paths.document,'capture-queue');folder.create({idempotent:true,intermediates:true});
  const source=new File(uri);if(!source.exists)throw new Error('Arquivo de origem indisponível.');
  const size=source.size;
  if(!Number.isFinite(size)||size<=0)throw new Error('O arquivo está vazio ou não pôde ser lido.');
  if(size>maxBytes)throw new Error(`O arquivo excede ${maxBytes/(1024*1024)} MiB.`);
  const target=new File(folder,id);
  if(target.exists)throw new Error('Já existe um arquivo com este identificador na fila.');
  try {
    // Expo SDK 57 copy() completes asynchronously. Only inspect/enqueue after it resolves.
    await source.copy(target);
    if(!target.exists||target.size!==size)throw new Error('A cópia do arquivo não foi confirmada.');
  } catch(error) {
    try {if(target.exists)target.delete()} catch {/* Keep the original failure visible. */}
    throw error;
  }
  return target.uri;
}
export async function removeQueuedFile(uri:string|null):Promise<void> {
  const folder=new Directory(Paths.document,'capture-queue');
  if(uri?.startsWith(folder.uri.replace(/\/$/,'')+'/')){const file=new File(uri);if(file.exists)file.delete()}
}
