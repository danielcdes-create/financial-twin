# Checklist de fechamento do mês

Em **Mais → Fechamento do mês**, escolha a competência no formato `AAAA-MM`. A tela começa no último mês completo e mantém progresso, observações, justificativas e saldos separados para cada mês.

## Dados a reunir e conferir

- Conferência geral: todas as contas e cartões utilizados, extratos importados, capturas e revisões, classificação, transferências, duplicidades, compromissos, investimentos e resumo financeiro.
- Cada conta: extrato completo, saldos antes do primeiro dia e ao final do último dia, entradas/saídas, transferências/saques, tarifas/rendimentos e débitos de faturas/compromissos.
- Cada cartão: fatura completa, compras/parcelas/estornos/encargos, total/pagamento/saldo, datas/limite, pagamentos na conta pagadora e parcelas/assinaturas futuras. O recorte inclui competências do mês e faturas anteriores pagas ou vencidas no período.

Marque os itens depois de comparar com os documentos originais. Use **Observações / Não se aplica** para indicar a fonte consultada, uma exceção ou uma justificativa. Itens sem aplicação exigem uma justificativa de pelo menos cinco caracteres. Extratos e saldos de contas com atividade, e faturas/totais de cartões com atividade, precisam ser conferidos. **Adicionar pendência** inclui requisitos próprios em qualquer seção. **Somente pendentes** facilita retomar o trabalho.

## Saldos e pendências

Os saldos informados pertencem ao fechamento daquele mês; não substituem o saldo atual de **Contas**. Para contas de caixa, a diferença é:

`encerramento − (abertura + movimentos cadastrados)`

Diferenças impedem marcar a conferência de saldos. Transferências e pagamentos de fatura entram nessa equação da conta pagadora, mesmo sendo neutros para o total econômico de receitas e despesas. Uma ponta de transferência vinculada e válida usa a data e o valor do ledger, inclusive quando a captura pertence a outro mês ou foi recebida após a referência de saldo. Só a ponta sem vínculo usa a captura como movimento; vínculo quebrado ou divergente exige revisão e não inventa um movimento substituto. Posições de investimento, criptoativos e financiamento podem ter variação de valor; diferenças precisam de explicação nas observações do item de saldos.

Receitas e gastos do resumo econômico seguem `competencia` declarada. Registros sem competência usam, explicitamente, o dia civil de `data_evento` em Brasília. Uma compra de 31/08 com competência 09 entra no consumo de setembro, enquanto o fluxo da conta continua em agosto. Estornos e parcelas seguem sua própria competência declarada. Dashboard e fechamento do Laboratório usam a mesma regra; fatura e previsão não criam um segundo consumo. Alterar a competência conserva a data histórica e as referências de caixa. O retrato de um mês concluído permanece preservado, com sinalização de alteração e exigência de reabertura para reconferência.

A API aponta capturas/revisões pendentes, natureza/categoria/conta ausentes, transferências capturadas incompletas, faturas sem cartão e divergências entre total, valor pago, saldo e obrigação vinculada. Outras diferenças, documentos ausentes e vínculos não explicitados dependem da conferência dos originais pelo usuário. As marcações registram essa conferência e não certificam automaticamente que o banco ou emissor esteja conciliado.

## Conclusão e revisão

O mês só pode ser concluído depois de terminado, com todos os itens conferidos ou justificados e nenhuma pendência impeditiva. A conclusão guarda os totais, grupos e marcações daquele momento. **Reabrir checklist do mês** permite continuar a revisão.

Mudanças nos documentos financeiros deixam as marcações dos grupos afetados pendentes para nova conferência. Se o checklist já estava concluído, a tela alerta sobre a mudança e mantém os totais da conclusão anterior. Edições concorrentes e marcações feitas sobre dados desatualizados são rejeitadas com HTTP 409; atualize e confira novamente.

Desde 1.20.4, o item de compromissos também acompanha cobranças e vínculos do período, compras de compromissos no mês e parcelas/obrigações com suas correções efetivas de agenda. Um cadastro pertencente apenas a outro mês não invalida o período conferido. Capturas confirmadas de cartão integram a atividade e as fontes do cartão, inclusive antes da importação da fatura. Uma conta com movimento aceito continua no fechamento mesmo quando sua abertura declarada é posterior; a divergência do cadastro impede concluir a conferência de escopo até sua revisão. Vencimentos usam datas civis; compras e pagamentos usam instantes em Brasília.

## Persistência e validação

`GET /v1/month-close/{month}` monta a lista em leitura consistente e não grava nada. Marcações, saldos, pendências adicionais, conclusão e reabertura usam `expected_revision`, transação MongoDB, lock compartilhado de movimentações e auditoria antes/depois. Marcações também exigem `expected_source_token` para o item exibido.

O estado mensal fica em `account_profiles` com `entity_type=month_close` e chave `month-close:AAAA-MM`, separado do catálogo das contas. Esta funcionalidade não modifica transações, contas importadas, faturas, obrigações ou conciliações.

Validação do release 1.20.3: 246 testes Python (16 específicos do checklist), 13 verificações de API com MongoDB sintético em replica set, typecheck/lint e UI web em 390/320 px. Os testes incluem persistência, isolamento entre meses, referência de saldos, pagamentos neutros, deduplicação explícita, invalidação por mudança, conflitos, conclusão/reabertura, auditoria atômica e preservação do histórico financeiro. O APK não foi instalado em aparelho físico nesta validação.
