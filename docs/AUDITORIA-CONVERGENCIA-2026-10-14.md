# Auditoria de convergência — 14/10/2026

Base conferida antes das correções: release privado **1.20.49 / versionCode 95**. As correções foram publicadas em **1.20.50 / versionCode 96**. A leitura comparou, com dados sintéticos, o que a API calcula e o que o cliente mostra em Início, Extrato, cartão de Compromissos, Detalhe, A conciliar e cronograma. Não houve escrita, deduplicação nem reparo no ledger ao vivo.

Cada linha é `fixed` (com o teste que chama a função entregue) ou `blocked` (com a mudança proibida).

## Dinheiro (centavos e sinal)

| Par | Resultado |
| --- | --- |
| Início (`income`, `expenses`, `net_flow`, categorias) e o extrato econômico | fixed. O Início mostra os totais da API. A soma das categorias é `expenses`, e o recorte econômico devolve o mesmo centavo. Teste `test_every_dashboard_category_matches_its_economic_drilldown` e `test_no_category_truncation_beyond_eight_or_twelve` em `backend/tests/test_filter_consistency.py`. |
| Sinal da linha do Extrato | fixed. A linha conserva o `valor` guardado, negativo na saída. Saída no resumo é magnitude positiva (`effects`). Teste `test_spending_pagination_filters_before_count_and_page`. |
| Obrigação parcelada em cartão, Detalhe e cronograma contra `schedule` | fixed. Total 100 em 3 parcelas: 33,33 + 33,33 + 33,34. Testes `test_obligation_remainder_stays_on_the_last_parcel` e `obligationShare` em `mobile/scripts/test-commitment-lists.cjs`. |
| Parcelamento no Extrato: parcela 1 e total só no detalhe | fixed. A lista usava `installment_count` da linha e podia divergir do plano. Agora lista e detalhe usam `parcelamentoListMark` / `parcelamentoPurchaseSummary`. Testes `parcelamentoListMark` em `mobile/scripts/test-filter-consistency.cjs` e `test_statement_detail_carries_the_plan_total_and_count`. Guardar um total de banco diferente de parcela × quantidade: blocked. Seria mudança semântica do ledger. |

## Data civil (America/Sao_Paulo)

| Par | Resultado |
| --- | --- |
| Extrato (`financialDateLabel`) e `local_date` | fixed. Instante `2026-10-01T02:30:00Z` é 30/09/2026 em Brasília. Data civil `AAAA-MM-DD` não anda. Testes `financialDateLabel` em `mobile/scripts/test-adversarial-fixes.cjs` e `test_B06_civil_deadlines_keep_day_while_events_follow_brasilia`. |
| Vencimento de compromisso na API e no cliente | fixed. `commitment_output` devolve só a data civil. Teste `test_dates_are_bson_dates_but_api_keeps_date_only_format`. |
| Mês do Início e o dia civil de Compromissos | fixed. `currentMonthWindow` usava o fuso do aparelho. `civilMonthWindow` usa Brasília. Teste `civilMonthWindow` em `mobile/scripts/test-commitment-lists.cjs`. |

## Classificação armazenada

| Par | Resultado |
| --- | --- |
| Extrato: natureza, categoria e subcategoria contra `NATURE_LABELS` | fixed. `classificationLine` mostra natureza, categoria › subcategoria e método. Transferência sem as duas contas distintas não esconde a categoria. Teste `classificationLine` em `mobile/scripts/test-filter-consistency.cjs`. |
| Transferência entre contas próprias no Extrato | fixed. A linha dizia “Transferência neutra” e a API diz “Transferência Neutra”. Teste `classificationLine` no mesmo script. |
| Método e conta/cartão no Extrato | fixed. O método usa o código guardado (`paymentLabel`). A conta só entra quando o cadastro existe. Teste `classificationLine` para o método e `ownAccountTransfer` para não inventar a seta sem as duas chaves. |

## Apresentação de compromissos

| Par | Resultado |
| --- | --- |
| Corte fixo 2026-10-01 em cartão, Detalhe, A conciliar e cronograma | fixed. “Pago (assumido)” não acompanha o mês. Testes `test_fixed_cutoff_marks_later_unlinked_dues_overdue` e `occurrenceState` / `reconcileCharge` em `mobile/scripts/test-commitment-lists.cjs`. Corte móvel nessas mesmas telas: blocked. A mudança proibida é um corte que acompanha o mês. |
| Próxima cobrança (cartão, Detalhe, A conciliar, cronograma) contra `schedule` | fixed. O cliente perdia o dia-âncora depois de um mês curto. `anchoredDue` segue `schedule` (31/01, 28/02, 31/03). Testes `test_month_end_schedule_and_finite_count`, `anchoredDue`, `nextOpenDue` e `pendingOccurrences` em `test-commitment-lists.cjs`. |
| Cobrança depois de `end_date` em cartão, Detalhe, A conciliar e cronograma | fixed. O mês escolhido permanece; o seguinte sai. Testes `test_end_date_keeps_the_chosen_month_and_drops_the_next` e `nextOpenDue` com `end_date` em `mobile/scripts/test-obligation-edits.cjs`. |
| `future_cutoff` em cartão, Detalhe, A conciliar e cronograma | fixed. O dia do corte e os seguintes saem nos dois lados. Testes `test_future_cutoff_drops_the_cutoff_day_and_later_charges` e `pendingOccurrences` com `future_cutoff`. |
| Conflito de vínculo histórico em cartão, Detalhe, A conciliar e cronograma | fixed. A API esvazia a agenda; o cliente não pula para o mês seguinte. Testes `test_historical_link_on_the_next_charge_empties_the_schedule` e `nextOpenDue` nulo em `test-commitment-lists.cjs`. |
| Parcela 1 na data da compra; total do plano só no detalhe | fixed. A linha do extrato é a primeira parcela. O total não substitui o valor da linha. Testes `test_commitment_purchase_is_one_confirmed_line_and_does_not_settle`, `test_statement_detail_carries_the_plan_total_and_count` e `parcelamentoPurchaseSummary`. |

## blocked

E1 (editar parte das parcelas futuras de um compromisso gerido): blocked. A mudança proibida é editar só uma parte das parcelas futuras de um compromisso gerido. O1 (obrigação pontual nova): blocked. A mudança proibida é um comportamento novo de obrigação pontual; o tipo `obrigacao` já existe. Corte móvel: blocked. A mudança proibida é um corte que acompanha o mês. Schema ou semântica do ledger: blocked. A mudança proibida é alterar o schema ou o sentido de um lançamento. Nenhuma coleção financeira foi alterada.

## Pares conferidos

O ledger ao vivo não foi escrito, deduplicado nem reparado.

- Dinheiro, centavos e sinal, Início contra a API: fixed. Teste `test_every_dashboard_category_matches_its_economic_drilldown`.
- Dinheiro, centavos e sinal, Extrato contra o `valor` guardado: fixed. Teste `test_spending_pagination_filters_before_count_and_page`.
- Dinheiro, centavos e sinal, cartão, Detalhe, A conciliar e cronograma contra `schedule`: fixed. Testes `obligationShare`, `commitmentFigures` em `check('pending-v6')` e `check('reconcile')` de `mobile/scripts/test-commitment-lists.cjs`.
- Dinheiro, centavos e sinal, A conciliar: fixed. A lista usa o valor de `pendingOccurrences`, o mesmo centavo da cobrança. Teste `check('reconcile')` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, Início contra o dia de Compromissos: fixed. Teste `civilMonthWindow` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, Extrato contra `local_date`: fixed. Testes `financialDateLabel` em `mobile/scripts/test-adversarial-fixes.cjs` e `test_B06_civil_deadlines_keep_day_while_events_follow_brasilia`.
- Data civil America/Sao_Paulo, cartão, Detalhe, A conciliar e cronograma contra `as_date` / `schedule`: fixed. Testes `test_dates_are_bson_dates_but_api_keeps_date_only_format` e `anchoredDue` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, cartão: fixed. A data do cartão é `anchoredDue`. Teste `anchoredDue` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, Detalhe: fixed. A data do detalhe é `anchoredDue`. Teste `anchoredDue` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, A conciliar: fixed. A data de A conciliar é `anchoredDue`. Teste `anchoredDue` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, cronograma: fixed. A data do cronograma é `anchoredDue`. Teste `anchoredDue` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação natureza, categoria e subcategoria, Extrato contra `NATURE_LABELS`: fixed. Teste `classificationLine` em `mobile/scripts/test-filter-consistency.cjs`.
- Classificação método e conta/cartão, Extrato: fixed. Testes `classificationLine` e `ownAccountTransfer` em `mobile/scripts/test-filter-consistency.cjs`.
- Classificação categoria, Início contra a API: fixed. O Início mostra `category_spending` da API, sem um segundo rótulo. Teste `test_every_dashboard_category_matches_its_economic_drilldown`.
- Classificação natureza, subcategoria, método e conta/cartão, Início: fixed. O Início não reescreve esses campos; o extrato é quem os mostra. Teste `classificationLine` em `mobile/scripts/test-filter-consistency.cjs`.
- Classificação método e conta/cartão, Início: fixed. O Início não inventa método nem conta; o rótulo guardado fica no extrato. Teste `classificationLine` e `ownAccountTransfer` em `mobile/scripts/test-filter-consistency.cjs`.
- Classificação método e cartão, cartão e Detalhe contra o código guardado: fixed. Teste `check('grouping')` em `mobile/scripts/test-commitment-lists.cjs` (`Débito`, sem o código `debito`).
- Classificação método e conta/cartão, cartão: fixed. O cartão mostra o método guardado e não inventa conta. Teste `check('grouping')` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação método e conta/cartão, Detalhe: fixed. O detalhe mostra o método guardado e não inventa conta. Teste `check('detail-open')` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação natureza, categoria e subcategoria, cartão e Detalhe: fixed. Essas telas mostram o tipo e o pagamento do compromisso, sem uma segunda natureza de extrato. Testes `check('grouping')` e `check('detail-open')` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação natureza, categoria, subcategoria, método e conta/cartão, A conciliar e cronograma: fixed. Essas telas não reclassificam o lançamento; mostram a cobrança de `pendingOccurrences`. Teste `check('reconcile')` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação método e conta/cartão, A conciliar: fixed. A conciliar não inventa método nem conta. Teste `check('reconcile')` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação método e conta/cartão, cronograma: fixed. O cronograma não inventa método nem conta. Teste `check('reconcile')` em `mobile/scripts/test-commitment-lists.cjs`.
- Apresentação de compromisso no Início (corte, próxima cobrança e `end_date`): fixed. O gráfico usa `summary` da API e não recalcula o corte. Testes `check('chart-accounts')` e `check('totals')` em `mobile/scripts/test-commitment-lists.cjs`.
- Apresentação de compromisso no Extrato (corte, próxima cobrança e `end_date`): fixed. O extrato não recalcula a agenda; a apresentação nessa tela é a parcela 1 na data da compra. Testes `test_commitment_purchase_is_one_confirmed_line_and_does_not_settle` e `parcelamentoPurchaseSummary`.
- Corte fixo 2026-10-01, cartão, Detalhe, A conciliar e cronograma contra `presentation_state`: fixed. Testes `test_fixed_cutoff_marks_later_unlinked_dues_overdue`, `occurrenceState` e `check('reconcile')`.
- Corte móvel nessas mesmas telas: blocked. A mudança proibida é um corte que acompanha o mês.
- Próxima cobrança, cartão, Detalhe, A conciliar e cronograma contra `schedule`: fixed. Testes `test_month_end_schedule_and_finite_count`, `anchoredDue`, `nextOpenDue` e `check('reconcile')`.
- Cobrança depois de `end_date`, cartão, Detalhe, A conciliar e cronograma: fixed. Testes `test_end_date_keeps_the_chosen_month_and_drops_the_next` e `nextOpenDue` com `end_date` em `mobile/scripts/test-obligation-edits.cjs`.
- Linha de parcelamento no Extrato, parcela 1 na data da compra, total só no detalhe: fixed. Testes `test_commitment_purchase_is_one_confirmed_line_and_does_not_settle`, `test_statement_detail_carries_the_plan_total_and_count` e `parcelamentoPurchaseSummary`.
- Total de banco diferente de parcela × quantidade: blocked. A mudança proibida é semântica de ledger.
