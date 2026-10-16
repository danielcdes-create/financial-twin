import { isAbort, isTransientFailure } from './apiClient';

type Entry = { key: string; controller: AbortController; promise: Promise<void> };
export class LatestQueries {
  private active = new Map<string, Entry>();
  private errors: Record<string, string> = {};
  private failed = new Map<string,()=>Promise<void>>();
  constructor(private changed: (pending: number, errors: Record<string, string>, connectionFailed: boolean) => void) {}
  run<T>(resource: string, key: string, load: (signal: AbortSignal) => Promise<T>, publish: (data: T) => void) {
    const previous = this.active.get(resource);
    if (previous?.key === key) return previous.promise;
    previous?.controller.abort();
    const entry: Entry = { key, controller: new AbortController(), promise: Promise.resolve() };
    this.active.set(resource, entry);
    delete this.errors[resource];
    this.failed.delete(resource);
    this.notify();
    entry.promise = Promise.resolve().then(() => load(entry.controller.signal)).then(data => {
      if (this.active.get(resource) === entry && !entry.controller.signal.aborted) publish(data);
    }).catch(error => {
      if (this.active.get(resource) === entry && !entry.controller.signal.aborted && !isAbort(error)) {
        this.errors[resource] = error instanceof Error ? error.message : 'Falha na consulta';
        if(isTransientFailure(error))this.failed.set(resource,()=>this.run(resource,key,load,publish));
      }
    }).finally(() => {
      if (this.active.get(resource) === entry) { this.active.delete(resource); this.notify(); }
    });
    return entry.promise;
  }
  cancel(resource: string) {
    this.active.get(resource)?.controller.abort();
    this.active.delete(resource);
    delete this.errors[resource];
    this.failed.delete(resource);
    this.notify();
  }
  cancelAll() {
    for (const entry of this.active.values()) entry.controller.abort();
    this.active.clear();
    this.errors = {};
    this.failed.clear();
    this.notify();
  }
  retryFailed(){return Promise.all([...this.failed.values()].map(retry=>retry())).then(()=>undefined)}
  private notify() { this.changed(this.active.size, { ...this.errors },this.failed.size>0); }
}
