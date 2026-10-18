# Revisão de subcategorias

## Catálogo da NT-ARCH-MONGO-002

O catálogo servido em `GET /v1/categories` (`taxonomy`) e nas opções de classificação é a árvore §07 de outubro de 2026, com no máximo dois níveis. Os grupos de nível 1 são Habitação, Alimentação, Transporte, Saúde e Cuidados, Pets, Lazer e Estilo, Compras, Educação, Finanças/Impostos e Receitas. Cada um tem `allow_direct_posting` falso. Cada grupo de despesa inclui a subcategoria `Outros`. Receitas se divide em Renda Principal, Renda Extra e Reembolsos, sem `Outros` e sem MCC.

Cada nível 2 traz `path`, `level` 2, `parent`, `analytics.budget_bucket`, `analytics.nature` e `analytics.default_mcc_codes`. O bucket e a natureza são os da tabela: `NEEDS`, `WANTS` ou `SAVINGS`, e `FIXED`, `VARIABLE` ou `F/V` onde a tabela imprime F/V. Alimentação mistura `NEEDS` e `WANTS` por subcategoria e não recebe natureza inventada. Educação fica `SAVINGS / NEEDS` e Finanças/Impostos fica `NEEDS / SAVINGS`. Os MCCs são os do grupo, repetidos em cada filho. Receitas não tem esses códigos.

A conversão reescreve categoria e subcategoria no extrato, nas capturas persistidas, nos compromissos e no perfil de `future_schedule_overrides` quando o par tem destino explícito. Um lançamento que só nomeia um grupo de nível 1 vai para o `Outros` desse grupo. Ausência, nulo, vazio e `Sem categoria` continuam sem categoria. Um par sem destino determinístico conserva o texto original. O rótulo anterior fica no mesmo registro, em `taxonomy_previous`. Valor, data, descrição, conta, natureza econômica, vínculos e o armazenamento `Decimal128` não mudam. Uma segunda execução não altera mais nada. O cliente lê `categoria_subcategorias` da API; esta mudança não exige APK novo.

Em Mais → Categorias, abra as subcategorias da categoria desejada. Criar, renomear e excluir preservam os valores e fontes dos registros. Exclusão de uma subcategoria em uso exige uma substituta da mesma categoria.

Desde 1.20.4, `GET /v1/categories/subcategories?category=...` inclui `token` em cada item de `details`. PATCH exige esse valor em `expected_token`; DELETE exige o parâmetro de consulta `expected_token`. A revisão abrange a geração do cadastro e os registros afetados e é conferida novamente dentro da transação MongoDB. Um nome recriado representa uma nova geração: uma aba antiga não pode alterar os novos registros.

Ausência do token retorna 428 e exige atualizar o cliente/lista. Alteração da geração ou dos registros retorna 409; o cliente recarrega a lista e conserva o texto digitado para o usuário revisar antes de tentar novamente. O servidor não reaplica automaticamente uma intenção antiga sobre um novo cadastro.

Criação continua explícita: um nome retirado pode ser criado novamente sem fundir as gerações. Gravação, reclassificação e auditoria são atômicas. Evidências da correção B08 estão no relatório de 1.20.4 em `../../audit_work/fixes-B01-B12-2026-10-01/RELATORIO.md`.
