# Diagnóstico de Gargalos e Guia de Instrumentação (`financial-twin`)

Este guia consolida o **diagnóstico técnico dos gargalos reais encontrados no código do `financial-twin`**, o **passo a passo de acoplamento dos coletores de telemetria** (`backend/app/telemetry.py` e `mobile/utils/telemetry.ts`) nos 5 pontos centrais da arquitetura, e o **plano de ação (Kaizen)** para eliminar a lentidão nas recuperações de dados.

## 1. Onde estão os gargalos atuais nas recuperações de dados

Ao auditar o código do `backend` e do `mobile`, identificamos **7 causas raízes** que explicam a lentidão percebida nas consultas e transições de tela:

| Gargalo Identificado | Arquivo e Linhas | Impacto Real na Performance | Correção Recomendada |
| :--- | :--- | :--- | :--- |
| **1. `COLLSCAN` por falta de índice em `competencia`** | `backend/app/dashboard_window.py` (L13–28) e `backend/app/economic_period.py` (L39) | `snapshot_for()` executa `find_one({'competencia': {'$regex': ...}}, sort=[('competencia', -1)])` e uma query `$or` combinando `data_evento` e `competencia`. Como `competencia` não possui índice em `transactions` nem em `provisional_transactions`, o MongoDB faz **scan completo da coleção** em todo carregamento da aba **Início**. | Criar índice composto `[("competencia", -1), ("data_evento", -1)]` em `transactions` e `[("status", 1), ("competencia", -1), ("data_evento", -1)]` em `provisional_transactions`, além de substituir `$regex` por faixa de strings (`"1900-01"` a `"2200-12"`). |
| **2. Disparo duplo e cascata na aba `Compromissos`** | `mobile/components/CommitmentManager.tsx` (L74, L80–84) e `CommitmentProjection.tsx` (L56–68) | Ao abrir **Compromissos**, o app dispara 6 requisições pesadas simultâneas (`/v1/accounts`, `/v1/account-positions`, `/v1/commitments`, `/v1/installments`, `/v1/future-commitments/obligations` e `/v1/commitment-projection`). Quando `loadCommitmentLists` termina, a linha 74 chama `setProjectionVersion(v => v + 1)`, forçando o `CommitmentProjection` a **disparar `/v1/commitment-projection` uma segunda vez** em seguida. | Remover o incremento de `projectionVersion` no `load` inicial (manter apenas em `onUpdated` após mutações) ou consolidar listas + projeção em um único endpoint `/v1/commitments/overview`. |
| **3. N+1 Queries em `with_merchant_name` (singular)** | `backend/app/main.py` (L277–300, L2520, L2593) | Diferente de `with_merchant_names` (plural), a versão singular consulta `db.merchants.find_one` (até 2x), chama `taxonomy_overrides(db)` (que lê `db.subcategories.find({})` inteiro sem cache) e reconstrói `historical_capture_profile` a cada linha. É chamada dentro de loops em `/v1/commitments/{id}/payment-candidates` e `/v1/statement-search`. | Cachear `taxonomy_overrides(db)` no `classification_cache` e substituir chamadas em loop de `with_merchant_name` pelo enriquecimento em lote `with_merchant_names`. |
| **4. Leitura integral da coleção em `_statement_page`, `spending_only` e `account_positions`** | `backend/app/main.py` (L1391–1392, L2639–2640) e `backend/app/account_positions.py` (L56–58) | `_statement_page` (`GET /v1/statement-search`) e `cursor_movements(..., spending_only=True)` carregam **todo o extrato** em memória Python (`list(db.transactions.find(...))`) antes de filtrar e paginar. Em `account_positions()`, todas as capturas confirmadas são lidas sem filtro de data. | Empurrar filtros de texto/valor para o MongoDB (usando índice de texto ou regex ancorado + projeção enxuta) e filtrar `provisional_transactions` pela data de corte também. |
| **5. `historical_statement_reviews` na revisão de registros** | `backend/app/record_review.py` (L14–91) e `backend/app/main.py` (L1368) | Toda página de `/v1/review-records` varre todas as capturas sem `status_revisao`, carrega lotes de `capture_batches`, varre `audit_log` (`statement_batch_committed`) e faz `db.accounts.find_one` em loop. | Persistir `status_revisao: "validado"` nas capturas antigas via migração única ou cachear o dicionário de lotes auditados em `ReferenceCache`. |
| **6. Overhead de `mongo_snapshot` em todas as leituras** | `backend/app/atomic.py` (L62–74) | Cada `GET` abre uma sessão e inicia uma transação multi-documento (`start_transaction(read_concern=ReadConcern("snapshot"))`). Em conexões remotas (Tailscale / Oracle / Atlas), isso adiciona round-trips extras e contenção de sessão quando o app dispara 5–6 `GET`s paralelos. | Usar `mongo_snapshot` apenas em endpoints compostos de fechamento/auditoria (`/v1/dashboard/overview`, `/v1/month-close`, `/v1/insights/closure`) e leitura direta (`readConcern="majority"`) para listagens simples paginadas. |
| **7. Invalidação global agressiva de caches após qualquer escrita** | `mobile/utils/apiClient.ts` (L49–54, L196) e `backend/app/main.py` (L207–219) | Qualquer `POST/PATCH/PUT` aborta todas as leituras em andamento no app e invalida `classification_cache` + `historical_context_cache` no backend, obrigando a próxima leitura a rodar mais de 15 comandos `.distinct()` e varrer 1.600 documentos em `platforms.py`. | Invalidar `classification_cache` apenas quando a mutação realmente alterar categorias, subcategorias ou criar novo fornecedor/plataforma. |

## 2. Como acoplar os coletores em 5 pontos únicos (Zero poluição de código)

A arquitetura do `financial-twin` já centraliza rede, ações, consultas e sincronização. Por isso, **não é necessário espalhar `console.log` pelas 35 telas/componentes**. Basta conectar os dois módulos entregues (`backend/app/telemetry.py` e `mobile/utils/telemetry.ts`) nos 5 pontos abaixo:

### Ponto 1 — Backend: `backend/app/main.py`

Logo após criar a instância `app = FastAPI(...)` (por volta da linha 151) e no `CORS`:

```python
from app.telemetry import install_telemetry, SpanTimer

app = FastAPI(title="Financial Digital Twin API", version="0.4.0", lifespan=lifespan)
install_telemetry(app)
```

E no `CORSMiddleware` (linha 194), inclua os cabeçalhos de correlação e métricas:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:19006", "http://127.0.0.1:19006"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Trace-Id", "X-Client-Action", "X-Client-Tab"],
    expose_headers=[
        "X-Operation-Pending",
        "Retry-After",
        "X-Session-Token",
        "X-Session-Expires-In",
        "X-Trace-Id",
        "Server-Timing",
        "X-Perf-Metrics",
    ],
)
```

### Ponto 2 — Mobile Rede (Leituras e Atualizações): `mobile/utils/apiClient.ts`

Em `request()` (linha 64), injete os cabeçalhos do `telemetry` e registre o tempo de rede vs. servidor:

```typescript
import { telemetry } from './telemetry';

// Dentro de request(path, init, timeoutMs, uploading):
const startedAt = performance.now();
const { traceId, headers: traceHeaders } = telemetry.createRequestHeaders();
const headers = new Headers(init.headers);
Object.entries(traceHeaders).forEach(([k, v]) => headers.set(k, v));
// ... após `const response = await transport(...)` e `const text = await response.text()`:
const serverMetrics = telemetry.parseServerMetrics(response.headers);
const durationMs = performance.now() - startedAt;
if (!init.method || init.method.toUpperCase() === 'GET') {
  telemetry.recordDataFetch({
    path,
    durationMs,
    status: response.ok ? 'ok' : 'error',
    traceId,
    payloadBytes: text.length,
    serverMetrics,
  });
} else {
  telemetry.record({
    category: 'data_mutation',
    name: `${init.method.toUpperCase()} ${path.split('?')[0]}`,
    duration_ms: Math.round(durationMs * 100) / 100,
    status: response.ok ? 'ok' : 'error',
    trace_id: traceId,
    metadata: { payload_bytes: text.length, server: serverMetrics },
  });
}
```

### Ponto 3 — Mobile Ações e Bloqueios de UI: `mobile/utils/userOperation.ts`

Em `runUserAction` (linha 51), envolva a execução para registrar automaticamente toda ação de usuário e eventuais cliques descartados por tela ocupada (`status: 'blocked'`):

```typescript
import { telemetry } from './telemetry';

export function runUserAction<T>(
  action: () => T | PromiseLike<T>,
  label = 'Processando…',
  onError?: (error: unknown) => void,
): T | Promise<T | undefined> | undefined {
  if (isUserInteractionBlocked()) {
    telemetry.trackUserAction(label, () => undefined, true);
    return;
  }
  return telemetry.trackUserAction(label, () => {
    // corpo original do runUserAction
  }, false);
}
```

### Ponto 4 — Mobile Navegação, Transições (TTI) e Capturas: `mobile/App.tsx`

1. **Configuração inicial** (junto ao `useEffect` do SQLite na linha 296):
   ```typescript
   useEffect(() => {
     api.configureMutationStore(db);
     void telemetry.configure({
       db,
       uploader: events => json('/v1/telemetry/events', { method: 'POST', body: JSON.stringify({ events }) }),
     });
   }, [db]);
   ```
2. **Transição de Abas (`setTab`)**: Ao trocar de aba, inicie o cronômetro de transição e feche-o quando `refreshVisible` concluir:
   ```typescript
   const navigateTab = useCallback((nextTab: Tab, reason: 'user_tap' | 'programmatic' | 'back' = 'user_tap') => {
     telemetry.startTabTransition(nextTab, reason);
     setTab(nextTab);
   }, []);
   ```
   E no final de `refreshVisible` (linha 563):
   ```typescript
   return promise.finally(() => telemetry.completeTabTransition(tab));
   ```
3. **Captura de Dados (`runCaptureTask` na linha 689)**:
   ```typescript
   const runCaptureTask = async (task: () => Promise<void>, captureKindLabel = 'capture') =>
     telemetry.measureSpan('data_capture', captureKindLabel, async () => {
       // corpo original do runCaptureTask
     });
   ```

## 3. Script de Índices para eliminar os `COLLSCAN`s imediatos

Adicione estes índices em `backend/app/main.py` (dentro de `lifespan`) e em `backend/scripts/audit_performance_indexes.py`:

```python
# Índices essenciais para eliminar COLLSCAN em snapshot_for, economic_rows, review_records e commitments:
app.state.db.transactions.create_index(
    [("competencia", -1), ("data_evento", -1), ("_id", -1)],
    name="transaction_competence_window",
)
app.state.db.transactions.create_index(
    [("status_revisao", 1), ("data_evento", -1), ("_id", -1)],
    name="transaction_review_status_page",
)
app.state.db.provisional_transactions.create_index(
    [("status", 1), ("competencia", -1), ("data_evento", -1)],
    name="provisional_competence_window",
)
app.state.db.provisional_transactions.create_index(
    [("event_id", 1)],
    name="provisional_by_event_id",
)
app.state.db.transaction_evidence.create_index(
    [("transaction_id", 1), ("status", 1)],
    name="evidence_by_transaction",
)
app.state.db.audit_log.create_index(
    [("event", 1), ("transaction_id", 1), ("created_at", -1)],
    name="audit_reviewed_labels",
)
```
