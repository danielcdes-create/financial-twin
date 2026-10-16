# Revisão de subcategorias

Em Mais → Categorias, abra as subcategorias da categoria desejada. Criar, renomear e excluir preservam os valores e fontes dos registros. Exclusão de uma subcategoria em uso exige uma substituta da mesma categoria.

Desde 1.20.4, `GET /v1/categories/subcategories?category=...` inclui `token` em cada item de `details`. PATCH exige esse valor em `expected_token`; DELETE exige o parâmetro de consulta `expected_token`. A revisão abrange a geração do cadastro e os registros afetados e é conferida novamente dentro da transação MongoDB. Um nome recriado representa uma nova geração: uma aba antiga não pode alterar os novos registros.

Ausência do token retorna 428 e exige atualizar o cliente/lista. Alteração da geração ou dos registros retorna 409; o cliente recarrega a lista e conserva o texto digitado para o usuário revisar antes de tentar novamente. O servidor não reaplica automaticamente uma intenção antiga sobre um novo cadastro.

Criação continua explícita: um nome retirado pode ser criado novamente sem fundir as gerações. Gravação, reclassificação e auditoria são atômicas. Evidências da correção B08 estão no relatório de 1.20.4 em `../../audit_work/fixes-B01-B12-2026-10-01/RELATORIO.md`.
