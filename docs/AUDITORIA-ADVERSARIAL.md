# Auditoria adversarial — 15/10/2026

Julgamento da árvore local de `financial-app` a partir de `431aaf5`, com a correção de contagem dupla desta entrega, commit `cc7f562`. Dados sintéticos. O ledger ao vivo não foi lido, escrito, deduplicado nem reparado. O release privado no disco é Android 1.20.61 / versionCode 107, SHA-256 `F321AA3191326A15BE85DCD5B357E86B84FD3AC03143111D9746171E6B05632B`, com o certificado de debug anterior. A instalação física permanece unverified.

Cada superfície recebe um veredito: `held`, `defect fixed`, `still open` ou `owner-blocked`. O que só um aparelho pode fechar fica `unverified`.

| Superfície | Veredito |
| --- | --- |
| Invariantes do ledger | defect fixed |
| Dinheiro e sinal | defect fixed |
| Data civil `America/Sao_Paulo` | held |
| Classificação armazenada | held |
| Compromissos e corte fixo de pago assumido | held |
| Captura e fila local | held |
| Cartões e contas | defect fixed |
| Dashboard | defect fixed |
| Autenticação e privacidade | held |

| Residual absorvido | Veredito |
| --- | --- |
| E1, editar parte das parcelas futuras de um compromisso gerido | owner-blocked. Não implementado. |
| Comportamento novo de obrigação pontual | owner-blocked. Não implementado. |
| Corte móvel de pago assumido | owner-blocked. Não implementado. |
| Schema ou semântica do ledger, inclusive total de banco diferente de parcela × quantidade | owner-blocked. Não implementado. |
| SEC02, trocar a chave de debug | owner-blocked. Não implementado. |
| UI-R01, leitura depois do ACK | held no helper compartilhado. Medição no aparelho: unverified. |
| UI-R02, blocos acima do campo ativo | still open. IME físico: unverified. |
| PS-R01, inbox sem orçamento de retenção | still open. Fila grande e disco baixo: unverified. |
| PS-R02, share com sessão bloqueada | still open. App frio, bloqueio e biometria: unverified. |
| Homologação física (instalação, IME, gestos, TalkBack, biometria, câmera, notificação, share) | unverified. Sem ADB. O APK 1.20.61 está no disco; a instalação física não foi feita. |
| Sugestão inversa no extrato, gravar Manter/Avaliar/Cortar, tipo editável Conta/Dívida | still open. Não foram inventados. |
| Duas evidências de voz distintas no aparelho | unverified. |
| Exceção GHSA do `node-forge` até 01/11/2026 | still open. |
| JSON numérico do dashboard em vez de string com duas casas | owner-blocked. Mudaria o contrato público da API. |

## Ledger

defect fixed. A captura promovida deixa de entrar duas vezes no total econômico. As demais regras abaixo permanecem held.

| Regra | Veredito |
| --- | --- |
| Evidência não é transação. Compras parecidas permanecem distintas. O mesmo `event_id` é idempotente. | held. `automatic_eligible` recusa compra parecida sem referência bancária. Teste `test_similar_purchases_do_not_merge_without_a_bank_reference`. |
| Transferência própria é neutra. Compra no cartão é consumo. Pagamento de fatura não entra na despesa. Estorno positivo reduz despesa. | held. `effects` e `calculate_financial_totals`: 100 de compra menos 15,50 de estorno = 84,50, com transferência e fatura de fora. Teste `test_money_sign_refund_transfer_and_card_bill_stay_apart`. |
| A compra parcelada no extrato não liquida a parcela. | held. `eligible_payment` recusa `parcelamento_purchase` no mesmo teste. |
| Captura confirmada já promovida ao extrato é o mesmo evento econômico. | defect fixed. O Início, a natureza, a série mensal, o drilldown econômico e o saldo da conta somavam o lançamento e a captura ligada por `linked_transaction_id`. Fechamento e insights já pulavam esse par. A lista geral do extrato continua mostrando os dois documentos. |
| Schema ou sentido de um lançamento, inclusive total de banco diferente de parcela × quantidade. | owner-blocked. Nenhuma coleção foi alterada. |

A correção está em `duplicate_of_ledger` / `captures_not_in_ledger` (`backend/app/economic_policy.py`), usados por `calculate_financial_totals`, `_dashboard` e o drilldown `spending_only`. A projeção única do Início inclui `linked_transaction_id`; sem esse campo o par some na leitura e a soma dobra. No saldo, a captura também não entra quando a perna do extrato já está dentro da referência, não só quando ela já foi somada depois dessa data. Testes `test_dashboard_does_not_count_a_promoted_capture_twice`, `test_linked_capture_is_one_economic_expense_on_the_dashboard_and_drilldown` e `test_linked_purchase_capture_does_not_move_the_balance_twice`.

## Dinheiro e sinal

defect fixed na contagem dupla do mesmo evento. held no sinal: a soma usa `Decimal` até a borda JSON do dashboard, que continua numérica. Trocar esse formato para string fica owner-blocked: mudaria o contrato público da API e não foi feito. `parseMoneyInput` aceita `1.234,56` e `1234.56` e recusa `1.2,34`, `1e2` e sinal sem permissão. `amount` recusa infinito. Testes do cliente em `mobile/scripts/test-adversarial-audit.cjs` e `test_money_sign_refund_transfer_and_card_bill_stay_apart`.

## Data civil

held. `America/Sao_Paulo`. O instante `2026-10-01T02:30:00Z` é 30/09/2026. A data civil `2026-10-10` não anda. A janela de filtro é inclusiva no início e exclusiva no dia seguinte. O mês do Início usa o mesmo dia civil. Testes `test_civil_calendar_keeps_brasilia_and_an_exclusive_end` e `civilToday` / `civilMonthWindow` no script do cliente.

## Classificação armazenada

held. A linha do extrato mostra a natureza, a categoria, a subcategoria e o método guardados. Transferência sem as duas contas distintas não esconde a categoria nem inventa contas. Doação em cartão continua compra. Código de pagamento desconhecido fica em branco. `cartao` e `cartao_credito` usam o mesmo rótulo. Testes `classificationLine`, `ownAccountTransfer` e `paymentMethodLabel` no script do cliente, e `test_persisted_capture_category_wins_over_inference_in_every_read` em `backend/tests/test_filter_consistency.py`.

## Compromissos e corte

held para a apresentação já entregue. O corte é a data fixa `2026-10-01` nos dois lados. 30/09 sem vínculo é Pago (assumido). 10/10 sem vínculo, com o dia já passado, é Vencido · sem vínculo e entra em A conciliar. 10/11 sem vínculo é Previsto e fica fora de A conciliar. Vínculo continua Pago vinculado. Âncora 31/01, 28/02, 31/03. Obrigação de 100 em 3 parcelas: 33,33 + 33,33 + 33,34. Sugestão automática não casa setembro com o vencimento de outubro. A parcela 1 do extrato não substitui o total do plano. Testes `test_fixed_cutoff_schedule_and_suggestion_window`, `occurrenceState`, `reconcileCharge`, `obligationShare`, `anchoredDue` e `parcelamentoPurchaseSummary`.

| Residual absorvido | Veredito |
| --- | --- |
| E1, editar só parte das parcelas futuras de um compromisso gerido | owner-blocked. O menu “Editar parcelas futuras” continua fora de `managed`. Não foi implementado. |
| Comportamento novo de obrigação pontual | owner-blocked. O `kind obrigacao` já existe. Não houve comportamento novo. |
| Corte que acompanha o mês | owner-blocked. A constante continua `2026-10-01`. |
| Sugestão inversa no extrato, gravar Manter/Avaliar/Cortar, tipo editável Conta/Dívida | still open. Continuam fora das notas v2–v5. Não foram inventados. |

## Captura e fila local

held para o descarte da fila. `finalizeQueueRow` só apaga a linha cuja identidade completa confere e não mexe em tabela de lançamento. Identidade diferente preserva a fila. O diálogo de exclusão avisa que o extrato salvo não muda. Teste de `finalizeQueueRow` no script do cliente.

| Residual absorvido | Veredito |
| --- | --- |
| PS-R01, inbox nativo sem orçamento de retenção | still open. `NotificationInbox` reescreve o array inteiro em `SharedPreferences.commit` e não tem coletor com orçamento. Fila grande e disco baixo: unverified. |
| Duas compras semelhantes | held. `test_similar_purchases_do_not_merge_without_a_bank_reference`. |
| Duas evidências de voz distintas no aparelho | unverified. Esta passagem não executou o microfone nem a fila nativa. |
| Exceção GHSA do `node-forge` empacotado até 01/11/2026 | still open. A exceção de build continua datada. Não foi substituída por release oficial nesta entrega. |

## Cartões e contas

defect fixed no saldo: a captura promovida não entra de novo quando o extrato já foi somado. held na fatura: saldo positivo permanece `vencida`, `aberta` ou `parcialmente_paga`. Saldo zero é `paga`. Essas quatro situações continuam no conjunto aberto. Teste `test_open_card_bill_stays_in_planning_until_the_balance_is_zero`. Saldo sem referência continua nulo. Conta encerrada permanece para histórico e não é escolha nova: contrato em `docs/ACCOUNT-PROFILES.md`, sem regravação do ledger.

## Dashboard

defect fixed na contagem dupla descrita em Ledger. O restante dos totais continua a política econômica única: categoria soma a despesa, sem truncar o agregado. Testes `test_every_dashboard_category_matches_its_economic_drilldown` e `test_no_category_truncation_beyond_eight_or_twelve`. “Cabe na minha vida?” não cria lançamento. Uma meta já alcançada pode atrasar se uma parcela posterior gasta a reserva. Testes de `backend/tests/test_purchase_check.py`.

## Autenticação e privacidade

held no que roda sem aparelho. Atualizar o perfil aumenta `auth_version`, grava auditoria e devolve sessão nova. Teste `test_profile_update_bumps_version_and_writes_audit`. A trava `runUserAction` solta quando o ACK retorna, mesmo com uma Promise interna ainda pendente. `refreshAfterMutation` dispara a releitura com `void` e devolve `Promise.resolve()`, então a releitura depois do ACK não segura a trava global. Teste no script do cliente.

| Residual absorvido | Veredito |
| --- | --- |
| UI-R01, leitura depois do ACK segura a trava | held no helper compartilhado, pelo teste acima. Medição no aparelho: unverified. |
| UI-R02, blocos assíncronos acima do campo ativo | still open. Não houve salto reproduzido nesta passagem. IME físico: unverified. |
| PS-R02, share com sessão bloqueada | still open. `app/+native-intent.ts` só encaminha para `/handle-share` e não guarda a intenção. App frio, bloqueio e biometria: unverified. |
| SEC02, troca da chave de debug | owner-blocked. Não implementado. `android/app/build.gradle` ainda assina release e debug com `signingConfigs.debug`. |
| Homologação física: instalação, IME, gestos, TalkBack, biometria, câmera, notificação e share | unverified. Não há aparelho ADB nesta sessão. O APK 1.20.61 está no disco; a instalação física não foi feita. |

## O que as auditorias anteriores deixavam aberto

Os registros datados foram absorvidos por esta nota e apagados. A evidência em `audit_work` permanece. NT-001, o runbook, UI-STABILITY, FUTURE-COMMITMENTS e os contratos de área continuam sendo a regra viva. Onde esta nota e um status antigo discordam, vale o veredito desta nota para o estado do código, e a NT-001 para o invariante.

owner-blocked e não implementados: E1, obrigação pontual nova, corte móvel, mudança de schema ou de semântica do ledger, e SEC02. Checagens que exigem aparelho, ADB ou o ledger ao vivo permanecem unverified.
