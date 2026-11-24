# Relatório de Auditoria Arquitetural de Qualidade, Testes e Confiabilidade (`TEST_AUDIT_REPORT.md`)

**Repositório:** `financial-twin` (`financial-app`)  
**Data da Auditoria e Remediação:** 24/11/2026  
**Baseline Auditado:** `v1.20.86` (`versionCode 132`, commit `0f0596d`)  
**Release Pós-Remediação:** `v1.20.87` (`versionCode 133`)  
**Status Final:** **CONCLUÍDO — 100% dos Gates, Suítes de Regressão e Verificadores Aprovados (`exit 0`)**

---

## 1. Fase 0 — Descoberta da Stack, Camadas, Gates e Contexto

### 1.1. Stack e Fronteiras de Integração Auditadas
| Camada | Tecnologias & Módulos | Fronteiras Críticas |
| :--- | :--- | :--- |
| **Backend API & Domínio (`backend/app/`)** | Python `3.12.14`, FastAPI `0.141.1`, Pydantic `2.13.5`, PyMongo `4.18.2`, PyMuPDF `1.28.2`, OpenPyXL `3.1.5` + `defusedxml 0.7.1`, Pillow `12.3.0`, HTTPX `0.28.1` | MongoDB Atlas v1.2 com `$jsonSchema` estrito (`additionalProperties: false` em `transactions`, `accounts`, `credit_card_bills`, `obligations`, `installment_plans`), coleções aditivas (`provisional_transactions`, `raw_events`, `commitments`, `future_schedule_overrides`, `transaction_field_overrides`), xAI (`grok-4.3` / `grok-voice-transcribe-2.0`), armazenamento SHA-256 (`MEDIA_STORAGE_PATH`) |
| **Cliente Mobile & Web (`mobile/`)** | Expo SDK `57`, React `19.2.3`, React Native `0.86.3`, Expo Router (`app/`), TypeScript `6.0.3`, `expo-sqlite`, `expo-secure-store`, `expo-local-authentication`, `expo-file-system`, `expo-sharing` | API REST autenticada via Bearer JWT + renovação deslizante (`X-Session-Token`), fila durável SQLite / Web Locks (`localStorage`), envelope biométrico AEAD `ChaCha20-Poly1305` (256 bits) |
| **Módulo Nativo Android (`mobile/modules/notification-capture/`)** | Kotlin `2.1.20` (`NotificationCaptureService.kt`, `NotificationInbox.kt`, `NotificationCaptureModule.kt`), Config Plugins (`withNotificationCapture.js`, `withPrivateStorage.js`) | `NotificationListenerService`, `SharedPreferences` (`inbox_v2` / `recovery_v2`), bridge Expo Modules, instalação verificada de pacotes (`ACTION_VIEW`) |
| **Operações, Migrações, Relay & CI/CD (`scripts/`, `backend/scripts/`, `backend/migrations/`, `.github/workflows/`)** | Python CLI & PowerShell (`validate.ps1`, `build-android.ps1`, `publish-release.ps1`, `proxy_to_vps.py`, `Build-OraclePackage.py`, `reconcile_official_statements.py`, `backup_restore.py`, `release_pointer.py`, `verify_apk_source.py`, `check_version_sync.py`) | Relay loopback->Tailscale TLS (`127.0.0.1:8000` -> `:8443`), containers endurecidos (`10001:10001`, `cap_drop: ALL`), assinatura APK RSA-4096 e verificação de proveniência do source-map |

---

## 2. Fase 1 — Auditoria de Validade e Remediações Realizadas na Suíte Existente

### 2.1. Problemas Identificados e Remediados na Suíte Existente
1. **Mock Drift de Schemas `$jsonSchema` no Backend (`[P0]` — Remediado)**:
   - *Diagnóstico*: 66 de 67 arquivos em `backend/tests/` usavam coleções falsas sem validação `$jsonSchema`, e `test_official_reconciler.py` definia `BILL_SCHEMA`/`OBL_SCHEMA` inline divergentes do Atlas real (`validators.v1_2.json`), ocultando 3 bugs críticos de produção (`BUG-01`, `BUG-02`, `BUG-03`).
   - *Remediação*: Extraído o catálogo canônico `backend/tests/fixtures/validators-v1.2.json` e criado `backend/tests/bson_schema_validator.py` (`validate_bson_document` + `ValidatingCollection` emitindo `WriteError(code=121)`), aplicado em `test_official_reconciler.py`, `test_commitment_profile.py` e `test_classification_transitions.py`.
2. **Fakes MongoDB Silenciosos / Incompletos (`[P1]` — Remediado)**:
   - *Diagnóstico*: `Collection._matches` em `test_ingestion.py` aceitava operadores desconhecidos (`$ne`, `$lt`, `$nin`, `$exists`, `$or`) retornando `True`; `update_one` não suportava `upsert=True`/`$setOnInsert` (forçando `patch.object` em `test_capture_currency.py` e `test_domain_ingestion_fixes.py`); `test_auth.py` usava lambda no-op para `db.account.update_one`.
   - *Remediação*: `Collection` atualizado com suporte real a operadores de consulta/ordenação/paginação e `upsert`; `test_auth.py` migrado para `_StatefulAccountCollection` validando substituição de `jti`, expiração e teto `MAX_SESSIONS`.
3. **Falsos Positivos e Tautologias (`[P0]`/`[P1]` — Remediados)**:
   - `test_commitment_profile.py`: corrigida tautologia literal `X == X` na linha 96 (`copy.deepcopy` prévio) e corrigido fixture `"status": "active"` -> `"ativo"` em `parcelamento` gerido para testar de fato `single_next_override` (`scope="next"`) e `scope="future"`.
   - `test_performance_plan.py`: corrigido enum inexistente `"rolling_average"` -> `"last_three_bills"`, exercitando `commitment_estimates.py:147-166`.
   - `test_architecture_failures.py`: expandido teste de paginação por cursor para percorrer Página 1 -> Página 2 -> Página 3 sem repetição ou omissão de âncoras.
   - `test-queue-finalization.cjs`: corrigida regex `/removeFile/` -> `/removeQueuedFile|removeFile|\.delete\(/`.
   - `test-adversarial-audit.cjs`: substituídos greps estáticos/ignorados no CI (`android/app/build.gradle` e `app/+native-intent.ts`) por execução real de `withNotificationCapture.js` (`compileModsAsync` com e sem keystore de release) e `redirectSystemPath`, além de sentinela `beforeExit`.
   - `audit-dependencies.cjs`: eliminada verdade vacua em `.every()` exigindo `findings.length > 0`.
   - `check_capture_batches.py`: substituída tautologia `check(..., True)` por requisição `DELETE` não autenticada verificando HTTP `401`.
4. **Testes Órfãos, Sandboxes VM Anônimos e Acoplamento Frágil (`[P0]`/`[P1]` — Remediados)**:
   - Criado `mobile/scripts/preload-vm-guard.cjs` (injetando `{ filename }` via proxy do TypeScript 6 em `vm.runInNewContext`, eliminando frames `evalmachine.<anonymous>` em 16 suítes e registrando handlers `unhandledRejection`/`uncaughtException` + `__expectAsyncCompletion`).
   - `mobile/scripts/run-client-tests.cjs` passou a carregar `preload-vm-guard.cjs` e executar oficialmente o teste antes órfão `mobile/utils/syncQueue.test.mjs`.
   - `backend/scripts/check_all.py` passou a despachar todas as **14** suítes de integração (incluindo `check_domain_account_fixes.py` e `check_domain_ledger_fixes.py`, corrigidas para respeitar `MONGODB_URI`, replica set sintético dinâmico e limpeza de bancos temporários).
   - `test-android-local-back.cjs` desacoplado do recorte textual frágil de `test-filter-navigation.cjs`.
   - `test-telemetry-headers.cjs` alinhado à assinatura de 3 parâmetros de `trackUserAction(..., isBlocked)` e `test-accessibility.cjs` expandido para varrer também `app/**/*.tsx` (`47` arquivos).
   - `test-notification-native.cjs` / `.ps1`: limpeza garantida de diretórios temporários (`finally`), resolução via `$env:JAVA_HOME`, suporte a `kotlinc` em Linux/macOS e compilação/execução direta de `NotificationCaptureModule.kt` com constantes Android reais (`"android.title"`, `SpannableStub`).

---

## 3. Fase 3 — Novos Testes `P0` e `P1` Implementados por Camada

| Camada | Arquivo Criado / Expandido | Escopo de Proteção Adicionado |
| :--- | :--- | :--- |
| **Backend (`P0`)** | `backend/tests/test_official_statement_parsers.py` | Geração sintética de PDFs multi-página (incluindo criptografia AES-256 e desbloqueio por CPF explícito, prefixo de 6 dígitos e descoberta em memória) cobrindo faturas e extratos Itaú e Santander, IOF internacional, `Principal + Juros`, estornos, trânsito técnico PIX cartão, transferências próprias, saldo anterior/pagamento parcial, deduplicação de janelas sobrepostas, correlação fatura-pagamento e parcelas multi-ano (`12/12`). |
| **Backend (`P0`)** | `backend/tests/test_official_reconciler.py` *(expandido)* + `test_bson_schema_validator.py` | Conformidade BSON completa contra `validators-v1.2.json`, reexecução idempotente (`already_reconciled`), tombstones (`deleted_movements` e capturas `cancelled`), divisão `principal_amount` + `fee_amount` (PIX no cartão) e atualização de faturas/parcelamentos existentes. |
| **Backend (`P0`)** | `backend/tests/test_account_auth_routes.py` | Cobertura ponta a ponta de `account_auth.py`: `/status`, `/setup`, `/session` (migração única de `app_password`, bloqueio 429 e reset), `/account` (`X-Session-Token`), `/biometric`, `PATCH /account` (409 + auditoria), `/password` (rotação + limpeza de sessões), `/sessions`, `/logout` e `/logout-all`. |
| **Backend (`P0`/`P1`)** | `backend/tests/test_atomic_and_pagination.py` | Cobertura integral de `atomic.py` (`_SessionDatabase`, `mongo_transaction`, `code=20 -> AtomicUnavailable`, limpeza de `_movement_insert_locked`), `movement_insert_lock.py` e `pagination.py` (múltiplas páginas, empates por `_id`, fallback `_EPOCH` e cursores adulterados `422`). |
| **Backend (`P1`)** | `backend/tests/test_insights_workflows.py` | Cobertura completa das rotas de `insights.py`: `/closure`, `/trail`, `/evidence/{id}/undo`, `/cases` + `refund_claims` + `/dossier`, `/guardian-detections` (4 anomalias) e `/experiments` + `/report`. |
| **Ops / Migrações / Release (`P0`/`P1`)** | `backend/tests/test_ops_migrations_and_release.py` + `scripts/check_version_sync.py` | **41 testes** cobrindo `proxy_to_vps.py` (half-close EOF, keep-alive, CORS `431`/`502`/preflight, guarda loopback), `reconcile_official_statements.py`, `Build-OraclePackage.py` (reprodutibilidade determinística SHA-256 e limpeza de falha), `verify_apk_source.py`, `release_pointer.py`, `publish-release.ps1` (compatibilidade UTF-8 sem BOM PS5/PS7), `check_version_sync.py`, `backup_restore.py`, `serve_test_database.py`, `release_check.py` e todas as 6 migrações em `backend/migrations/*.py`. |
| **Mobile (`P0`)** | `mobile/scripts/test-app-update-security.cjs` | Fluxo completo de `AppUpdate.tsx` (Android e Web): manifesto inválido, transporte privado HTTPS/loopback, bloqueio cross-origin, verificação SHA-256/tamanho com descarte de APK corrompido, handoff Android `ACTION_VIEW` (`content://`) e proibição de fallback não verificado. |
| **Mobile (`P0`)** | `mobile/scripts/test-biometric-crypto-and-auth.cjs` + `test-account-security.cjs` *(expandido)* | CSPRNG WebCrypto obrigatório, AEAD `ChaCha20-Poly1305` (>64B, UTF-8 multibyte/surrogates, rejeição de bit-flip em tag/nonce/ciphertext/AAD), rejeição de `cachedEnvelope` corrompido, recuperação `KeyPermanentlyInvalidated`, preservação de CPF de PDF ao desativar biometria e limpeza garantida em `logout()` offline. |
| **Mobile (`P1`)** | `mobile/scripts/test-share-and-routes.cjs` | Execução em runtime de `app/+native-intent.ts`, `app/handle-share.tsx`, `app/_layout.tsx`, `app/account.tsx`, `app/compromissos/mes/[month].tsx`, `app/index.tsx`, `app/login.tsx` e `modules/notification-capture/index.ts` (snapshot v2 + `exportRecovery`). |
| **Mobile (`P1`)** | `mobile/scripts/test-statement-link-and-telemetry.cjs` | Paginação de `StatementCommitmentLink.tsx` além de 200 compromissos (`offset=200`) + `swap`/`unlink`, `queueDb.web.tsx` (`perf_telemetry` com teto de 200 linhas), `telemetry.ts` (`parseServerMetrics`, `flush`, P50/P95) e `costControlInsights.ts`. |
| **Nativo Kotlin (`P1`)** | `mobile/scripts/fixtures/notification-inbox/InboxProbe.kt` *(56 -> 99 asserções)* | Recuperação automática de `NotificationInbox` após falha transitória de leitura/escrita, regex Itaú/débito/transferência/`last4`, pacotes bancários autorizados vs bloqueados, `SpannableStub`, corte `MAX_RECOVERY_ENTRIES` e métodos de `NotificationCaptureModule` (`isEnabled` anti-spoofing, `installApk`, `getInboxSnapshot`, `exportRecovery`). |

---

## 4. Fase 4 — Bugs Reais de Produção e Operação Encontrados e Corrigidos

*Nenhum teste foi enfraquecido; todos os 13 bugs abaixo foram corrigidos diretamente no código de produção/operação e cobertos por testes de regressão dedicados.*

| ID | Severidade | Componente Corrigido | Causa Raiz & Correção Aplicada |
| :--- | :---: | :--- | :--- |
| **BUG-01** | **`P0`** | `backend/app/official_reconciler.py` | `_sync_card_bill_and_obligation` omitia `mora`, `tarifas`, `seguros` e `outros_encargos` exigidos pelo validador real de `obligations` no Atlas, falhando em toda nova fatura. Campos preenchidos com `Decimal128("0.00")` e payloads alinhados estritamente ao `validators-v1.2.json`. |
| **BUG-02** | **`P0`** | `backend/app/commitment_profile.py` + `installment_labels.py` | `paint_movement` gravava campos proibidos (`vendedor`, `merchant_name`, `plataforma`, `card_id`, `card_label`), `account_id` em `str` e `metodo_pagamento` fora do enum em `db.transactions` (`WriteError 121`). Refatorado para resolver `merchant_id`, `platform_id` e `account_id` canônicos (`ObjectId`) e normalizar o enum de `metodo_pagamento`. |
| **BUG-03** | **`P0`** | `backend/app/main.py` + `platforms.py` | `_update_transaction_classification` gravava `user_cleared_fields` em `db.transactions` (`WriteError 121`) e tentava `delete_one` em `_SessionDatabase` abortada (`HTTP 500`). Movido o armazenamento de `user_cleared_fields` do ledger para a coleção aditiva `transaction_field_overrides` (lida em lote sem N+1) e protegido o rollback em sessão transacional. |
| **BUG-04** | **`P0`** | `mobile/components/AppUpdate.tsx` + `NotificationCaptureModule.kt` | Substituído `Sharing.shareAsync` (`ACTION_SEND`) por handoff nativo `installApk` (`Intent.ACTION_VIEW` com `content://` URI + `FLAG_GRANT_READ_URI_PERMISSION`), verificação SHA-256 em streaming (`createSha256Hasher`), download `{ idempotent: true }` e bloqueio de fallback não verificado `Linking.openURL` no Android. |
| **BUG-05** | **`P0`** | `mobile/utils/biometricSession.ts` | Removido fallback inseguro `Math.random()` em `randomBytes()` (exigindo CSPRNG `crypto.getRandomValues`) e bloqueado o fallback silencioso para token antigo quando `cachedEnvelope` (`'1:v2:...'`) apresenta falha de autenticação Poly1305/corrupção. |
| **BUG-06** | **`P0`** | `scripts/proxy_to_vps.py` | Corrigido fechamento prematuro do transporte SSL no EOF do cliente (`write_eof()` condicional sem fechar `remote_w` antes do fim da resposta), cancelamento limpo da leitura keep-alive após resposta upstream e inclusão de cabeçalhos CORS + JSON nas respostas `431`, `502` e preflight `OPTIONS`. |
| **BUG-07** | **`P0`** | `backend/migrations/*.py`, `backup_restore.py`, `serve_test_database.py`, `release_check.py` | Adicionados `sys.path` e modo dry-run padrão (`--apply` explícito) nas migrações; poda de órfãos em `backfill_descriptor_keys.py`; exclusão de itens `validado` em `repair_audit_2026_09_26.py`; validação regex de `sha256` e guarda `require_synthetic_target` em `backup_restore.py`/`serve_test_database.py`; leitura binária segura em `release_check.py`. |
| **BUG-08** | **`P1`** | `backend/app/official_statement_parsers.py` | `_infer_statement_item_date` corrigido para recuar `installment_number - 1` meses em compras parceladas multi-ano (`>=12x`); reconciliação matemática de faturas Itaú e Santander ajustada para suportar `Saldo anterior` rotativo e múltiplos pagamentos/antecipações no ciclo; `lines.index` protegido com `ValueError` descritivo. |
| **BUG-09** | **`P1`** | `backend/app/official_reconciler.py` + `reconcile_official_statements.py` | Removido catch-and-retry inválido na mesma `mongo_transaction`; incluída `review_queue` em `TOUCHED_COLLECTIONS` do backup pré-apply; rejeitado `--pdf` não-PDF sem cair em auto-discovery silencioso e busca `--pdf-dir` tornada case-insensitive. |
| **BUG-10** | **`P1`** | `NotificationInbox.kt` + `NotificationCaptureModule.kt` + `NotificationCaptureService.kt` | Limpeza automática de `failure` em `snapshot()` quando `read()` + `commit()` voltam a operar normalmente; verificação exata de `ComponentName` em `isListenerEnabled()` contra spoofing de prefixo; substituição de `Locale("pt","BR")` depreciado por `Locale.forLanguageTag("pt-BR")`. |
| **BUG-11** | **`P1`** | `AuthProvider.tsx`, `biometricSession.ts`, `+native-intent.ts`, `StatementCommitmentLink.tsx` | Preservação do CPF salvo de PDF ao alternar biometria (limpando-o apenas no `logout()`); limpeza de `__financialTwinPendingShare` ao receber rotas normais; paginação completa de `/v1/commitments` além de 200 itens em `StatementCommitmentLink.tsx`. |
| **BUG-12** | **`P1`** | `Build-OraclePackage.py`, `verify_apk_source.py`, `release_pointer.py`, `build-android.ps1` | Tar/gzip 100% determinístico (`mtime=0`), inclusão de scripts de recuperação/migração e remoção de arquivo parcial em erro; expansão de `REQUIRED_SOURCES` + bloqueio de fontes locais não rastreadas no bundle map; validação de sufixo `.manifest.json` e checagem de `$LASTEXITCODE` em `build-android.ps1`. |
| **BUG-13** | **`P1`** | `mobile/scripts/publish-release.ps1` | `Set-Content -Encoding utf8NoBOM` e `[IO.File]::Move($src,$dst,$true)` (.NET Core 3+/PS 7) falhavam no Windows PowerShell 5.1 (.NET Framework 4.8) durante `-StrictPublish`. Substituídos por `[System.IO.File]::WriteAllText(..., [System.Text.UTF8Encoding]::new($false))` e `Move-Item -LiteralPath ... -Force`. |

---

## 5. Comparativo Final de Gates e Cobertura (Antes vs. Depois)

### 5.1. Gates de Qualidade e Execução
| Gate / Verificação | Antes (Baseline) | Depois (Pós-Remediação) | Status Final |
| :--- | :---: | :---: | :---: |
| `unittest discover -s backend/tests` | 593 testes (`67` arquivos) | **670 testes (`74` arquivos)** | **`exit 0` (OK)** |
| `pip_audit -r backend/requirements.lock.txt --no-deps --disable-pip` | 0 vulnerabilidades | **0 vulnerabilidades** | **`exit 0` (OK)** |
| `node scripts/run-client-tests.cjs` (+ `syncQueue.test.mjs`) | 62 scripts (`.mjs` órfão) | **66 scripts + `syncQueue.test.mjs`** | **`exit 0` (OK)** |
| Probe Nativo Kotlin (`InboxProbeKt` + `NotificationCaptureModule.kt`) | 56 asserções (`Module` fora, 1 warning) | **99 asserções (`Module` incluso, 0 warnings)** | **`exit 0` (OK)** |
| `pnpm exec expo install --check` | OK | **OK** | **`exit 0` (OK)** |
| `node scripts/audit-dependencies.cjs` | OK | **OK (0 advisories runtime)** | **`exit 0` (OK)** |
| `pnpm exec tsc --noEmit` | 0 erros | **0 erros** | **`exit 0` (OK)** |
| `pnpm exec eslint App.tsx components utils app modules/notification-capture/index.ts` | 0 erros (`modules/` fora) | **0 erros (`modules/` incluso)** | **`exit 0` (OK)** |
| `python scripts/check_version_sync.py` + `release_pointer.py --check` | Inexistente no CI | **Ativo em `validate.ps1` e `quality.yml`** | **`exit 0` (OK)** |
| `backend/scripts/check_all.py` (Dispatcher de Integração) | 12 suítes (2 órfãs) | **14 suítes registradas e saneadas** | **Auditado & Alinhado** |

### 5.2. Cobertura por Camada e Módulos Críticos (Antes vs. Depois)
| Camada / Módulo Crítico | % Linhas (Antes) | % Linhas (Depois) | Δ Linhas | % Branches (Antes) | % Branches (Depois) | Δ Branches |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`backend/app/` (Camada Completa)** | 81,14% | **88,03%** | **+6,89 pp** | 54,50% | **58,74%** | **+4,24 pp** |
| ↳ `official_statement_parsers.py` | 20,18% | **92,88%** | **+72,70 pp** | 11,09% | **63,11%** | **+52,02 pp** |
| ↳ `pagination.py` | 27,59% | **100,00%** | **+72,41 pp** | 0,00% | **80,00%** | **+80,00 pp** |
| ↳ `account_auth.py` | 41,11% | **96,11%** | **+55,00 pp** | 8,06% | **69,35%** | **+61,29 pp** |
| ↳ `movement_insert_lock.py` | 35,71% | **85,71%** | **+50,00 pp** | 14,29% | **78,57%** | **+64,28 pp** |
| ↳ `atomic.py` | 58,73% | **100,00%** | **+41,27 pp** | 35,29% | **79,41%** | **+44,12 pp** |
| ↳ `insights.py` | 62,08% | **94,38%** | **+32,30 pp** | 31,75% | **56,19%** | **+24,44 pp** |
| ↳ `commitment_estimates.py` | 80,62% | **94,38%** | **+13,76 pp** | 58,72% | **70,35%** | **+11,63 pp** |
| ↳ `official_reconciler.py` | 83,82% | **88,38%** | **+4,56 pp** | 52,01% | **54,73%** | **+2,72 pp** |
| **`scripts/` (Scripts Python Raiz)** | 33,21% | **81,46%** | **+48,25 pp** | 27,56% | **55,20%** | **+27,64 pp** |
| ↳ `proxy_to_vps.py` | 0,00% | **82,94%** | **+82,94 pp** | 0,00% | **50,00%** | **+50,00 pp** |
| ↳ `Build-OraclePackage.py` | 0,00% | **85,96%** | **+85,96 pp** | 0,00% | **47,73%** | **+47,73 pp** |
| **`backend/migrations/`** | 0,00% | **52,93%** | **+52,93 pp** | 0,00% | **32,02%** | **+32,02 pp** |
| **`mobile/` (Camada Completa TS/TSX)** | 81,96% (`101` arq.) | **86,84% (`113` arq.)** | **+4,88 pp** | 56,80% | **59,44%** | **+2,64 pp** |
| ↳ `utils/biometricSession.ts` | 25,52% | **94,32%** | **+68,80 pp** | 0,00% | **72,65%** | **+72,65 pp** |
| ↳ `components/AppUpdate.tsx` | 60,47% | **81,87%** | **+21,40 pp** | 16,13% | **63,64%** | **+47,51 pp** |
| ↳ `components/AuthProvider.tsx` | 81,57% | **94,27%** | **+12,70 pp** | 66,67% | **77,84%** | **+11,17 pp** |
| ↳ `modules/notification-capture/index.ts` | 0,00% | **100,00%** | **+100,00 pp** | 0,00% | **93,02%** | **+93,02 pp** |
| ↳ `app/+native-intent.ts` | 0,00% | **100,00%** | **+100,00 pp** | 0,00% | **85,71%** | **+85,71 pp** |
| ↳ `app/handle-share.tsx` | 0,00% | **88,46%** | **+88,46 pp** | 0,00% | **71,43%** | **+71,43 pp** |
| ↳ `utils/costControlInsights.ts` | 83,00% | **100,00%** | **+17,00 pp** | 13,10% | **41,27%** | **+28,17 pp** |
| ↳ `utils/telemetry.ts` | 79,95% | **93,07%** | **+13,12 pp** | 47,37% | **62,39%** | **+15,02 pp** |
