export class AuthState {
  private token = '';
  private expiresAt = 0;
  private revision = 0;
  private sessionGeneration = 0;
  private suspended = false;
  private listeners = new Set<() => void>();
  subscribe = (listener: () => void) => {this.listeners.add(listener); return () => {this.listeners.delete(listener);};};
  snapshot = () => this.revision;
  sessionIdentity = () => this.sessionGeneration;
  getToken = () => {
    if (this.token && this.expiresAt <= Date.now()) this.lock();
    return this.suspended ? '' : this.token;
  };
  expiry = () => this.expiresAt;
  unlocked = () => !!this.token && this.expiresAt > Date.now();
  activate(token: string, expiresAt: number) {
    if (!token || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw new Error('Sessão expirada. Entre com sua senha.');
    this.sessionGeneration++; this.token = token; this.expiresAt = expiresAt; this.suspended = false; this.changed();
  }
  lock = () => {this.sessionGeneration++; this.token = ''; this.expiresAt = 0; this.suspended = false; this.changed();};
  suspend = () => {if (!this.suspended) {this.suspended = true; this.changed();}};
  resume = () => {if (this.suspended) {this.suspended = false; this.changed();}};
  // Native dialog/form callbacks can precede Android's focus event. Admit new
  // work only after that same session resumes; never replay it after locking.
  awaitSession = (signal?: AbortSignal): Promise<void> | null => {
    if (!this.unlocked() || !this.suspended) return null;
    const generation = this.sessionGeneration;
    return new Promise((resolve, reject) => {
      let done = false;
      let unsubscribe = () => {};
      const finish = (error?: Error) => {
        if (done) return;
        done = true; clearTimeout(timer); unsubscribe(); signal?.removeEventListener('abort', cancel);
        if (error) reject(error); else resolve();
      };
      const cancel = () => finish(Object.assign(new Error('Consulta cancelada'), {name: 'AbortError'}));
      const check = () => {
        if (generation !== this.sessionGeneration || !this.unlocked()) cancel();
        else if (!this.suspended) finish();
      };
      const timer = setTimeout(() => {
        if (!this.unlocked()) cancel();
        else finish(new Error('Volte ao aplicativo e tente novamente.'));
      }, Math.min(30_000, this.expiresAt - Date.now()));
      unsubscribe = this.subscribe(check);
      signal?.addEventListener('abort', cancel, {once: true});
      if (signal?.aborted) cancel(); else check();
    });
  };
  private changed() {this.revision++; this.listeners.forEach(listener => listener());}
}

export function requirePrivateTransport(url: string) {
  let parsed: URL;
  try {parsed = new URL(url);} catch {throw new Error('Configure a conexão do aplicativo no servidor.');}
  if (parsed.username || parsed.password || (parsed.protocol !== 'https:' &&
      !(parsed.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)))) {
    throw new Error('Para proteger sua senha e seus dados, a conexão precisa usar HTTPS.');
  }
}
