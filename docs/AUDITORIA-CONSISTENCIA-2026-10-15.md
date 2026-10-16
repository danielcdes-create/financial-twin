# Auditoria de consistência — 15/10/2026

Base conferida: release privado **1.20.58 / versionCode 104**, HEAD `26ebfe8`, antes das correções desta entrega. A leitura chamou os helpers e as funções de API que Início, Extrato, cartão, Detalhe, A conciliar e cronograma já usam, com dados sintéticos. O ledger ao vivo não foi escrito, deduplicado nem reparado.

Cada linha é `fixed` (com o teste que chama a função entregue) ou `blocked` (com a mudança proibida).

## Dinheiro (centavos e sinal)

| Par | Resultado |
| --- | --- |
| Início (`income`, `expenses`, `net_flow`, categorias) e o extrato econômico | fixed. O Início mostra os totais da API. A soma das categorias é `expenses`, e o recorte econômico devolve o mesmo centavo. Teste `test_every_dashboard_category_matches_its_economic_drilldown` e `test_no_category_truncation_beyond_eight_or_twelve` em `backend/tests/test_filter_consistency.py`. |
| Sinal da linha do Extrato | fixed. A linha conserva o `valor` guardado, negativo na saída. Teste `test_spending_pagination_filters_before_count_and_page`. |
| Obrigação parcelada em cartão, Detalhe, A conciliar e cronograma contra `schedule` | fixed. Total 100 em 3 parcelas: 33,33 + 33,33 + 33,34. O cronograma da obrigação omitia o “Pago (assumido)” e, sem `actual_amount`, o detalhe caía no total. Os dois lados passam a usar `settledRows`, que chama `obligationShare`. Testes `test_obligation_remainder_stays_on_the_last_parcel`, `obligationShare` e `settledRows` / `obligation-settled` em `mobile/scripts/test-commitment-lists.cjs`. |
| Parcelamento no Extrato: parcela 1 e total só no detalhe | fixed. Lista e detalhe usam `parcelamentoListMark` / `parcelamentoPurchaseSummary`. Testes `parcelamentoListMark` em `mobile/scripts/test-filter-consistency.cjs` e `test_statement_detail_carries_the_plan_total_and_count`. Guardar um total de banco diferente de parcela × quantidade: blocked. Seria mudança semântica do ledger. |

## Data civil (America/Sao_Paulo)

| Par | Resultado |
| --- | --- |
| Extrato (`financialDateLabel`) e `local_date` | fixed. Instante `2026-10-01T02:30:00Z` é 30/09/2026 em Brasília. Data civil `AAAA-MM-DD` não anda. Testes `financialDateLabel` em `mobile/scripts/test-adversarial-fixes.cjs` e `test_B06_civil_deadlines_keep_day_while_events_follow_brasilia`. |
| Vencimento de compromisso na API e no cliente | fixed. `commitment_output` devolve só a data civil. Teste `test_dates_are_bson_dates_but_api_keeps_date_only_format`. |
| Mês do Início e o dia civil de Compromissos | fixed. `civilMonthWindow` usa Brasília. Teste `civilMonthWindow` em `mobile/scripts/test-commitment-lists.cjs`. |

## Classificação armazenada

| Par | Resultado |
| --- | --- |
| Extrato: natureza, categoria e subcategoria contra `NATURE_LABELS` | fixed. `classificationLine` mostra natureza, categoria › subcategoria e método. Transferência sem as duas contas distintas não esconde a categoria. Teste `classificationLine` em `mobile/scripts/test-filter-consistency.cjs`. |
| Método e conta/cartão no Extrato, cartão, Detalhe, mês e cronograma | fixed. O código `cartao` aparecia como “Cartão” em `paymentMethodLabel` e como “Cartão de crédito” em `settlementLabel`; `cartao_credito` vazava cru no mês. Um mapa só, `paymentMethodLabel`, alimenta Extrato, cartão, Detalhe e a linha do mês. Código desconhecido fica em branco no Extrato, no cartão e no mês. `paymentLabel` usa o mesmo mapa que `paymentMethodLabel`. A conta só entra quando o cadastro existe. Testes `paymentMethodLabel`, `paymentLabel`, `chargePaymentLabel` e `obligation-settled` em `mobile/scripts/test-commitment-lists.cjs`, mais `classificationLine` e `ownAccountTransfer` em `mobile/scripts/test-filter-consistency.cjs`. |
| Categoria no Início | fixed. O Início mostra `category_spending` da API, sem um segundo rótulo. Teste `test_every_dashboard_category_matches_its_economic_drilldown`. |
| Natureza, subcategoria, método e conta/cartão no Início | fixed. O Início não reescreve esses campos nem inventa método ou conta. O rótulo guardado fica no Extrato, em `classificationLine` e `ownAccountTransfer`, em `mobile/scripts/test-filter-consistency.cjs`. |
| Natureza, categoria, subcategoria, método e conta/cartão em A conciliar e no cronograma | fixed. Essas telas não reclassificam o lançamento; mostram a cobrança de `pendingOccurrences` / `settledRows`. Testes `check('reconcile')` e `obligation-settled` em `mobile/scripts/test-commitment-lists.cjs`. |

## Apresentação de compromissos

| Par | Resultado |
| --- | --- |
| Corte fixo 2026-10-01 em cartão, Detalhe, A conciliar e cronograma | fixed. “Pago (assumido)” não acompanha o mês. O cronograma da obrigação gerida agora lista a mesma parcela assumida que o detalhe, com o mesmo centavo. Testes `test_fixed_cutoff_marks_later_unlinked_dues_overdue`, `occurrenceState` e `settledRows` / `obligation-settled`. Corte móvel nessas mesmas telas: blocked. A mudança proibida é um corte que acompanha o mês. |
| Próxima cobrança (cartão, Detalhe, A conciliar, cronograma) contra `schedule` | fixed. `anchoredDue` segue `schedule` (31/01, 28/02, 31/03). Testes `test_month_end_schedule_and_finite_count`, `anchoredDue`, `nextOpenDue` e `pendingOccurrences` em `test-commitment-lists.cjs`. |
| Cobrança depois de `end_date` | fixed. O mês escolhido permanece; o seguinte sai. Testes `test_end_date_keeps_the_chosen_month_and_drops_the_next` e `nextOpenDue` com `end_date` em `mobile/scripts/test-obligation-edits.cjs`. |
| `future_cutoff` | fixed. O dia do corte e os seguintes saem nos dois lados. Testes `test_future_cutoff_drops_the_cutoff_day_and_later_charges` e `pendingOccurrences` com `future_cutoff`. |
| Vínculo histórico que esvazia a agenda | fixed. A API esvazia a agenda; o cliente não pula para o mês seguinte. Testes `test_historical_link_on_the_next_charge_empties_the_schedule` e `nextOpenDue` nulo em `test-commitment-lists.cjs`. |
| Parcela 1 na data da compra; total do plano só no detalhe | fixed. A linha do extrato é a primeira parcela. O total não substitui o valor da linha. Testes `test_commitment_purchase_is_one_confirmed_line_and_does_not_settle`, `test_statement_detail_carries_the_plan_total_and_count` e `parcelamentoPurchaseSummary`. |

## blocked

E1 (editar parte das parcelas futuras de um compromisso gerido): blocked. A mudança proibida é editar só uma parte das parcelas futuras de um compromisso gerido. Comportamento novo de obrigação pontual: blocked. A mudança proibida é um comportamento novo de obrigação pontual; o tipo `obrigacao` já existe. Corte móvel: blocked. A mudança proibida é um corte que acompanha o mês. Schema ou semântica do ledger: blocked. A mudança proibida é alterar o schema ou o sentido de um lançamento. Nenhuma coleção financeira foi alterada.

## Pares conferidos

O ledger ao vivo não foi escrito, deduplicado nem reparado.

- Dinheiro, centavos e sinal, Início contra a API: fixed. Teste `test_every_dashboard_category_matches_its_economic_drilldown`.
- Dinheiro, centavos e sinal, Extrato contra o `valor` guardado: fixed. Teste `test_spending_pagination_filters_before_count_and_page`.
- Dinheiro, centavos e sinal, cartão, Detalhe, A conciliar e cronograma contra `schedule`: fixed. Testes `obligationShare`, `settledRows` e `obligation-settled` em `mobile/scripts/test-commitment-lists.cjs`.
- Data civil America/Sao_Paulo, Extrato contra `local_date`: fixed. Testes `financialDateLabel` e `test_B06_civil_deadlines_keep_day_while_events_follow_brasilia`.
- Data civil America/Sao_Paulo, vencimento da API contra o cliente: fixed. Teste `test_dates_are_bson_dates_but_api_keeps_date_only_format`.
- Data civil America/Sao_Paulo, mês do Início: fixed. Teste `civilMonthWindow`.
- Classificação natureza, categoria e subcategoria, Extrato: fixed. Teste `classificationLine`.
- Classificação natureza, subcategoria, método e conta/cartão, Início: fixed. O Início não inventa método nem conta. Testes `test_every_dashboard_category_matches_its_economic_drilldown`, `classificationLine` e `ownAccountTransfer`.
- Classificação natureza, categoria e subcategoria, cartão e Detalhe: fixed. Testes `check('grouping')` e `check('detail-open')` em `mobile/scripts/test-commitment-lists.cjs`.
- Classificação método e conta/cartão, Extrato, cartão, Detalhe, A conciliar e cronograma: fixed. Testes `paymentMethodLabel`, `chargePaymentLabel`, `classificationLine` e `ownAccountTransfer`.
- Corte fixo 2026-10-01: fixed. Testes `test_fixed_cutoff_marks_later_unlinked_dues_overdue` e `settledRows`.
- Corte móvel: blocked. A mudança proibida é um corte que acompanha o mês.
- Próxima cobrança contra `schedule`: fixed. Testes `test_month_end_schedule_and_finite_count` e `anchoredDue`.
- `end_date`: fixed. Teste `test_end_date_keeps_the_chosen_month_and_drops_the_next`.
- `future_cutoff`: fixed. Teste `test_future_cutoff_drops_the_cutoff_day_and_later_charges`.
- Vínculo histórico que esvazia a agenda: fixed. Teste `test_historical_link_on_the_next_charge_empties_the_schedule`.
- Parcela 1 na data da compra e total só no detalhe: fixed. Testes `test_commitment_purchase_is_one_confirmed_line_and_does_not_settle` e `parcelamentoPurchaseSummary`.
- E1: blocked. A mudança proibida é editar parte das parcelas futuras de um compromisso gerido.
- Obrigação pontual nova: blocked. A mudança proibida é comportamento novo de obrigação pontual.
- Schema ou semântica do ledger: blocked. A mudança proibida é alterar o schema ou o sentido de um lançamento.
