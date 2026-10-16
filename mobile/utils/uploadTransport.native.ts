// Expo fetch materializes every multipart file as bytes, then copies the whole
// body in JS. RN's XMLHttpRequest streams URI parts through native networking.
// Preserve names and fields, without passing bytes() callbacks to the bridge.
export const uploadFetch: typeof fetch = (input, init) => {
  if (!(init?.body instanceof FormData)) return fetch(input, init);
  const multipart = new FormData();
  for (const [name, entry] of init.body.entries()) {
    const file = entry as unknown as {uri?: string; name?: string; type?: string};
    if (typeof entry === 'string') multipart.append(name, entry);
    else if (file?.uri) multipart.append(name, {uri: file.uri, name: file.name, type: file.type} as any);
    else throw new Error('O arquivo não está disponível para envio. Escolha-o novamente.');
  }
  return new Promise<Response>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const signal = init.signal;
    const aborted = () => Object.assign(new Error('Consulta cancelada'), {name: 'AbortError'});
    let finished = false;
    const finish = (error?: Error) => {
      if (finished) return;
      finished = true; signal?.removeEventListener('abort', cancel);
      if (error) reject(error);
      else {
        const headers = new Headers();
        for (const line of (xhr.getAllResponseHeaders() || '').split(/\r?\n/)) {
          const colon = line.indexOf(':');
          if (colon > 0) headers.append(line.slice(0, colon).trim(), line.slice(colon + 1).trim());
        }
        resolve({ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, headers,
          text: async () => xhr.responseText} as Response);
      }
    };
    const cancel = () => {finish(aborted()); xhr.abort();};
    if (signal?.aborted) {finish(aborted()); return;}
    xhr.onload = () => finish(xhr.status > 0 ? undefined : new Error('Não foi possível enviar o arquivo. Ele continua na fila.'));
    xhr.onerror = () => finish(new Error('Não foi possível enviar o arquivo. Ele continua na fila.'));
    xhr.onabort = () => finish(aborted());
    xhr.ontimeout = () => finish(new Error('O envio demorou demais. O arquivo continua na fila.'));
    signal?.addEventListener('abort', cancel, {once: true});
    try {
      xhr.open(init.method || 'POST', String(input));
      new Headers(init.headers).forEach((value, name) => xhr.setRequestHeader(name, value));
      xhr.send(multipart);
    } catch (error) {finish(error instanceof Error ? error : new Error('Não foi possível enviar o arquivo.'));}
  });
};
