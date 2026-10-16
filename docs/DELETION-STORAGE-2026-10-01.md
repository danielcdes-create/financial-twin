# Exclusão indisponível no banco ativo

A captura apresentada pelo usuário não foi excluída. Duas requisições DELETE retornaram 503, com erro MongoDB 8000. O Atlas estava no limite compartilhado de coleções; `financas.deleted_movements` ainda não existia. A primeira escrita do arquivo de histórico tentava criar essa coleção, falhava e abortava a transação. As suítes anteriores em banco local não cobriam essa condição do ambiente ativo.

## Correção e recuperação

O banco sintético `financial_twin_test_delete_1735fd4f`, identificado pelo resultado da auditoria original, foi exportado com documentos BSON, opções e índices. Sua restauração local foi validada por hashes de todos os documentos nas nove coleções antes da retirada da cópia remota. Nenhum outro banco foi retirado. O backup e a cópia local permanecem disponíveis.

Com a capacidade liberada, foi criada a coleção vazia `deleted_movements` no banco financeiro. O startup agora reserva essa coleção e seu índice antes de atender solicitações. A API fornece orientação específica quando o Atlas recusa uma operação pelo limite de coleções.

As seis suítes integradas que criam bancos sintéticos passaram a exigir MongoDB local por padrão. A execução deliberada em cluster remoto exige `FIN_ALLOW_REMOTE_TEST_DATABASE=1`. O modo de consulta `check_convergence.py --readonly` continua disponível. Isso evita que validações rotineiras consumam a capacidade da aplicação.

## Evidências

Passaram 193 testes Python e 17 verificações integradas de exclusão no replica set local. As seis suítes recusaram o alvo Atlas padrão antes de abrir um cliente MongoDB. No próprio Atlas, uma captura sintética foi excluída via HTTP com 200; seu reenvio retornou 200 idempotente, com um arquivo de histórico e uma auditoria. Captura e evento passaram a cancelados. Esse banco de prova também foi preservado e restaurado localmente antes de retirar sua cópia remota.

Os hashes e as contagens das oito coleções financeiras permaneceram iguais. A captura real continua confirmada e sem arquivo de exclusão; confirmar sua exclusão continua sendo uma ação do usuário.

A API foi reiniciada sob o supervisor existente. `/health` confirmou MongoDB conectado, e a prévia de exclusão da captura real retornou 200, com `can_delete=true`. Resultado em `live-server.json`. O MongoDB temporário local foi encerrado; seus dados restaurados permanecem no diretório de evidências da entrega anterior.

Resultados e backups: [deletion-storage-2026-10-01](../../audit_work/deletion-storage-2026-10-01/), incluindo `preservation.json`, `retained-test-backup/manifest.json`, `atlas-verification.json`, `atlas-probe-backup/manifest.json`, `local-deletion-regressions.json`, `backend-tests.log` e os snapshots `production-before.json` / `production-after.json`.

Esta entrega altera o servidor e ferramentas de teste. O APK 1.19.8 continua compatível; não requer reinstalação ou limpeza de dados.
