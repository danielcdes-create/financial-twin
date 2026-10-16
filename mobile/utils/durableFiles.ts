// Web files survive document disposal: the queue stores an IndexedDB key.
let database: Promise<IDBDatabase> | undefined;
function open(): Promise<IDBDatabase> {
  return database ||= new Promise((resolve,reject)=>{
    const request=indexedDB.open('financial-twin-files',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('files');
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>{database=undefined;reject(request.error)};
  });
}
async function operation<T>(mode:IDBTransactionMode, action:(store:IDBObjectStore)=>IDBRequest<T>):Promise<T> {
  const db=await open();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction('files',mode);const request=action(tx.objectStore('files'));
    tx.oncomplete=()=>resolve(request.result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('Gravação interrompida.'));
  });
}
export async function persistQueuedFile(uri:string,id:string,maxBytes=20*1024*1024):Promise<string> {
  const response=await fetch(uri); if(!response.ok)throw new Error('Não foi possível ler o arquivo selecionado.');
  const blob=await response.blob();if(!blob.size)throw new Error('O arquivo está vazio ou não pôde ser lido.');
  if(blob.size>maxBytes)throw new Error(`O arquivo excede ${maxBytes/(1024*1024)} MiB.`);
  // One queue/receipt identity owns immutable original bytes. IndexedDB add is
  // atomic across tabs and refuses a collision instead of replacing evidence.
  await operation('readwrite',store=>store.add(blob,id));return `idb:${id}`;
}
export async function queuedBlob(uri:string):Promise<Blob> {
  if(!uri.startsWith('idb:')) {
    try {const response=await fetch(uri);if(response.ok)return await response.blob()}catch{/* An old temporary URI may be gone. */}
    throw Object.assign(new Error('Reanexe o arquivo: esta captura antiga usava um arquivo temporário.'),{status:410});
  }
  const value=await operation<Blob|undefined>('readonly',store=>store.get(uri.slice(4)));
  if(!value)throw Object.assign(new Error('Arquivo indisponível. Reanexe o documento.'),{status:410});return value;
}
export async function removeQueuedFile(uri:string|null):Promise<void> {
  if(uri?.startsWith('idb:'))await operation('readwrite',store=>store.delete(uri.slice(4)));
}
