# Exclusão no Extrato

App 1.19.4 / código 40: toque no registro do Extrato, escolha **Excluir registro**, confira estabelecimento, valor e data e toque em **Confirmar exclusão**. Cancelar não envia exclusão. A operação exige conexão; a tela só confirma sucesso após resposta explícita do servidor.

`GET /v1/movements/{ledger|capture}/{identity}/deletion` fornece uma confirmação vinculada ao conteúdo atual. `DELETE /v1/movements/{ledger|capture}/{identity}` exige esse token; mudanças ou novos vínculos impedem a exclusão com 409. Repetir a mesma confirmação após perda de resposta é idempotente.

Registros importados são retirados da coleção ativa `transactions` somente dentro de uma transação MongoDB que preserva seu documento completo em `deleted_movements`, identificado por origem e identidade. Capturas permanecem em `provisional_transactions` com status `cancelled` e também têm o documento anterior preservado. `audit_log` registra a ação. Fontes, evidências e eventos originais são mantidos. Nenhum outro registro de valor/descrição semelhante é excluído automaticamente.

Consultas, dashboard, saldos por referência e simulações passam a usar apenas os registros ativos, pelos mesmos caminhos existentes. O evento de uma captura excluída permanece terminal, impedindo que um reenvio antigo a recrie. Revisões pendentes referentes ao registro excluído são encerradas com motivo explícito.

Pagamentos vinculados a compromissos, conciliações, pernas de transferência confirmada e fontes usadas em casos/reembolsos são protegidos. A exclusão não desfaz silenciosamente esses vínculos nem ajusta contratos ou obrigações. A conferência desses vínculos ocorre novamente ao excluir. Escritas de matching e vínculo de compromisso também tocam o registro dentro de sua transação para disputar uma exclusão concorrente.

A prevenção estrutural de 2026-10-03 também protege registros que sustentam evidências ativas. Antes de cancelar ou excluir a fonte, o vínculo deve ser desfeito explicitamente em Decisões e evidências; a evidência revertida permanece no histórico. A exclusão participa da mesma trava de captura, importação e conciliação, para que uma inserção concorrente não considere o registro prestes a ser retirado como uma movimentação ativa. Correções de valor/data/natureza de uma captura com evidências vinculadas também pedem revisão do vínculo.

Não há limpeza automática dos documentos arquivados nem restauração pela interface nesta versão. Um novo import externo que substitua a coleção ativa precisa respeitar os arquivos de exclusão; este recurso não modifica ferramentas externas de migração.

Desde 1.19.7 / código 43, a exclusão também arquiva o descritor auxiliar, remove-o de `transaction_descriptor_index` e atualiza sua cobertura na mesma transação. A classificação em lote dos registros restantes continua disponível. Um índice previamente incompleto não é declarado completo apenas porque a coleção ativa diminuiu. Exclusões concorrentes e repetição foram verificadas em MongoDB real na [auditoria de convergência](../../audit_work/consistency-convergence-2026-10-01/RELATORIO.md).

Validação: 140 testes Python, casos de duplicado individual, token obsoleto, vínculos, replay da captura, evidências mantidas e falha de auditoria. Integração HTTP em banco sintético independente confere transações reais, totais, saldos, concorrência e rollback. Evidências em `../../audit_work/movement-deletion-2026-10-01/`; instalação em aparelho físico ainda não conferida.
