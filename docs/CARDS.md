# Cartões e faturas

Atualização 1.20.33: planejamento, compra e projeção compartilham `obligation_policy.py`: obrigações em `futura`, `aberta`, `vencida` e `parcialmente_paga` participam quando têm saldo positivo. Editar uma fatura futura para aberta conserva a dívida nos leitores; identidade e prevenção de dupla contagem continuam obrigatórias. Uma fatura quitada permanece fora dos fluxos em aberto.

Confirmar exclusão exige dono válido antes do diálogo e imediatamente antes do transporte. Recarga após 409 conserva a origem e o rascunho até conferir as diferenças; uma segunda recarga idêntica não autoriza substituir o remoto silenciosamente. Valor usa o parser compartilhado estrito. [Auditoria atual](AUDITORIA-ADVERSARIAL.md).

Em **Mais → Cartões**, escolha primeiro o cartão. A tela seguinte mostra somente suas faturas, com competência, vencimento, total, valor pago e saldo em aberto. **Trocar cartão** volta ao seletor.

É possível editar instituição, nome e os quatro dígitos finais do cartão. O identificador importado continua estável. As alterações de nome também aparecem na seleção de cartões dos compromissos.

Em **Editar cartão**, também é possível gerenciar validade (MM/AAAA), dia de vencimento da fatura, dia de fechamento, melhor dia para compras e observações. Esses campos são opcionais; os dias aceitam 1 a 31. A validade do cartão é independente do vencimento da fatura. O melhor dia é informado pelo usuário, sem cálculo automático, pois o calendário pode variar conforme o banco. As informações aparecem no catálogo e no detalhe do cartão.

As configurações ficam nos metadados do cartão e não alteram datas de faturas, compromissos, parcelas ou pagamentos existentes. A API representa validade como AAAA-MM e dias como inteiros; `null` remove explicitamente um campo opcional. Clientes antigos que omitem esses campos preservam os valores existentes. O formulário inclui esses campos na comparação entre origem, rascunho e versão remota em caso de conflito.

Auditoria de 2026-10-08: a seleção dos compromissos usa o catálogo atual de cartões, incluindo apelido e final corrigidos. Cartões excluídos não são oferecidos para novos vínculos; editar um compromisso que já usa esse cartão conserva o vínculo e permite corrigir seus outros dados.

Em **Mais → Assinaturas e parcelamentos**, os parcelamentos importados mostram o cartão associado pelo nome atual do catálogo. Vínculos conferidos ficam em `installment_card_links`, com `_id` igual ao ID do plano e `card_id` igual ao ID estável do cartão, revisão, fundamento e auditoria. Essa camada não reescreve o plano original nem duplica suas parcelas; `/v1/installments` entrega `card_id`, `card_label`, `card_deleted` e `card_link_requires_review`. Um cartão excluído conserva o vínculo histórico; um cartão ausente exige conferência, sem associação automática por nome de loja. As mensalidades e anuidades gerenciadas usam o `card_id` do compromisso. Uma anuidade discriminada num final de cartão permanece nesse final, sem ser atribuída a outro cartão de compras. Parcelas futuras são previsão e podem sofrer redução ou isenção.

Cada fatura permite corrigir competência, vencimento, total e observações. O total e vencimento são sincronizados com a obrigação vinculada, em uma única transação MongoDB. A diferença no total ajusta o principal e o agregado de novos lançamentos; os itens da fatura, compras e pagamentos registrados permanecem intactos. A correção fica registrada na auditoria com os documentos anteriores e posteriores. O status é derivado do saldo e dos pagamentos existentes; editar uma fatura não cria um pagamento.

O total deve ter até duas casas decimais e cobrir os pagamentos, o saldo anterior e os encargos registrados. Competências duplicadas no mesmo cartão são recusadas. Uma fatura sem uma única obrigação correspondente, ou com valores divergentes dela, precisa ser conciliada antes da edição.

Desde 1.20.4, essa verificação inclui o saldo em aberto de ambos os documentos e a equação total − pagamento. Divergência retorna 409, sem reparar valores silenciosamente. Uma alteração apenas de observações em registro consistente grava somente os metadados, preservando a fatura e a obrigação. Vencimentos são datas civis, incluindo a convenção legada de meia-noite UTC; instantes reais de pagamento continuam sujeitos ao calendário de Brasília.

**Excluir** remove o cartão ou a fatura da lista de uso, com confirmação explícita. É uma exclusão recuperável: não apaga compras, pagamentos, obrigações nem conciliações, e valores devidos continuam nos cálculos financeiros. **Mostrar excluídos → Restaurar** recupera o cadastro. Excluir um cartão preserva suas faturas e os vínculos dos compromissos existentes; ele deixa de ser oferecido para novos vínculos. Restaure o cartão antes de editar suas faturas.

Os perfis e as exclusões usam documentos com IDs `card-profile:<id>` e `bill-profile:<id>` na coleção existente `account_profiles`, com chaves exclusivas e discriminador `entity_type`. O catálogo de contas bancárias ignora esses tipos. Não há alteração do contrato dos cadastros importados nem criação de outra coleção. As correções de fatura usam os campos permitidos pelos validadores atuais.

Leituras usam snapshot. Gravações usam transação, trava compartilhada de movimentações e token calculado a partir do cadastro, metadados e, para faturas, das obrigações vinculadas. Mudanças concorrentes exigem atualizar a lista antes de salvar. A API anterior `/v1/cards` mantém compatibilidade com APKs antigos; o aplicativo novo usa `/v1/card-catalog` e `/v1/card-bills/{id}`.

Validação: testes de seleção, isolamento entre cartões, edição, exclusão, restauração, conflitos, sincronização da dívida, valores inválidos e rollback. Integração em banco sintético com replica set e os validadores estritos reais. UI verificada em 320 e 390 pixels; APK ARM64 gerado e assinado. Instalação e comportamento em aparelho físico exigem verificação no dispositivo.
