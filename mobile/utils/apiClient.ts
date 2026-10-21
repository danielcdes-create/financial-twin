import { ReferenceCache } from './classificationOptions';
import {acknowledgeMutation,rejectMutation,journaledMutation,retainMutation,pendingMutations,type MutationDb,type MutationCommand} from './mutationJournal';
import { telemetry } from './telemetry';

export const READ_TIMEOUT_MS = 20_000;
export const WRITE_TIMEOUT_MS = 45_000;
export const UPLOAD_TIMEOUT_MS = 120_000;
const OPTIONS_PATH = '/v1/transactions/classification-options';
const publicPath = (path: string) => ['/health', '/live', '/v1/auth/status', '/v1/auth/setup', '/v1/auth/session'].includes(path);
export const changesRemoteState = (path:string) => ![
  /^\/v1\/capture-batches\/[^/]+\/preview$/,
  /^\/v1\/capture-batches\/file-status$/,
  /^\/v1\/capture\/interpret$/,
  /^\/v1\/insights\/(?:simulate|liquidity|purchase-scan)$/,
  /^\/v1\/auth\/session$/,
  /^\/v1\/telemetry\//,
].some(pattern=>pattern.test(path.split('?')[0]));
export class ApiError extends Error {
  constructor(message: string, public status?: number, public retryable = false) { super(message); }
}
export const aborted = () => Object.assign(new Error('Consulta cancelada'), { name: 'AbortError' });
export const isAbort = (error: unknown) => error instanceof Error && error.name === 'AbortError';
export const isTransientFailure = (error: unknown) => !isAbort(error) &&
  (error instanceof ApiError ? error.retryable || [408,502,503,504].includes(error.status || 0) : error instanceof Error);
export type Availability = 'checking' | 'connected' | 'reconnecting';
type Flight = { controller: AbortController; promise: Promise<any>; consumers: number; settled: boolean };
type Settings = { baseUrl: string; token: () => string; sessionIdentity?: () => number; awaitSession?: (signal?: AbortSignal) => Promise<void> | null; fetch?: typeof fetch; uploadFetch?: typeof fetch; readTimeoutMs?: number; writeTimeoutMs?: number; uploadTimeoutMs?: number; referenceTtlMs?: number; readRetryDelaysMs?: readonly number[]; requireSession?: boolean; onUnauthorized?: () => void };

const nowMs = () =>
  typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();

export function createApiClient(settings: Settings) {
  const fetcher = settings.fetch || fetch;
  const reads = new Map<string, Flight>();
  const controllers = new Set<AbortController>();
  const references = new ReferenceCache<any>(settings.referenceTtlMs);
  let session = settings.token();
  let sessionIdentity = settings.sessionIdentity?.();
  let readGeneration = 0;
  let connectionGeneration = 0;
  let mutationDb:MutationDb|undefined;
  const mutationReceipts=new Map<string,any>();
  const mutationListeners=new Set<()=>void>();
  const notifyMutations=()=>mutationListeners.forEach(listener=>listener());
  let availability: Availability = 'checking';
  const listeners = new Set<()=>void>();
  const setAvailability = (value: Availability) => {
    if(availability===value)return;
    availability=value;listeners.forEach(listener=>listener());
  };
  // A GET started before a write is not a valid refresh for that write, even
  // when the URL is identical. Cancel only reads; simultaneous writes retain
  // their own acknowledgement and must not cancel one another.
  const invalidateReads = () => {
    readGeneration++;
    for (const flight of reads.values()) flight.controller.abort();
    reads.clear();
    references.invalidate();
  };
  const reset = () => {
    connectionGeneration++;
    for (const controller of controllers) controller.abort();
    invalidateReads();
    session = settings.token();
    sessionIdentity = settings.sessionIdentity?.();
    setAvailability('checking');
  };
  const checkSession = () => { if (session !== settings.token() || sessionIdentity !== settings.sessionIdentity?.()) reset(); };
  const request = async (path: string, init: RequestInit, timeoutMs: number, uploading = false, attempt = 1) => {
    if (!settings.baseUrl) throw new ApiError('Configure EXPO_PUBLIC_API_BASE_URL no ambiente mobile.');
    const token = settings.token();
    const identity = settings.sessionIdentity?.();
    const connection = connectionGeneration;
    const isPublic = publicPath(path);
    if (settings.requireSession && !isPublic && !token) throw new ApiError('Entre na sua conta para continuar.', 401);
    const controller = new AbortController();
    const cancel = () => controller.abort();
    if (init.signal?.aborted) throw aborted();
    controllers.add(controller);
    init.signal?.addEventListener('abort', cancel, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    const startedAt = nowMs();
    const isGet = !init.method || init.method.toUpperCase() === 'GET';
    const isTelemetryEndpoint = path.startsWith('/v1/telemetry/');
    const { traceId, headers: traceHeaders } = telemetry.createRequestHeaders();
    try {
      const headers = new Headers(init.headers);
      if (token) headers.set('Authorization', `Bearer ${token}`);
      if (!(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
      if (!uploading && !isTelemetryEndpoint) {
        Object.entries(traceHeaders).forEach(([k, v]) => {
          if (!headers.has(k)) headers.set(k, v);
        });
      }
      const transport = uploading ? settings.uploadFetch || fetcher : fetcher;
      const response = await transport(settings.baseUrl + path, { ...init, headers, cache: 'no-store', signal: controller.signal });
      const text = await response.text();
      if (controller.signal.aborted) throw aborted();
      let body: any;
      try { body = text ? JSON.parse(text) : null; } catch { body = null; }
      if (!isPublic && (token !== settings.token() || identity !== settings.sessionIdentity?.())) throw aborted();
      if (response.status === 401 && !isPublic && token) settings.onUnauthorized?.();
      const durationMs = nowMs() - startedAt;
      const serverMetrics = telemetry.parseServerMetrics(response.headers);
      if (!isTelemetryEndpoint) {
        if (isGet) {
          telemetry.recordDataFetch({
            path,
            durationMs,
            status: response.ok ? 'ok' : 'error',
            traceId,
            payloadBytes: text.length,
            attempt,
            serverMetrics,
            ...(response.ok ? {} : { error: `HTTP ${response.status}` }),
          });
        } else {
          const serverTotal = serverMetrics?.total_ms ?? null;
          telemetry.record({
            category: 'data_mutation',
            name: `${(init.method || 'POST').toUpperCase()} ${path.split('?')[0]}`,
            duration_ms: Math.round(durationMs * 100) / 100,
            status: response.ok ? 'ok' : 'error',
            trace_id: traceId,
            metadata: {
              uploading,
              status_code: response.status,
              payload_bytes: text.length,
              ...(serverMetrics
                ? {
                    ...serverMetrics,
                    network_overhead_ms:
                      serverTotal !== null
                        ? Math.max(0, Math.round((durationMs - serverTotal) * 100) / 100)
                        : null,
                    server_total_ms: serverTotal,
                    server_db_ms: serverMetrics.db_ms ?? null,
                    server_app_ms: serverMetrics.app_ms ?? null,
                    server_db_calls: serverMetrics.db_calls ?? null,
                  }
                : {}),
            },
          });
        }
      }
      if (!response.ok) throw new ApiError(typeof body?.detail === 'string' ? body.detail : `Erro ${response.status} em ${path}`, response.status,response.headers?.get('X-Operation-Pending')==='1');
      if (body === null) throw new ApiError(`Resposta inválida em ${path}`, response.status);
      if(path==='/health')setAvailability(body.mongodb==='connected'?'connected':'reconnecting');
      return body;
    } catch (error) {
      const durationMs = nowMs() - startedAt;
      if (!isTelemetryEndpoint && !(error instanceof ApiError && error.status !== undefined)) {
        const wasAborted = connection !== connectionGeneration || init.signal?.aborted || controller.signal.aborted;
        if (isGet) {
          telemetry.recordDataFetch({
            path,
            durationMs,
            status: wasAborted && !timedOut ? 'aborted' : 'error',
            traceId,
            attempt,
            error: timedOut ? 'timeout' : error instanceof Error ? error.message : String(error),
          });
        } else {
          telemetry.record({
            category: 'data_mutation',
            name: `${(init.method || 'POST').toUpperCase()} ${path.split('?')[0]}`,
            duration_ms: Math.round(durationMs * 100) / 100,
            status: wasAborted && !timedOut ? 'aborted' : 'error',
            trace_id: traceId,
            metadata: {
              uploading,
              timed_out: timedOut,
              error: error instanceof Error ? error.message : String(error),
            },
          });
        }
      }
      // A transport may ignore AbortSignal and settle after its timeout. Its
      // old timeout cannot change availability after focus/session reset.
      if (connection !== connectionGeneration || init.signal?.aborted) throw aborted();
      if (timedOut){
        setAvailability('reconnecting');
        throw new ApiError(`Tempo esgotado em ${path}.${init.method && init.method !== 'GET' ? ' O servidor pode ter recebido a gravação; confira o resultado antes de repetir.' : ' Tente atualizar novamente.'}`,undefined,true);
      }
      if (controller.signal.aborted) throw aborted();
      if(error instanceof ApiError ? [502,504].includes(error.status||0)||(path==='/health'&&error.status===503) : error instanceof Error)setAvailability('reconnecting');
      throw error;
    } finally {
      clearTimeout(timer);
      controllers.delete(controller);
      init.signal?.removeEventListener('abort', cancel);
    }
  };
  const pause = (delay: number, signal?: AbortSignal) => new Promise<void>((resolve,reject)=>{
    if(signal?.aborted){reject(aborted());return}
    const cancel=()=>{clearTimeout(timer);signal?.removeEventListener('abort',cancel);reject(aborted())};
    const timer=setTimeout(()=>{signal?.removeEventListener('abort',cancel);resolve()},delay);
    signal?.addEventListener('abort',cancel,{once:true});
  });
  const requestRead = async (path: string, init: RequestInit) => {
    const delays=settings.readRetryDelaysMs??[600,1200];
    const deadline=Date.now()+(settings.readTimeoutMs??READ_TIMEOUT_MS);
    for(let attempt=0;;attempt++){
      try{return await request(path,init,Math.max(1,deadline-Date.now()),false,attempt+1)}
      catch(error){
        if(!isTransientFailure(error)||attempt>=delays.length||Date.now()+delays[attempt]>=deadline)throw error;
        await pause(delays[attempt],init.signal||undefined);
      }
    }
  };
  const read = (path: string, init: RequestInit, generation = ''): Promise<any> => {
    if (init.signal?.aborted) return Promise.reject(aborted());
    const epoch = readGeneration;
    const key = JSON.stringify([session, sessionIdentity, epoch, path, generation, [...new Headers(init.headers).entries()]]);
    let flight = reads.get(key);
    const deduped = Boolean(flight);
    if (!flight) {
      const controller = new AbortController();
      const created: Flight = { controller, consumers: 0, settled: false, promise: Promise.resolve() };
      created.promise = requestRead(path, { ...init, signal: controller.signal }).finally(() => {
        created.settled = true;
        if (reads.get(key) === created) reads.delete(key);
      });
      flight = created;
      reads.set(key, flight);
    }
    const shared = flight;
    shared.consumers++;
    if (deduped && !path.startsWith('/v1/telemetry/')) {
      telemetry.recordDataFetch({
        path,
        durationMs: 0,
        status: 'deduped',
        sharedConsumers: shared.consumers,
      });
    }
    return new Promise((resolve, reject) => {
      let finished = false;
      const finish = (callback: (value: any) => void, value: any) => {
        if (finished) return;
        finished = true;
        init.signal?.removeEventListener('abort', cancel);
        shared.consumers--;
        if (!shared.settled && shared.consumers === 0) {
          shared.controller.abort();
          if (reads.get(key) === shared) reads.delete(key);
        }
        callback(value);
      };
      const cancel = () => finish(reject, aborted());
      init.signal?.addEventListener('abort', cancel, { once: true });
      shared.promise.then(value => epoch === readGeneration ? finish(resolve, value) : finish(reject, aborted()), error => finish(reject, error));
    });
  };
  const json = async (path: string, init: RequestInit = {}): Promise<any> => {
    if (init.signal?.aborted) throw aborted();
    if (settings.requireSession && !publicPath(path) && !settings.token()) {
      const identity = settings.sessionIdentity?.();
      const waiting = settings.awaitSession?.(init.signal || undefined);
      if (waiting) {
        await waiting;
        if (identity !== settings.sessionIdentity?.() || !settings.token()) throw aborted();
      }
    }
    checkSession();
    if (!init.method || init.method.toUpperCase() === 'GET') {
      if (path === OPTIONS_PATH) {
        const token = session;
        const identity=sessionIdentity,epoch=readGeneration;
        let fetchedFromNetwork = false;
        try{
          const result = await references.get(generation => {
            if (token !== settings.token() || identity!==settings.sessionIdentity?.()) throw aborted();
            fetchedFromNetwork = true;
            return read(path, init, String(generation));
          });
          if (token !== settings.token() || identity!==settings.sessionIdentity?.()) throw aborted();
          if (!fetchedFromNetwork) {
            telemetry.recordDataFetch({
              path,
              durationMs: 0,
              status: 'cache_hit',
            });
          }
          return result;
        }catch(error){
          // Reference controls must not become permanently empty when an
          // unrelated confirmed write invalidates their in-flight options.
          if(isAbort(error)&&epoch!==readGeneration&&!init.signal?.aborted&&token===settings.token()&&identity===settings.sessionIdentity?.())return json(path,init);
          throw error;
        }
      }
      return read(path, init);
    }
    // A timed-out write may still commit. Invalidate before and after every attempt.
    const mutating=changesRemoteState(path);
    if(mutating)invalidateReads();
    let command:MutationCommand|undefined;
    try {
      if(mutationDb&&journaledMutation(path,init.method.toUpperCase())&&typeof init.body==='string'){
        const identity=settings.sessionIdentity?.(),token=settings.token();
        command=await retainMutation(mutationDb,settings.baseUrl,path,init.method.toUpperCase(),JSON.parse(init.body));
        notifyMutations();
        if(token!==settings.token()||identity!==settings.sessionIdentity?.())throw aborted();
        if(mutationReceipts.has(command.command_key))return await acknowledgeMutation(mutationDb,command,mutationReceipts.get(command.command_key));
        if(command.result)return JSON.parse(command.result);
        init={...init,body:JSON.stringify(JSON.parse(command.body).values)};
      }
      const result=await request(path, init, path === '/v1/capture/interpret' ? settings.uploadTimeoutMs ?? UPLOAD_TIMEOUT_MS : settings.writeTimeoutMs ?? WRITE_TIMEOUT_MS);
      if(command&&mutationDb){
        // Keep an in-memory receipt before attempting fallible local storage.
        if(result?.persisted===true&&result.request_id===JSON.parse(command.body).values.request_id)mutationReceipts.set(command.command_key,result);
        const confirmed=await acknowledgeMutation(mutationDb,command,result);
        mutationReceipts.set(command.command_key,confirmed);
        return confirmed;
      }
      return result;
    }
    catch(error){
      if(command&&mutationDb&&error instanceof ApiError&&!error.retryable&&[400,404,409,422,428].includes(error.status||0)){
        try{await rejectMutation(mutationDb,command,error.status!,error.message)}catch{/* Preserve unresolved journal if local storage fails. */}
      }
      throw error;
    }
    finally { if(mutating)invalidateReads();if(command)notifyMutations(); }
  };
  const upload = async (path: string, body: FormData) => {
    if (settings.requireSession && !publicPath(path) && !settings.token()) {
      const identity = settings.sessionIdentity?.();
      const waiting = settings.awaitSession?.();
      if (waiting) {
        await waiting;
        if (identity !== settings.sessionIdentity?.() || !settings.token()) throw aborted();
      }
    }
    checkSession();
    const mutating=changesRemoteState(path);
    if(mutating)invalidateReads();
    try { return await request(path, { method: 'POST', body }, settings.uploadTimeoutMs ?? UPLOAD_TIMEOUT_MS, true); }
    finally { if(mutating)invalidateReads(); }
  };
  return { json, upload, reset, invalidateReferences: () => references.invalidate(), invalidateReads,
    getReadGeneration: () => readGeneration,
    configureMutationStore:(db:MutationDb)=>{mutationDb=db;},
    pendingMutations:()=>mutationDb?pendingMutations(mutationDb,settings.baseUrl):Promise.resolve([]),
    subscribeMutations:(listener:()=>void)=>{mutationListeners.add(listener);return()=>{mutationListeners.delete(listener)};},
    resumeMutation:(command:MutationCommand)=>{const content=JSON.parse(command.body);if(content.baseUrl!==settings.baseUrl)return Promise.reject(new Error('Esta tentativa pertence a outro servidor.'));return json(command.path,{method:content.method,body:JSON.stringify(content.values)});},
    getAvailability:()=>availability,
    subscribeAvailability:(listener:()=>void)=>{listeners.add(listener);return ()=>{listeners.delete(listener)}} };
}