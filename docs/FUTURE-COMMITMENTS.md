# Edição e exclusão de compromissos futuros

Atualização 1.20.33: um parcelamento concluído conserva `next_installment_number = installment_count + 1`, inclusive 601 após 600/600. Esse sentinela é aceito somente com `status=concluido`; editar observações preserva pagamentos, contador e datas. Não há próxima cobrança e não é permitido reativar esse cadastro sem corrigir explicitamente sua condição. Os cronogramas não projetam cobranças de compromisso concluído.

Em 409, o editor compara origem/local/remoto por identidade da parcela e por campo. Alterações disjuntas podem ser mescladas; valor, data ou cancelamento sobrepostos exigem escolha. Parcela paga/removida bloqueia reaplicação. Recargas repetidas e conflitos consecutivos conservam as decisões ainda pendentes. O token remoto só autoriza o patch conferido. [Validação atual](RECUPERACAO-ANDROID-2026-10-09.md).

## Gráfico mensal de compromissos

Em **Mais → Assinaturas e parcelamentos**, o gráfico **Compromissos por mês** mostra quanto já está previsto em cada mês, antes de novas compras. O período automático vai do mês corrente até a última parcela futura, incluindo esse último mês. Há opções de 6, 12 e 24 meses e quantidade personalizada entre 1 e 120 meses. Sem compras parceladas futuras, o padrão é 12 meses; horizontes automáticos maiores são limitados a 120 meses com aviso. A leitura de 2026-10-08 encontrou última parcela em janeiro de 2028, resultando em 16 meses; a data é recalculada na leitura.

As barras separam parcelas, assinaturas e outras obrigações, incluindo financiamento. Tocar em um mês mostra os valores por grupo e as cobranças que compõem o total. Meses sem previsão aparecem com zero. O mês corrente contém somente o restante previsto desde o dia da leitura; uma parcela com somente competência mantém a unidade mensal. Pagamentos, parcelas faturadas, cancelamentos, exclusões futuras e correções de cronograma seguem os contratos existentes. Obrigações vinculadas explicitamente a planos não duplicam suas parcelas.

Faturas conhecidas aparecem separadamente no detalhe, pelo saldo em aberto, e não são somadas às barras, pois podem conter as compras e assinaturas já previstas. Este gráfico representa os cadastros de compromissos e não substitui o saldo de caixa ou a lista de dívidas vencidas. Vencidos anteriores não são trasladados para meses futuros. Contas variáveis usam a média disponível na leitura em todo o horizonte; não se prediz a variação futura de consumo nem reajustes ou novas compras. Problemas de agenda, regra ou fonte são informados junto ao gráfico.

`GET /v1/commitment-projection?months=24` calcula a projeção em snapshot e devolve meses, composição, faturas separadas, último mês de parcelas e avisos. Omitir `months` usa o período automático. Valores são agregados em Decimal e transmitidos como texto. O endpoint não grava nem avança agendas. A UI preserva a identificação do período exibido durante recargas, ignora respostas antigas e conserva o período escolhido após alterações nos cadastros.

Validação: suíte completa com 452 testes do backend, mais a regressão HTTP adicional de autenticação e limites do período; oito testes específicos da projeção passaram. Os 48 scripts do cliente, TypeScript e lint passaram, com 32 cenários no script de transições de domínio. O componente real foi renderizado em 320 e 390 px, sem transbordamento horizontal, e exercitado com seleção de mês e período personalizado. Evidências em `../../audit_work/commitment-projection-2026-10-08/` e logs `../../audit_work/commitment-projection-{backend,client,domain}-2026-10-08.log`. Consulta ao banco real somente para leitura da projeção; nenhuma escrita financeira. Sem novo APK, reinício do backend ou validação física Android nesta etapa.

Entrega Android solicitada depois: versão **1.20.31 / código 77**, ARM64, gerada em 2026-10-08. Assinatura compatível com 1.20.30; 83 fontes do bundle verificadas contra o checkout, incluindo o gráfico. APK e download do atualizador conferem em tamanho e SHA-256. O worker local foi retomado pelo supervisor existente; a rota do gráfico está disponível, `/health` confirmou MongoDB conectado e a chamada sem sessão foi recusada com 401. Atualização disponível em **Mais → Ajustes → Atualizações**. Nenhum aparelho ADB conectado; instalação e comportamento físico permanecem sem validação. Evidências em `../../audit_work/apk-1.20.31/release-verification.json`.

## Contas variáveis — 1.20.30 / código 76

Cemig, Supergasbras, Claro e TIM podem usar uma previsão mensal pela média das três últimas contas confirmadas, com arredondamento de centavos. A regra é conferida por fornecedor e conta bancária e fica em `commitment_amount_rules`, separada do histórico. Não é ativada automaticamente para outros fornecedores. A API calcula o valor na leitura, incluindo extratos importados e capturas confirmadas; cada nova conta elegível substitui a mais antiga da amostra. Meses sem registro não contam como zero. Sem três contas elegíveis, conserva o valor de referência e informa a necessidade de conferência. Capturas ainda pendentes, estornos, transferências, moeda estrangeira, outra conta e capturas já vinculadas ao mesmo lançamento não entram na média.

Em **Mais → Assinaturas e parcelamentos → Editar compromisso**, a opção **Previsão do valor** permite escolher **Média das 3 últimas contas** ou **Valor fixo**. O valor calculado não é editável enquanto a média estiver selecionada. Datas, conta, observações e status continuam sujeitos à edição e à revisão concorrente; trocar a conta exige revisar a regra ou usar valor fixo. Clientes anteriores que omitem `amount_mode` preservam o modo existente e os identificadores de comandos pendentes.

O usuário pode selecionar **Cancelado** para encerrar a previsão, **Pausado** para suspendê-la ou **Excluir futuras** para retirar as cobranças futuras do cadastro, preservando pagamentos e documentos históricos. Essa exclusão usa o mesmo corte de datas dos demais compromissos gerenciados. Não representa cancelamento no fornecedor. A média não registra nem liquida pagamentos e não avança a agenda automaticamente; o vínculo de uma saída realizada continua sendo uma ação explícita.

Lista, dashboard, prévia de exclusão e planejamento de caixa usam o mesmo valor efetivo. Fechamento de um mês usa apenas contas pagas até o fim daquele mês, sem incorporar cobranças posteriores. Alertas não tratam uma média variável como preço contratual fixo. Total Pass permanece um compromisso mensal separado; o encerramento de Smart Fit não remove suas despesas passadas. Um cadastro cancelado, como ChatGPT, não projeta novas cobranças.

### Auditoria de 2026-10-08

Editar um compromisso com média automática conserva o valor de referência armazenado; o valor calculado na leitura não substitui essa referência. Omissão ou `null` em `amount_mode` conserva o modo existente. A validação da regra também se aplica aos clientes antigos: mudar conta, frequência ou tipo exige uma configuração válida ou a escolha explícita de valor fixo. Regra ausente permanece sinalizada como necessidade de conferência. A interpretação de voz respeita o bloqueio do valor calculado.

A média e a busca de pagamentos conferem a mesma identidade de conta. Capturas identificadas somente por perfil bancário entram apenas quando há uma única conta importada correspondente; contas ambíguas, identidade divergente e moeda estrangeira ficam fora da amostra e das candidatas automáticas. A busca aceita IDs importados em BSON ou texto e nomes abreviados dos fornecedores previstos na regra. Regra inválida retorna conflito para conferência, sem falha interna. O seletor conserva a referência importada ou de perfil já usada pelo compromisso.

Validação desta auditoria: 445 testes de backend, 48 scripts do cliente e repetição dos 29 cenários de transição de domínio após os ajustes finais. Os testes usam dados sintéticos e serviços simulados; não houve migração ou correção do histórico financeiro real. Logs em `../../audit_work/cards-commitments-backend-2026-10-08.log`, `../../audit_work/cards-commitments-client-2026-10-08.log` e `../../audit_work/cards-commitments-domain-2026-10-08.log`. Esta etapa não gera APK, não reinicia o serviço e não valida comportamento físico Android.

Android 1.19.6 / código 42. Mais → Compromissos mantém a edição dos cadastros manuais e adiciona “Excluir futuras”. Parcelamentos importados exibem “Editar futuras” e “Excluir futuras”; o editor permite alterar valor e vencimento ou marcar uma parcela individual para excluir. Uma confirmação mostra as alterações antes de gravar. A seção de obrigações futuras inclui financiamento e outras obrigações importadas, em páginas de dez registros.

Somente parcelas projetadas sem transação/fatura vinculada podem ser alteradas. Parcelas pagas, faturadas e com mês anterior ao atual são preservadas. Uma projeção com apenas competência usa o mês como unidade temporal, incluindo o mês corrente; o app não inventa o dia de vencimento. A data exata é opcional nesse caso. Se fornecida, deve ser hoje ou futura. Obrigações exigem status futura, vencimento hoje/futuro, valor pago zero e ausência de reconciliação. Excluir uma previsão no app não cancela a obrigação no banco ou fornecedor.

Correções dos importados ficam na coleção adicional `future_schedule_overrides`; documentos originais em `installment_plans`, `obligations` e `transactions` não são reescritos. Os endpoints de cronograma, o total projetado do dashboard e os fluxos de obrigações da simulação usam a mesma visão corrigida. Alterar o saldo projetado da obrigação não recalcula o principal, juros, seguros ou contrato: é uma correção da previsão de caixa. Excluir retira a previsão dos cálculos. Correções permanecem após o mês passar. Se a fonte depois registrar pagamento ou mudar a linha original, a informação nova prevalece e uma indicação pede conferir a correção antiga.

Excluir futuras de um cadastro manual guarda um corte de datas, mantém seu histórico e pagamentos vinculados e o retira da lista ativa. Ocorrências vencidas anteriores ao corte continuam preservadas; novas renovações após o corte não são projetadas. Editar pelo formulário existente conserva os valores efetivamente pagos que estão nos vínculos.

O vínculo de pagamento identifica a ocorrência pelo vencimento (`occurrence_key=due:AAAA-MM-DD`) e conserva separadamente `paid_date`; parcelas também guardam o índice liquidado. Janeiro pago em fevereiro não impede liquidar fevereiro com outra despesa no mesmo mês. A mesma movimentação e a mesma ocorrência permanecem exclusivas. Vínculos antigos que só informam pagamento/mês continuam preservados e exigem conferência quando sua ocorrência é ambígua; não são migrados por heurística.

Se mais de uma ocorrência futura cabe na janela de sete dias do pagamento, a interface exige confirmação explícita do vencimento da próxima cobrança. Para liquidar uma cobrança posterior, é necessário conferir primeiro a agenda. Pagamento histórico fora da janela não avança automaticamente a próxima cobrança e fica marcado quando a identidade da ocorrência não foi informada. A última cobrança de uma assinatura com término consome a previsão e conclui o cadastro; pagamento antecipado não volta a gerar dívida em Compromissos, planejamento ou fechamento. Uma renovação manual com vencimento posterior ainda dentro do término conserva a pausa para decisão de renovação.

Quando informado explicitamente, o vencimento de um vínculo `historical_only` precisa ser anterior à próxima cobrança. A próxima cobrança deve ser liquidada pelo vínculo normal, que avança a agenda. Se um documento já contém vínculo histórico explícito para ocorrência vigente ou futura, a leitura preserva esse documento e sinaliza `payment_occurrence_requires_review`; o checklist impede concluir a conferência de compromissos, o total futuro fica indefinido e a projeção de caixa exclui a fonte até revisar a agenda. Novos vínculos recebem 409 enquanto essa contradição permanece. Não se corrige contador, data ou história por inferência.

Prévia e gravação verificam a fonte, a revisão das correções e o dia civil de Brasília. Cada confirmação tem identificador próprio, reutilizado se a resposta se perder; o mesmo identificador com outro conteúdo é rejeitado. Correção, auditoria e confirmação são gravadas em uma transação MongoDB. Edições simultâneas têm um vencedor e um conflito 409, que exige reabrir e conferir. Uma falha de auditoria desfaz toda a gravação. O histórico de auditoria guarda o documento original, a visão antes da mudança e a revisão confirmada. Importações externas não participam da mesma transação do app; a visão corrigida confere novamente status, referências e identidade da linha ao ler.

Validação: 174 testes Python, incluindo proteção de histórico/pagamento, centavos, vencimento, fonte alterada, persistência após mudança de mês, idempotência e rollback; 22 verificações HTTP em MongoDB real em banco sintético separado; interface com APIs simuladas em 390 e 320 px, incluindo parcela individual, exclusão total, cancelamento, resposta perdida, edição de cadastro manual e financiamento. Typecheck, lint e export web passaram. Evidências em `../../audit_work/future-commitments-2026-10-01`. A assinatura e o download local do APK são conferidos antes da publicação; instalação em aparelho físico permanece sem validação.

Auditoria 1.19.7 / código 43: a elegibilidade temporal segue o vencimento corrigido, inclusive após passar o vencimento original. A lista de futuras é ordenada pela data efetiva; uma correção vencida sai dessa lista, mantendo sua pendência no planejamento. Cronogramas importados e lista de obrigações leem fonte/correções em snapshot. A busca de cobranças manuais começa no período atual, evitando omitir assinaturas antigas por consumir o limite com histórico. Falha de uma seção mantém as demais e exibe aviso com nova tentativa; alterações da fonte importada são sinalizadas nas obrigações. Resultado e limites em [auditoria de consistência e convergência](../../audit_work/consistency-convergence-2026-10-01/RELATORIO.md).

## Redesign de Compromissos — 11/10/2026

Android e Web são o mesmo cliente Expo. O cálculo mensal continua em `monthly_projection`. O vínculo com o extrato continua em `payment-candidates` e no POST de pagamento, sempre com confirmação. Conciliar uma cobrança importada, como um financiamento, abre a mesma busca do extrato ordenada pelo valor e pela data. O botão **Vincular este lançamento** grava o vínculo na correção do app, sem reescrever o plano importado. A cobrança sai de “A conciliar” e da projeção. Desfazer remove só esse vínculo. O agrupamento e o desfazer são leitura ou reversão do que já existe: `external_key`, `future_schedule_overrides` e o corte `future_cutoff`. Não houve migração nem campo novo.

| Item | Decisão | Motivo |
| --- | --- | --- |
| R1 | aceitar | A série já está no `external_key` (`parcela-N` ou `AAAA-MM`). A lista agrupa na leitura e no cliente. O cronograma abre sob demanda, virtualizado. Os documentos de plano, obrigação e pagamento não são reescritos. |
| R2 | adaptar | Não há coleção nova de soft-delete. A confirmação diz quantas parcelas futuras e o valor. Desfazer reverte o override ou o corte já gravado no comando. Pagamentos vinculados não entram na reversão. |
| R3 | adaptar | Cada ocorrência mostra Previsto, Vencido sem pagamento ou Pago vinculado. O vencido sem vínculo fica em “A conciliar” e confirma um único candidato em dois toques. Não vincula sozinho. A sugestão inversa no Extrato fica de fora: o detalhe da movimentação não tem esse encaixe e a aceitação não exige. |
| R4 | adaptar | Os filtros Assinaturas, Contas, Parcelas e Dívidas saem de `kind`, `tipo` e `amount_mode`. As barras continuam Parcelas, Assinaturas e Outras obrigações. Conta variável segue na barra de assinaturas e no filtro Contas. |
| R5 | adaptar | O resumo “Falta pagar este mês” e “Próximos 12 meses” é a própria projeção (`summary` e, se o servidor antigo não enviar, as mesmas linhas). Os controles de 1 a 120 meses permanecem. O mês escolhido mostra a composição. Uma categoria pequena não some atrás do financiamento. |
| R6 | adaptar | Uma lista só, com filtros. “Concluídos” começa aberto para os parcelamentos sem parcela projetada continuarem visíveis. |
| R7 | descartar a gravação | O detalhe mostra o impacto com `future_pending_total` e deixa claro que encerrar no app não cancela o fornecedor. Manter / Avaliar / Cortar não é gravado. |
| R8 | adaptar | Teal na ação principal, âmbar no vencido, vermelho só na confirmação destrutiva, alvo de 48 dp nos controles tocados. O header “Conectado” e o FAB não mudam. |
| R9 | aceitar | “Débito”, “1 parcela” / “N parcelas”, “~” e “Estimado”, final de cartão sem repetição e nome cru como subtítulo quando já existe nome de exibição. Nada disso regrava o texto original. |

### Fatias

1. Um card por série, microcopy e ações de futuras com escopo e desfazer. A exclusão de uma série importada repete o DELETE já existente, uma identidade por ocorrência, e o desfazer repete o POST `/undo`. O botão Desfazer fica fora do card, porque o reload tira da lista o cadastro excluído, a obrigação `future_excluded` e o parcelamento que foi para Concluídos.
2. Estados e conciliação no próprio Compromissos.
3. Filtros derivados e resumo do gráfico, sem mudar a soma mensal.
4. Impacto de encerrar só como texto. O marcador de decisão não foi implementado.

Web e Android usam a mesma tela. Em largura a partir de 960 px a lista permanece ao lado do detalhe. Abaixo disso o cronograma abre na própria lista.

### Perguntas em aberto

1. Gravar Manter / Avaliar / Cortar exige campo novo. Não foi criado.
2. Se a taxonomia derivada de `kind`, `tipo` e `amount_mode` não bastar, um campo novo também precisa de decisão. Não foi criado.
3. A remoção de uma série longa hoje são N comandos já existentes. Um comando único no servidor mudaria o contrato. Não foi criado.

## Nota v2 — 11/10/2026

A nota v2 ajusta a apresentação. Não cria schema, migração nem campo. A v1 continua valendo onde esta seção não diz o contrário. O gráfico passa a separar Contas de Assinaturas; o total do mês continua a soma das séries, igual à classificação anterior em que a conta variável entrava em Assinaturas.

| Item | Decisão | O que ficou |
| --- | --- | --- |
| A1 | aceitar | Card ativo: nome, valor (com “~” se estimado), uma linha de meta e selo. O toque abre o detalhe. Explicações, vínculo zero, Editar e o botão largo saem da superfície. |
| A2 | adaptar | “Concluídos e cancelados (N)” começa recolhido. Linha compacta, sem ação primária. Vínculo manual no menu. Cancelado não entra em “A conciliar”: vencido só existe em cadastro ativo. Parcelamento com soma projetada zero vai para o grupo. |
| A3 | aceitar | Selo “Vencido · sem vínculo”, âmbar fora do teal. Vincular no card só se vencido, hoje ou nos próximos 7 dias. |
| A4 | aceitar | Plural, “Serviços essenciais”, “Itaú” e final de cartão uma vez. Nome cru segue como subtítulo. |
| B1 | adaptar | Cronograma em superfície própria, virtualizada, uma linha por parcela. “Previsto” não se repete. Vincular na linha só no cadastro gerenciado. |
| B2 | aceitar | Próxima parcela uma vez, restantes, última data e “soma das parcelas previstas”. |
| B3 | adaptar | Encerrar de cadastro gerenciado grava `status=cancelado`. Remover parcelas futuras continua no corte e no desfazer existentes, no diálogo. Escopo acima de 12 parcelas ou de R$ 5.000 pede um segundo passo. Financiamento importado não ganha status novo. |
| C1 | adaptar | Ordem: resumo, “A conciliar”, gráfico, filtros, lista. Linha compacta com candidato único quando a API já o devolve. Sem confirmação agregada de várias saídas. |
| C2 | adaptar | Seletor 6 / 12 / 24 / Tudo, com Tudo no horizonte automático. Personalizado, de 1 a 120, abre no sheet. A composição do mês abre em sheet na tela estreita e ao lado do gráfico na larga. |
| C3 | aceitar | Filtros numa linha horizontal. O ⓘ fica no cabeçalho da seção. |
| D1 | adaptar | Card, filtro, gráfico e detalhe usam `kind`, `tipo` e `amount_mode`. Conta variável sai de Assinaturas e entra em Contas. Dívidas é o rótulo da série que já era `other`. |

Pendências que esta nota não fecha: sugestão inversa no Extrato, colunas roláveis e marcos do gráfico, ordenação, e gravar Manter / Avaliar / Cortar. Homologação física continua sem aparelho. Android e Web saem do mesmo componente.

O cronograma abre numa camada da Activity, fora da rolagem da página e sem diálogo nativo.

## Nota v3 — 11/10/2026

A navegação passa a ser lista, detalhe e, a partir do detalhe, cronograma, edição e vínculo. O card não tem botões. A data de corte da apresentação ficou fixa em 11/10/2026 na nota seguinte: vencimentos anteriores aparecem como pago assumido e não entram em “A conciliar”. Não há data gravada nem linhas de passado materializadas. Financiamento importado só mostra as parcelas que o app já conhece. O tipo editável Conta/Dívida não foi criado. Não há comando para desfazer um vínculo; esse desfazer não foi inventado. A Web continua sem uma URL por tela. O cabeçalho do ano permanece quando as parcelas daquele ano são recolhidas. O cadastro gerenciado também abre o cronograma, e a linha vencida ou de hoje sem vínculo oferece Vincular pelo fluxo de candidatos já existente. A composição do mês só abre ao toque e pode ser fechada. Os chips de período rolam na horizontal. Um parcelamento concluído mostra o último valor já liquidado, não a soma zerada das parcelas projetadas. Encerrar e Excluir futuras de um parcelamento avisam que o contrato não foi quitado; o aviso de fornecedor fica na assinatura.

## Nota v4 — busca no extrato — 11/10/2026

O corte da apresentação é 11/10/2026, devolvido pelo servidor na leitura dos compromissos. Não há documento novo nem tela para editar essa data. A tela de vínculo consulta o extrato inteiro no servidor, por período, nome e valor. Os candidatos automáticos continuam como sugestões iniciais. A confirmação mostra a diferença entre o previsto e o lançamento. No detalhe do Extrato, uma saída ainda sem compromisso oferece “Vincular a compromisso”. Desvincular tem comando próprio: o desfazer imediato restaura a agenda gravada no vínculo; um vínculo posterior impede esse retorno e o app avisa. Trocar o lançamento altera só a movimentação do mesmo vínculo. O cronograma mostra o resumo, separa A pagar e Pagas, e marca projeção de 12 meses quando a renovação não tem fim. O formulário usa a máscara dd/mm/aaaa, porque o campo é do React Native e não um seletor de WebView. O gráfico classifica a média das três últimas contas como Conta, com o mesmo total do mês, e omite a série zerada. O tipo editável continua fora desta leva. A Web continua sem uma URL por tela.

## Nota v5 — obrigação pontual, corte fixo e término — 11/10/2026

Esta nota complementa a v4. Onde discordar, vale esta. Não reclassifica Condomínio nem cria o tipo editável Assinatura / Conta / Parcela / Dívida. Não apaga lançamento do extrato nem vínculo já gravado. Totais do mês, “Falta pagar este mês” e “Próximos 12 meses” não podem contar duas vezes a mesma obrigação. Android e Web saem do mesmo componente. A Web continua sem uma URL por tela.

### 1. Obrigação pontual

O formulário só oferece Assinatura e Compra parcelada. IPVA, IPTU e imposto semelhante não cabem nesses dois: não são renovação automática nem compra já contratada no cartão.

Aceitar um terceiro tipo, Obrigação pontual. O `kind` novo é `obrigacao`. É a única mudança de esquema desta nota. O valor guardado é o total. O parcelamento, quando existe, é hipotético: o app projeta as datas e não cria linha no extrato, não inventa pagamento e não quita o tributo no órgão.

O cadastro pede:

- nome;
- valor total;
- data à vista, o dia em que o total caberia numa cobrança só;
- se for parcelado, o número de parcelas (de 2 a 48) e a data a partir da qual o parcelamento hipotético começa.

Vale um plano de cada vez. À vista entra no mês da data à vista, com o total. Parcelado reparte o total em parcelas iguais a partir da data informada, uma por mês; o centavo que sobra fica na última. O plano que não está escolhido não entra no gráfico nem em “Falta pagar este mês”. No detalhe, a linha deixa claro que o parcelamento é hipotético e não é contrato com banco.

No filtro e no gráfico, Obrigação pontual cai em Dívidas, junto do financiamento importado. Não entra em Assinaturas.

### 2. O assumido é só o passado de desenvolvimento

“Pago (assumido)” existe porque o app ainda estava em desenvolvimento e o passado anterior a outubro de 2026 não tem vínculo. Não é uma regra que anda com o calendário. Novembro não transforma outubro em pago.

O corte fixo passa a ser 01/10/2026, devolvido na leitura, sem tela para editar. Antes dessa data, sem vínculo, a parcela é “Pago (assumido)”. Dessa data em diante, ou há vínculo, ou a parcela fica “Previsto” ou “Vencido · sem vínculo”.

- 30/09/2026 sem vínculo: “Pago (assumido)”;
- 10/10/2026 sem vínculo, com o dia já passado: “Vencido · sem vínculo”;
- 10/11/2026 sem vínculo: “Previsto”;
- em dezembro de 2026, 10/10 sem vínculo continua atrasado, não assumido;
- o que já tem lançamento continua “Pago · extrato”.

### 3. Terminar em, no detalhe

Remover parcelas futuras de uma assinatura sem fim, como a Claro, oferece apagar as 600 cobranças do horizonte (R$ 107.280 no exemplo). É tudo ou nada. O campo `end_date` já existe e o formulário esconde o término em Avançado.

No detalhe, assinatura e obrigação pontual ganham “Terminar em”. O usuário escolhe o mês da última cobrança, por exemplo dez/2027. Essa cobrança de dezembro permanece. O que vier depois sai da projeção. A gravação é o `end_date` que já existe, no dia-âncora daquele mês, pelo PUT atual. Não é o comando que apaga as 600 linhas. Vínculos e extrato ficam.

A confirmação diz o mês da última cobrança e que o histórico permanece. O vermelho continua só no diálogo de remover. Remover parcelas futuras segue para parcelamento e financiamento importados, e para quem quiser cortar de hoje em diante. Numa assinatura sem data de fim, o menu oferece primeiro “Terminar em”.

Cartão, detalhe, A conciliar e cronograma leem a mesma ocorrência que `schedule` no servidor. O dia-âncora (`schedule_anchor_day` / `schedule_anchor_end`) volta depois de um mês curto; encadear o dia já encurtado mudava a próxima cobrança. `end_date` continua inclusive. `future_cutoff` continua exclusivo: a cobrança desse dia e as seguintes saem da projeção. Um vínculo histórico marcado na próxima cobrança ou depois esvazia a agenda nos dois lados, em vez de o cliente pular para o mês seguinte. O centavo que sobra numa obrigação parcelada continua na última parcela, nos dois lados.

### Fora desta nota

Tipo editável para corrigir Condomínio. URL por tela na Web. Publicação de APK, até pedido explícito.
