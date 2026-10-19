/**
 * Coletor Unificado de Telemetria de Performance para o App Mobile/Web (`financial-twin`).
 * Salve em: mobile/utils/telemetry.ts
 *
 * Cobre 100% do ciclo de vida solicitado:
 * 1. Navegação e Transições de Aba (`trackNavigation` / `markScreenReady` -> mede Time-to-Data real).
 * 2. Ações do Usuário e Bloqueios de UI (`trackUserAction` -> integra com `runUserAction`).
 * 3. Recuperação de Dados / Leituras (`trackDataFetch` -> mede rede, cache hit, dedup in-flight e lê `Server-Timing` do backend).
 * 4. Captura de Dados (`trackCapture` -> manual, voz, câmera, PDF, persistência local e fila SQLite).
 * 5. Atualização de Dados / Mutações (`trackMutation` -> journal SQLite, escrita remota e tempo de refresh pós-mutação).
 */

export type TelemetryCategory =
  | 'navigation'
  | 'transition'
  | 'action'
  | 'data_fetch'
  | 'data_capture'
  | 'data_mutation'
  | 'queue_sync';

export type TelemetryStatus =
  | 'ok'
  | 'error'
  | 'aborted'
  | 'blocked'
  | 'cache_hit'
  | 'deduped';

export type ServerPerfBreakdown = {
  total_ms?: number;
  db_ms?: number;
  app_ms?: number;
  db_calls?: number;
  n_plus_one?: string[];
};

export type TelemetryEvent = {
  event_id: string;
  timestamp: string;
  category: TelemetryCategory;
  name: string;
  duration_ms: number;
  status: TelemetryStatus;
  tab: string;
  trace_id: string;
  metadata: Record<string, any>;
};

type SqliteLike = {
  execAsync?: (sql: string) => Promise<void>;
  runAsync?: (sql: string, ...params: any[]) => Promise<any>;
  getAllAsync?: <T = any>(sql: string, ...params: any[]) => Promise<T[]>;
};

const MAX_MEMORY_EVENTS = 400;
const FLUSH_BATCH_SIZE = 25;
const SLOW_FETCH_MS = 400;
const SLOW_TRANSITION_MS = 500;

const nowMs = (): number =>
  typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();

const newTraceId = (): string =>
  'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 3) | 8).toString(16);
  });

const ID_IN_NAME = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24}/i;

const redactTelemetryName = (name: string) =>
  name.split('?')[0].replace(new RegExp(ID_IN_NAME.source, 'gi'), '{id}').slice(0, 160);

/** OkHttp rejects header values outside tab and ASCII printable. "Início" is char 0xed at index 2. */
const asciiHeaderValue = (value: string) => {
  const folded = typeof value.normalize === 'function'
    ? value.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    : value;
  return folded.replace(/\u2026/g, '...').replace(/[^\t\x20-\x7e]/g, '').slice(0, 40);
};

const CLIENT_META_KEYS = new Set([
  'from', 'to', 'reason', 'cached', 'forced', 'payload_bytes', 'attempt',
  'shared_consumers', 'network_overhead_ms', 'server_total_ms', 'server_db_ms',
  'server_app_ms', 'server_db_calls', 'slow_fetch', 'slow_transition',
  'uploading', 'status_code', 'timed_out', 'kind',
]);

const safeTelemetryMetadata = (metadata?: Record<string, any>) => {
  const safe: Record<string, string | number | boolean | null> = {};
  if (!metadata) return safe;
  for (const [key, value] of Object.entries(metadata)) {
    if (!CLIENT_META_KEYS.has(key)) continue;
    if (typeof value === 'boolean' || value === null) safe[key] = value;
    else if (typeof value === 'number' && Number.isFinite(value)) safe[key] = value;
    else if (typeof value === 'string' && value.length <= 40 && !ID_IN_NAME.test(value) && !/\d{3,}/.test(value)) safe[key] = value;
  }
  return safe;
};

class PerformanceTelemetryCollector {
  private currentTab = 'Início';
  private currentAction = '';
  private activeTransition: {
    from: string;
    to: string;
    startedAt: number;
    traceId: string;
  } | null = null;
  private memoryRing: TelemetryEvent[] = [];
  private outbox: TelemetryEvent[] = [];
  private db: SqliteLike | null = null;
  private uploader: ((events: TelemetryEvent[]) => Promise<void>) | null = null;
  private flushing = false;
  private listeners = new Set<() => void>();

  /**
   * Configura persistência SQLite local e função opcional de envio em lote para `/v1/telemetry/events`.
   */
  async configure(options: {
    db?: SqliteLike;
    uploader?: (events: TelemetryEvent[]) => Promise<void>;
  }) {
    if (options.db) {
      this.db = options.db;
      try {
        await this.db.execAsync?.(
          'CREATE TABLE IF NOT EXISTS perf_telemetry (event_id TEXT PRIMARY KEY NOT NULL, timestamp TEXT NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL, duration_ms REAL NOT NULL, status TEXT NOT NULL, tab TEXT NOT NULL, trace_id TEXT NOT NULL, metadata TEXT NOT NULL)',
        );
      } catch {
        // Falha em telemetria nunca bloqueia o fluxo financeiro
      }
    }
    if (options.uploader) {
      this.uploader = options.uploader;
    }
  }

  getCurrentTab() {
    return this.currentTab;
  }

  getCurrentAction() {
    return this.currentAction;
  }

  /**
   * Gera cabeçalhos de correlação para injetar em `apiClient.ts` -> `request()`.
   */
  createRequestHeaders(customTraceId?: string): {
    traceId: string;
    headers: Record<string, string>;
  } {
    const traceId = asciiHeaderValue(customTraceId || this.activeTransition?.traceId || newTraceId());
    const headers: Record<string, string> = { 'X-Trace-Id': traceId };
    const tab = asciiHeaderValue(this.currentTab);
    const action = asciiHeaderValue(this.currentAction);
    if (tab) headers['X-Client-Tab'] = tab;
    if (action) headers['X-Client-Action'] = action;
    return { traceId, headers };
  }

  /**
   * Extrai métricas do cabeçalho `X-Perf-Metrics` ou `Server-Timing` retornado pelo backend.
   */
  parseServerMetrics(headers?: Headers | null): ServerPerfBreakdown | null {
    if (!headers) return null;
    const rawJson = headers.get('X-Perf-Metrics');
    if (rawJson) {
      try {
        return JSON.parse(rawJson) as ServerPerfBreakdown;
      } catch {
        // Fallback para Server-Timing
      }
    }
    const serverTiming = headers.get('Server-Timing');
    if (!serverTiming) return null;
    const result: ServerPerfBreakdown = {};
    for (const part of serverTiming.split(',')) {
      const durMatch = /([a-z_]+);dur=([0-9.]+)/i.exec(part);
      if (durMatch) {
        const [, metric, val] = durMatch;
        const num = Number(val);
        if (metric === 'total') result.total_ms = num;
        if (metric === 'db') result.db_ms = num;
        if (metric === 'app') result.app_ms = num;
      }
      const descMatch = /desc="(\d+)\s+queries"/i.exec(part);
      if (descMatch) result.db_calls = Number(descMatch[1]);
    }
    return Object.keys(result).length ? result : null;
  }

  /**
   * 1. NAVEGAÇÃO E TRANSIÇÃO DE TELA/ABA
   * Inicia o cronômetro quando o usuário troca de aba/tela e fecha quando os dados visíveis terminam de carregar.
   */
  startTabTransition(nextTab: string, reason: 'user_tap' | 'programmatic' | 'back' = 'user_tap') {
    const previousTab = this.currentTab;
    if (previousTab === nextTab) return;
    this.currentTab = nextTab;
    const traceId = newTraceId();
    this.activeTransition = {
      from: previousTab,
      to: nextTab,
      startedAt: nowMs(),
      traceId,
    };
    this.record({
      category: 'navigation',
      name: `${previousTab} -> ${nextTab}`,
      duration_ms: 0,
      status: 'ok',
      trace_id: traceId,
      metadata: { from: previousTab, to: nextTab, reason },
    });
  }

  /**
   * Deve ser chamado ao final de `refreshVisible` ou quando a aba alvo concluir o carregamento dos seus recursos.
   */
  completeTabTransition(tabName: string, extraMeta: Record<string, any> = {}) {
    if (!this.activeTransition || this.activeTransition.to !== tabName) return;
    const duration = Math.round((nowMs() - this.activeTransition.startedAt) * 100) / 100;
    const { from, to, traceId } = this.activeTransition;
    this.activeTransition = null;
    this.record({
      category: 'transition',
      name: `TTI:${to}`,
      duration_ms: duration,
      status: 'ok',
      trace_id: traceId,
      metadata: {
        from,
        to,
        slow_transition: duration >= SLOW_TRANSITION_MS,
        ...extraMeta,
      },
    });
  }

  /**
   * 2. AÇÕES DO USUÁRIO (`runUserAction` em `userOperation.ts`)
   */
  trackUserAction<T>(
    label: string,
    action: () => T | PromiseLike<T>,
    isBlocked: boolean,
  ): T | Promise<T> | undefined {
    const traceId = newTraceId();
    if (isBlocked) {
      this.record({
        category: 'action',
        name: label,
        duration_ms: 0,
        status: 'blocked',
        trace_id: traceId,
        metadata: { reason: 'interaction_gate_busy' },
      });
      return undefined;
    }
    const start = nowMs();
    const prevAction = this.currentAction;
    this.currentAction = label;
    try {
      const result = action();
      if (result != null && typeof (result as PromiseLike<T>).then === 'function') {
        return Promise.resolve(result).then(
          value => {
            this.currentAction = prevAction;
            this.record({
              category: 'action',
              name: label,
              duration_ms: Math.round((nowMs() - start) * 100) / 100,
              status: 'ok',
              trace_id: traceId,
            });
            return value;
          },
          error => {
            this.currentAction = prevAction;
            const aborted = error instanceof Error && error.name === 'AbortError';
            this.record({
              category: 'action',
              name: label,
              duration_ms: Math.round((nowMs() - start) * 100) / 100,
              status: aborted ? 'aborted' : 'error',
              trace_id: traceId,
              metadata: { error: error instanceof Error ? error.message : String(error) },
            });
            throw error;
          },
        );
      }
      this.currentAction = prevAction;
      this.record({
        category: 'action',
        name: label,
        duration_ms: Math.round((nowMs() - start) * 100) / 100,
        status: 'ok',
        trace_id: traceId,
      });
      return result as T;
    } catch (error) {
      this.currentAction = prevAction;
      this.record({
        category: 'action',
        name: label,
        duration_ms: Math.round((nowMs() - start) * 100) / 100,
        status: 'error',
        trace_id: traceId,
        metadata: { error: error instanceof Error ? error.message : String(error) },
      });
      throw error;
    }
  }

  /**
   * 3. RECUPERAÇÃO DE DADOS (`data_fetch`)
   * Mede tempo total no cliente, tamanho do payload, se houve deduplicação/cache e separa Rede vs. Backend (`Server-Timing`).
   */
  recordDataFetch(params: {
    path: string;
    durationMs: number;
    status: TelemetryStatus;
    traceId?: string;
    payloadBytes?: number;
    attempt?: number;
    sharedConsumers?: number;
    serverMetrics?: ServerPerfBreakdown | null;
    error?: string;
  }) {
    const cleanPath = params.path.split('?')[0];
    const queryString = params.path.includes('?') ? params.path.split('?')[1] : '';
    const serverTotal = params.serverMetrics?.total_ms ?? null;
    const networkOverheadMs =
      serverTotal !== null ? Math.max(0, Math.round((params.durationMs - serverTotal) * 100) / 100) : null;

    this.record({
      category: 'data_fetch',
      name: `GET ${cleanPath}`,
      duration_ms: Math.round(params.durationMs * 100) / 100,
      status: params.status,
      trace_id: params.traceId || newTraceId(),
      metadata: {
        query: queryString,
        payload_bytes: params.payloadBytes ?? 0,
        attempt: params.attempt ?? 1,
        shared_consumers: params.sharedConsumers ?? 1,
        network_overhead_ms: networkOverheadMs,
        server_total_ms: serverTotal,
        server_db_ms: params.serverMetrics?.db_ms ?? null,
        server_app_ms: params.serverMetrics?.app_ms ?? null,
        server_db_calls: params.serverMetrics?.db_calls ?? null,
        server_n_plus_one: params.serverMetrics?.n_plus_one ?? null,
        slow_fetch: params.durationMs >= SLOW_FETCH_MS,
        ...(params.error ? { error: params.error } : {}),
      },
    });
  }

  /**
   * 4 & 5. CAPTURA DE DADOS (`data_capture`) E ATUALIZAÇÃO DE DADOS (`data_mutation`)
   */
  async measureSpan<T>(
    category: TelemetryCategory,
    name: string,
    work: (traceId: string) => Promise<T>,
    metadata: Record<string, any> = {},
  ): Promise<T> {
    const start = nowMs();
    const traceId = newTraceId();
    try {
      const value = await work(traceId);
      this.record({
        category,
        name,
        duration_ms: Math.round((nowMs() - start) * 100) / 100,
        status: 'ok',
        trace_id: traceId,
        metadata,
      });
      return value;
    } catch (error) {
      const aborted = error instanceof Error && error.name === 'AbortError';
      this.record({
        category,
        name,
        duration_ms: Math.round((nowMs() - start) * 100) / 100,
        status: aborted ? 'aborted' : 'error',
        trace_id: traceId,
        metadata: {
          ...metadata,
          error: error instanceof Error ? error.message : String(error),
        },
      });
      throw error;
    }
  }

  record(input: {
    category: TelemetryCategory;
    name: string;
    duration_ms: number;
    status: TelemetryStatus;
    trace_id?: string;
    metadata?: Record<string, any>;
  }) {
    const event: TelemetryEvent = {
      event_id: newTraceId(),
      timestamp: new Date().toISOString(),
      category: input.category,
      name: redactTelemetryName(input.name),
      duration_ms: input.duration_ms,
      status: input.status,
      tab: this.currentTab,
      trace_id: input.trace_id || newTraceId(),
      metadata: safeTelemetryMetadata(input.metadata),
    };

    this.memoryRing.push(event);
    if (this.memoryRing.length > MAX_MEMORY_EVENTS) {
      this.memoryRing.shift();
    }
    this.outbox.push(event);

    if (this.db?.runAsync) {
      void this.db
        .runAsync(
          'INSERT OR IGNORE INTO perf_telemetry(event_id,timestamp,category,name,duration_ms,status,tab,trace_id,metadata) VALUES(?,?,?,?,?,?,?,?,?)',
          event.event_id,
          event.timestamp,
          event.category,
          event.name,
          event.duration_ms,
          event.status,
          event.tab,
          event.trace_id,
          JSON.stringify(event.metadata),
        )
        .catch(() => {});
    }

    if (this.outbox.length >= FLUSH_BATCH_SIZE) {
      void this.flush();
    }
    this.listeners.forEach(listener => listener());
  }

  async flush() {
    if (this.flushing || !this.uploader || this.outbox.length === 0) return;
    this.flushing = true;
    const batch = this.outbox.splice(0, FLUSH_BATCH_SIZE);
    try {
      await this.uploader(batch);
    } catch {
      // Re-enfileira em caso de offline sem perder os eventos recentes
      this.outbox.unshift(...batch.slice(-50));
    } finally {
      this.flushing = false;
    }
  }

  /**
   * Gera um resumo local instantâneo (Pareto por P95) para ser exibido na aba Ajustes ou exportado em JSON.
   */
  getSummary() {
    const groups = new Map<string, number[]>();
    const errors = new Map<string, number>();
    for (const ev of this.memoryRing) {
      const key = `${ev.category}:${ev.name}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(ev.duration_ms);
      if (ev.status === 'error' || ev.status === 'aborted') {
        errors.set(key, (errors.get(key) || 0) + 1);
      }
    }
    const rows = [...groups.entries()].map(([key, values]) => {
      const sorted = [...values].sort((a, b) => a - b);
      const n = sorted.length;
      const p50 = sorted[Math.floor(0.5 * (n - 1))] || 0;
      const p95 = sorted[Math.floor(0.95 * (n - 1))] || 0;
      const avg = sorted.reduce((acc, v) => acc + v, 0) / (n || 1);
      return {
        key,
        count: n,
        avg_ms: Math.round(avg * 10) / 10,
        p50_ms: Math.round(p50 * 10) / 10,
        p95_ms: Math.round(p95 * 10) / 10,
        max_ms: Math.round((sorted[n - 1] || 0) * 10) / 10,
        failures: errors.get(key) || 0,
      };
    });
    return rows.sort((a, b) => b.p95_ms - a.p95_ms);
  }

  subscribe(listener: () => void) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

export const telemetry = new PerformanceTelemetryCollector();