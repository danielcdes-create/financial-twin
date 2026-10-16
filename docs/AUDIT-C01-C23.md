# Correções da auditoria C01–C23

Referência: [auditoria de 1.20.9](../../audit_work/exhaustive-2026-10-02/RELATORIO.md) e [verificação das correções](../../audit_work/fixes-C01-C23-2026-10-02/RELATORIO.md).

## Edições e decisões

Classificação e revisão de um lançamento exigem o `expected_token` retornado pela leitura. Saldo de referência exige esse token e um `request_id` UUID: repetir o mesmo comando devolve o resultado anterior; mudar seu conteúdo com o mesmo ID retorna 409. O token é calculado sobre a fonte completa, sem acrescentar campos ao extrato histórico. Ausência de versão retorna 428 e conflito retorna 409; o cliente preserva os campos digitados e solicita uma conferência explícita da versão atual.

Uma correspondência exige `review_token`, `source_token` e `candidate_token` da mesma revisão observada. Valor, data civil, natureza ou conta diferentes exigem `acknowledge_difference=true`, após exibição dos dois registros. Origem, candidato e revisão são conferidos novamente dentro da transação; nenhuma evidência é anexada a uma fonte que mudou.

Reprocessar uma notificação exige `expected_revision`, só aceita capturas ainda em revisão e grava captura, evento e auditoria juntos. A interpretação externa ocorre antes da transação; qualquer decisão ou vínculo concorrente invalida o resultado. Conciliar uma transferência também avança sua revisão. A trilha de evidências usa uma única leitura snapshot.

Estorno com sinal negativo exige correção explícita de natureza/tipo; estorno positivo reduz despesa, sem virar receita. Contas abertas cadastradas em `account_profiles` são aceitas nos compromissos pela identidade estável `profile:<key>`; contas encerradas/arquivadas não podem ser selecionadas para novos vínculos.

## Importação, continuidade e restauração

O leitor e o provedor visual aceitam o mesmo limite de dez páginas PDF. Os testes cobrem seis e dez páginas com provedor simulado; não fazem uma chamada paga de IA. XLSX com contêiner ou XML inválido recebe erro permanente de entrada. Arquivos, filas e rascunhos permanecem recuperáveis.

Lotes usam cursores e o cliente percorre todas as páginas. Obrigações e parcelamentos usam `offset`, `next_offset`, `has_more` e `total`, com filtros aplicados antes da paginação. O cliente carrega a lista completa e rejeita cursores repetidos.

Backup inventaria os caminhos tanto de eventos quanto de lotes; restauração remapeia os caminhos para o novo volume. A verificação torna o arquivo da origem indisponível e confirma bytes, preview e importação na base restaurada.

Voz e interpretação de compra compartilham limite de duas operações de IA simultâneas por processo. Excesso recebe 429 com `Retry-After: 5`; para vários processos, o limite total é a soma dos limites individuais. Notificações Android usam uma lista exata de pacotes bancários; o pacote é uma indicação de origem, e a captura continua sujeita à revisão do usuário.

## Dependências e validação

Os sete pacotes Expo foram alinhados às versões recomendadas para SDK 57. A versão pública `node-forge` 1.4.0 ainda é marcada pela [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv). Aplicamos por `pnpm patch` a verificação de estrutura ASN.1 da [correção proposta pelo mantenedor](https://github.com/digitalbazaar/forge/pull/1152), com testes de assinatura válida, digest adulterado e estrutura malformada.

`audit-dependencies.cjs` confere o hash do código aplicado e os testes criptográficos antes de admitir exclusivamente esse alerta na árvore de `@expo/cli`, até 2026-11-01. O relatório bruto permanece disponível; o alerta não é escondido. Outros alertas ou uma auditoria indisponível falham. Substituir pelo release oficial corrigido assim que publicado e retirar a exceção. A dependência pertence às ferramentas de desenvolvimento/build.

`run-client-tests.cjs` executa todos os arquivos `test-*.cjs`; o dispatcher de integração e os guards são iguais no fluxo local e no CI. Os testes gravam somente em bases sintéticas locais. Logs e resultados não substituem a verificação em Android físico nem confirmam uma execução remota do CI.
