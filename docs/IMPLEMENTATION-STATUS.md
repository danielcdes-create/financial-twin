# Estado de execução da NT-001

Atualizado em 01/10/2026. Requisitos: [NT-001](NT-001-CONFIABILIDADE-E-EVOLUCAO.md). Procedimentos e contratos: [runbook](NT-001-RUNBOOK-EXECUTAVEL.md).

## Correções da auditoria adversarial

Os 15 achados foram tratados. O julgamento vigente está na [auditoria adversarial](AUDITORIA-ADVERSARIAL.md). Android **1.19.8 / versionCode 44** disponível em `/v1/app-update`, com manifesto, assinatura e download integral verificados. A API e a exportação desktop foram atualizadas. Os números abaixo documentam entregas históricas.

A falha 503 na exclusão foi corrigida no banco ativo: limite de coleções do Atlas, preservação de um banco sintético e reserva antecipada do arquivo de histórico. [Diagnóstico, recuperação e provas](DELETION-STORAGE-2026-10-01.md). A captura real permanece confirmada até nova confirmação do usuário.

A atualização de apresentação **Android 1.19.9 / versionCode 45** restaura duas casas decimais no Início. Os valores dos cards usam uma única linha, com ajuste de fonte no Android e medição de texto no web; abaixo de 360 px a grade usa uma coluna. Valores usuais e altos foram conferidos no navegador em 320 e 390 px, sem quebra ou corte. Lint, TypeScript, export desktop, build Android, assinatura e download do APK passaram. Evidências em `../../audit_work/dashboard-cents-2026-10-01/`; não houve escrita no banco. Sem aparelho ADB, a instalação física continua pendente.

## Acréscimo: Cabe na minha vida?

Implementado em **Mais → Cabe na minha vida?**: entrada por print ou preço confirmado, extração xAI revisável, horas de renda de trabalho, caixa de 90 dias, adiamento de meta, três alternativas de compra e cartão PNG com campos públicos restritos. Android recebe imagens pelo intent de compartilhamento. Contratos, premissas e recuperação: [PURCHASE-CHECK.md](PURCHASE-CHECK.md).

Versão da entrega de 29/09/2026: **Android 1.18.0 / versionCode 35**, ARM64, **43.817.247 bytes**. SHA256: `7F00E4C860B3CC46ACD97E4E83496F2E399E37AED6D2DCFAC715ABB0F1BF52A6`. Build concluído; assinatura verificada e certificado igual ao 1.17.0. O APK foi promovido a `mobile/artifacts/financial-twin-arm64.apk` e preservado como `financial-twin-1.18.0.apk`. O endpoint local anunciou versão 1.18.0 e seu download integral coincidiu com tamanho e hash do manifesto.

Backend ativo com Mongo conectado e as rotas `purchase-check` e `purchase-scan` no OpenAPI. Export desktop atualizado para a API local de porta 8000. A validação final passou com **104 testes Python**, regressão de storage web, typecheck, lint e teste de privacidade do cartão. A xAI reconheceu R$ 300,00 e três parcelas de R$ 100,00 no print sintético através da API ativa. A simulação retornou 16,0 horas, 30 dias de adiamento e saldo mínimo de R$ 700,00. O mapeamento do contexto real de compromissos também foi aceito pelo contrato da nova rota; seu conteúdo financeiro não foi copiado para as evidências.

Evidências: `../../audit_work/purchase-check-2026-09-29/`, incluindo `delivery-validation.json`, `live-assessment.json`, `live-synthetic-scan.json`, `planning-context-contract.json`, `signature-validation.json`, `apk-validation.json` e `served-apk-validation.json`.

Inspeção visual final: análise, alternativas e PNG real do cartão conferidos no navegador, também em 390 px. `public-card.png` contém os bytes gerados pelo app; `purchase-mobile.png` registra a tela. O navegador integrado iniciou o download do PNG e depois o cancelou com zero bytes recebidos; não foi confirmado salvamento concluído por esse navegador.

Limites: nenhum aparelho conectado ao ADB; recebimento do print e compartilhamento nativo ainda não homologados fisicamente. O HTTPS da tailnet voltou a retornar timeout neste host; download remoto não foi confirmado. Build e código não equivalem a esses testes físicos. As premissas da nova tela permanecem na sessão e não são salvas como perfil.

## Entrega original da NT-001

Cinco funcionalidades implementadas em API e interface: fechamento explicável, cenários de decisão, guardião de cobranças/reembolsos, liquidez por conta e experimentos de economia. Entrada: **Mais → Decisões e evidências**.

Na validação original, o backend atualizado estava ativo em `127.0.0.1:8000`. `/health` confirmou Mongo conectado. O OpenAPI continha 13 caminhos `/v1/insights`, alguns com métodos distintos. Fechamento de produção respondeu com consistência `snapshot` e cobertura `incomplete`, conservando as lacunas reais.

Android **1.17.0 / versionCode 34**, ARM64, gerado e publicado no endpoint local. Tamanho: **43.748.483 bytes**. SHA256: `FFB4E595608F91DD67719D00151CB972796FF67CCE467DA072E616EC6AEED645`. O download HTTP local foi comparado integralmente ao manifesto. Assinatura APK verificada; certificado coincide com o APK anterior. Trata-se do certificado Android Debug já utilizado no canal privado do projeto.

O HTTPS configurado da tailnet retornou timeout neste host. Tailscale está online e o proxy `:8443 → 127.0.0.1:8000` permanece configurado. Não afirmar download remoto validado. Não há aparelho conectado ao ADB.

## Fila e nível de evidência

| Etapa | Estado | Evidência / limite |
|---|---|---|
| Q00 | code_verified com ressalva | Nota e checkpoint posterior às primeiras edições; não há snapshot inicial integral |
| Q01 | integration_verified | Política econômica, calendário local, estorno e limites via HTTP |
| Q02 | code_verified | Matching adversarial: ISO, direção, valor, empate, manual e overflow |
| Q03 | integration_verified | Rollback Mongo real, revisões, idempotência, invalidação e proteção de créditos vinculados |
| Q04 | code_verified | Âncora mensal e rejeição de pagamentos neutros |
| Q05 | integration_verified | 241 revisões com timestamp igual percorridas sem perda/duplicata; token de prévia e rascunhos |
| Q06 | code_verified | Bytes duráveis e adaptadores compilados; testes de storage web; homologação física pendente |
| Q07 | integration_verified local | Locks, auditorias de dependências, backup/restauração, assinatura e download local |
| Q08 | integration_verified | Fechamento, grafo polimórfico, desfazer/refazer e idempotência |
| Q09 | integration_verified | Casos, dossiê, crédito único, revisão e rollback |
| Q10 | integration_verified | Motor Decimal, quatro alternativas e curva diária; simulação visual com dados sintéticos |
| Q11 | integration_verified | Reserva, débitos futuros do doador, conta desconhecida, limite diário e conservação monetária |
| Q12 | integration_verified | Um experimento ativo; conclusão libera trava e congela data; comparação não causal |
| Q13 | integration_verified local | Web export, lint/typecheck, interface em 390 px, Android e backend ativado |
| Dispositivo | blocked_external | Instalação, captura real, encerramento do processo e sobrevivência offline ainda não homologados |
| Tailnet | blocked_external | Timeout de conexão HTTPS neste host; não foram alteradas permissões/exposição de rede |
| CI remoto | planned | YAML entregue; depende da adoção no repositório apropriado |

## Matriz A01–A16

| Achado | Implementação | Evidência |
|---|---|---|
| A01 matching inseguro | `matching.py`, `transaction_service.py` | oposto, diferente, empate e manual não se fundem automaticamente |
| A02 gravação parcial | sessões em `atomic.py` e wrappers | falha injetada no audit com rollback Mongo real |
| A03 transferência quita compromisso | `eligible_payment` | rejeição sem atualizar compromisso |
| A04 estorno vira receita | política única | 100 de consumo menos 15,50 de estorno = 84,50; receita zero |
| A05 IDs obsoletos de transferência | invalidação e dependências | edição remove conciliação; revisão antiga rejeitada |
| A06 ISO perde score | normalização UTC | ISO e datetime equivalentes |
| A07 âncora perdida | cronograma persistido | janeiro 31 → fevereiro 28 → março 31 |
| A08 arquivo temporário | IndexedDB/diretório persistente | código e build; sobrevivência física pendente |
| A09 import fora do media_store | armazenamento comum | suíte de importação e mídia |
| A10 calendário UTC | financial_time e dashboard_window | limites locais e period_end via HTTP |
| A11 truncamento de filas | cursores e continuação na UI | 241 itens Mongo e 241 itens web |
| A12 versão divergente | manifesto lido do APK e validado | download local de 1.17.0 / código 34 |
| A13 propagação inicial ativada | opt-in falso por edição e token | prévia alterada rejeitada antes de gravar |
| A14 falso salvamento | persistência confirmada e erros visíveis | quota e JSON corrompido preservam original |
| A15 varreduras e limites | janelas do dashboard, saldos desde referência e overflow explícito | overview e integração; consulta all continua intencionalmente histórica |
| A16 operação/governança | locks, scripts, runbook, backup/restore e CI | restauração real; raiz ainda sem repositório Git próprio/CI remoto executado |

Também foram adicionados: limite de login por IP/processo; atualização das dependências vulneráveis com adaptação CommonJS/ESM; exclusão conservadora de notificações negadas/canceladas; trava compartilhada na fila Android; aviso de envio de fotos/PDFs à extração xAI.

## Checks executados

Revalidação em 29/09/2026: `scripts/validate.ps1` e `scripts/validate.ps1 -Integration -WebExport` concluíram com exit code 0. A segunda execução registrou 99 testes Python, 17 verificações integradas em bancos sintéticos `financial_twin_test_*`, testes de armazenamento web, typecheck, lint e export web em `data/test-evidence/20260929-054425/integration-results.json`. O manifesto local do APK 1.17.0 foi conferido novamente contra tamanho e SHA-256 do arquivo. Nenhum aparelho apareceu em `adb devices`; homologação física permanece pendente.

- **99 testes Python passaram**, incluindo suíte anterior e regressões significativas.
- **17 verificações integradas passaram**, via FastAPI/TestClient e MongoDB replica set real com dados sintéticos: `../../audit_work/implementation-2026-09-29/integration-05/integration-results.json`.
- Backup BSON restaurado em banco de teste vazio; contagens e Decimal128 preservados.
- Storage web: consulta expandida, 241 itens, idempotência, quarentena, quota e corrupção preservada.
- TypeScript e ESLint do código-fonte passaram; `scripts/validate.ps1` verifica os exit codes.
- Builds web e Android ARM64 concluídos; inspeção visual em largura padrão e 390 px. Simulação de saldo 1.000 e compra 300 em três parcelas exibiu finais 700, 800 e 760; a espera mostrou 100 de parcelas após o horizonte.
- pip-audit do lock Python e pnpm audit: zero avisos conhecidos na consulta. Evidências: `python-advisories.json` e `mobile-advisories-fixed.json`.
- Saúde/rotas e preservação: `live-release-results.json`, `production-before.json`, `served-artifact.json`, na pasta da implementação.

## Preservação e operação

Hashes e contagens antes/depois da ativação permaneceram iguais: **1.482 transações, 407 obrigações, 27 faturas, 13 planos de parcelamento e 9 contas**. Não foi executada reconstrução ou correção heurística do ledger. A inicialização criou índices de workflow. Um índice por conta já existia com outro nome: o startup foi ajustado para reutilizar a definição equivalente, sem apagar o índice existente.

Bancos sintéticos e restaurações foram mantidos para inspeção. Os testes não executaram pagamentos, transferências bancárias, extrações de documentos reais ou mensagens externas.

A tentativa inicial de backup recursivo foi interrompida antes de gerar arquivo. `source-checkpoint-q01.zip` foi criado após as primeiras edições e não representa fonte anterior integral. Git do mobile e hashes da auditoria anterior foram preservados; não houve reset, stash ou clean.

## Próximos passos de homologação

1. Instalar APK conservando os dados e executar o roteiro físico da seção 10 do runbook. Não marcar device_verified a partir do build.
2. Verificar a tailnet em dispositivo autorizado. Este host não concluiu o HTTPS; não alterar firewall/permissões para mascarar o resultado.
3. Adotar o workflow no repositório apropriado. O mobile é checkout separado; nenhum CI remoto foi executado nesta entrega.
4. Atribuir contas às capturas históricas e confirmar cobertura dos extratos mediante revisão humana. Nenhuma funcionalidade inventa esses dados.

O fechamento apresenta prévia explícita de 100 movimentos; totais abrangem o período inteiro. Detectores exigem vínculo com compromisso; cobranças sem vínculo precisam de revisão. A comparação de experimentos é observacional. Essas limitações de inferência permanecem visíveis.
