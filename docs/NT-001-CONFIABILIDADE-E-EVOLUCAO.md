# NT-001 — Contrato executável de confiabilidade e evolução do Financial Twin

Versão normativa: 1.0, 29/09/2026. Aplicação: `financial-app`. Status de execução: consultar `IMPLEMENTATION-STATUS.md`; a existência desta nota NÃO significa implementação ou validação concluída.

Leitura complementar obrigatória: [runbook executável](NT-001-RUNBOOK-EXECUTAVEL.md), com mapa de arquivos, contratos HTTP, exemplos numéricos, comandos, conflitos, backup e homologação. Consulte o [estado e as evidências](IMPLEMENTATION-STATUS.md) para separar implementação, integração, ativação e dispositivo.

## 1. Objetivo e interpretação

Esta nota especifica a correção de A01–A16 da auditoria de 29/09/2026 e a implementação das cinco funcionalidades propostas. É um contrato para execução incremental por agentes LLM, revisores e desenvolvedores. Uma instrução explícita posterior do usuário prevalece. Não é possível prometer execução perfeita por qualquer modelo; os contratos, testes adversariais e checkpoints tornam erros detectáveis e limitam seus efeitos.

Entregar funcionalidades utilizáveis na interface e API, não apenas protótipos ou botões sem persistência. O planejamento de liquidez é consultivo; não executar operações bancárias. O guardião prepara dossiês, mas não envia reclamações. O simulador é determinístico e não promete probabilidade de sucesso sem calibração. A economia dos experimentos é observacional, não causal.

## 2. Invariantes invioláveis

| ID | Contrato |
|---|---|
| INV-01 | Evidência não é transação. Duas compras distintas não podem desaparecer por similaridade. Reenvio do MESMO event_id é idempotente. |
| INV-02 | Transferência própria é neutra em receitas/despesas e exige origem/destino diferentes; afeta cada saldo uma vez. |
| INV-03 | Compra no cartão é consumo; liquidação da fatura é caixa/passivo. Não somar ambos como despesa econômica. |
| INV-04 | Estorno diminui despesa; não é salário/receita. Em ausência de compra original, manter vínculo pendente e não inventar categoria. |
| INV-05 | `Decimal`/Decimal128 na lógica monetária; novas APIs usam strings com duas casas. Nunca somar moeda em float. |
| INV-06 | Instantes são UTC com offset; calendário é America/Sao_Paulo; datas de competência/vencimento são datas civis, não instantes. |
| INV-07 | Janela temporal é `[início inclusivo, fim exclusivo)`, com fim no início LOCAL do dia seguinte. |
| INV-08 | Saldo ausente é null/desconhecido, nunca zero. Cobertura incompleta bloqueia recomendações categóricas. |
| INV-09 | Estado financeiro e auditoria da mutação gravam na mesma transação MongoDB. Falha implica rollback. |
| INV-10 | Registro editável tem revisão; mudanças concorrentes retornam conflito. Nunca sobrescrever a alteração desconhecida de outro editor. |
| INV-11 | Alterar origem/data/valor/natureza invalida dependências conciliadas ou bloqueia a alteração até desfazer vínculo. |
| INV-12 | Nenhuma fila tem truncamento silencioso. Toda lista limitada informa total, has_more, next_cursor e permite continuar. |
| INV-13 | Propagação em lote exige opt-in em cada edição, prévia atual e confirmação do conjunto. Classificação pode abranger valores diferentes quando descritor e direção atendem às regras. |
| INV-14 | Só exibir “salvo” após confirmação de persistência. Arquivo precisa de bytes duráveis, não URI temporária. |
| INV-15 | Simular/alertar/experimentar nunca cria transação efetiva nem altera histórico financeiro. |
| INV-16 | Nenhuma migração apaga/reconstrói o ledger. Migrações são aditivas, idempotentes, com dry-run, relatório e caminho de reversão. |
| INV-17 | Um reembolso observado não equivale automaticamente à resolução de um caso. Exigir referência de movimento positivo e vínculo explícito. |
| INV-18 | Decisão humana e desfazimento produzem novos eventos de auditoria. Não apagar eventos antigos para esconder a decisão. |

## 3. Protocolo de trabalho do executor

1. Ler esta nota, `README.md`, `mobile/AGENTS.md` e o status. Confirmar diretório, arquivos modificados e versões instaladas.
2. Preservar os arquivos antes da edição. Nunca usar checkout/reset/clean global para resolver testes. Não imprimir `.env`, URI Mongo, senhas ou tokens.
3. Selecionar o primeiro item da fila cujas dependências estejam concluídas. Modificar somente arquivos necessários. Não usar subprocessos concorrentes que editem os mesmos arquivos.
4. Reproduzir o defeito ou escrever um teste comportamental significativo antes da correção. Um teste que só repete a implementação não satisfaz o contrato.
5. Implementar função de domínio pura quando possível; persistência em camada separada; UI consome a API. Uma única política para cada regra financeira.
6. Rodar regressões focadas; após lote coeso rodar a suíte completa. Alterou API ou UI: typecheck e lint. Alterou persistência: incluir falha parcial/conflito/reenvio.
7. Atualizar status com arquivos, comandos, resultado, limites e próximo item. Não marcar dispositivo/integração como validado a partir de mocks.
8. Se houver falha desconhecida, registrar reprodução e reduzir a mudança. Não “corrigir” testes retirando invariantes.
9. Não chamar produção nos testes de escrita. Banco de teste deve ter nome explícito `financial_twin_test_*`; nunca usar o banco configurado por padrão como teste.
10. Encerrar uma etapa somente após critérios de aceite. Para impedir conclusão fictícia, estados permitidos: planned, implementing, code_verified, integration_verified, device_verified, blocked_external.

## 4. Arquitetura alvo incremental

Preservar FastAPI/PyMongo, Expo 57, armazenamento local e coleções v1.2. Não realizar reescrita ampla. Introduzir módulos pequenos para calendário, política econômica, paginação, trilhas e planejamento. Rotas antigas permanecem compatíveis quando isso não permite corrupção; em conflito de revisão, solicitar recarga com HTTP 409/428.

Leituras explicáveis carregam referências e proveniência; não confundem `snapshot` transacional com cobertura completa. Um snapshot pode ser consistente e ainda ter extratos ausentes. A UI deve exibir as duas propriedades separadamente.

Novas coleções de workflow usam `_id` string UUID, `schema_version`, `revision`, `created_at`, `updated_at`, autoria `owner` e payload validado. Índices: identidade única, estado/data e idempotência por request_id. Artefatos de simulação armazenam premissas, não duplicam o ledger inteiro.

## 5. Fila única de execução e dependências

| Ordem | Trabalho | Dependências | Entrega mínima verificável |
|---|---|---|---|
| Q00 | Preservação, especificação e evidências | nenhuma | snapshot de fonte; esta nota; status separado |
| Q01 | Política econômica e calendário | Q00 | estorno/transferência/fatura uniformes; limites locais; testes |
| Q02 | Matching conservador e normalização | Q01 | empate não automático; direção/valor compatíveis; timestamp ISO; overflow explícito |
| Q03 | Atomicidade, revisões, invalidação | Q01 | rollback; conflito; vínculos não obsoletos; audit completo |
| Q04 | Recorrências e calendário de quitação | Q01,Q03 | âncora 31 e ano bissexto preservada; pagamento neutro rejeitado |
| Q05 | Paginação, opt-in e rascunho confiável | Q02,Q03 | >200 pendências acessíveis; vendedor desativado; salvo confirmado |
| Q06 | Arquivos e recuperação offline | Q03 | bytes IndexedDB/diretório durável; ACK antes de remoção; arquivos importados no media_store |
| Q07 | Release, dependências, backup e testes operacionais | Q00–Q06 | manifesto validado; lock; ferramentas de check/backup/restore isolado |
| Q08 | Fechamento comprovável | Q01–Q07 | cobertura, trilha, ambiguidades, desfazer vínculo auditado |
| Q09 | Guardião de cobranças/reembolsos | Q08 | detectar, abrir caso, anexar refs, exportar dossiê, resolver com crédito |
| Q10 | Simulação de decisões | Q01,Q04,Q08 | três cenários, curva diária, reserva, premissas e lacunas |
| Q11 | Liquidez por conta | Q10 | proposta neutra e conservadora; faltas de saldo explícitas; sem execução bancária |
| Q12 | Experimentos de economia | Q01,Q08 | criar, acompanhar, concluir, comparar bases e registrar satisfação |
| Q13 | Integração, experiência e release | Q08–Q12 | API+UI, regressões, build web; homologação/Android explicitamente separados |

O critério é reduzir acoplamento: uma política econômica antes de cinco consumidores; calendário antes de recorrência; bytes duráveis antes de novas evidências; cobertura antes de previsões. Não pular Q01–Q07 para implementar telas de previsão com saldos inventados.

## 6. Contratos de correção A01–A16

### A01/A06 — matching

- `captured_at` e `event_timestamp` devem ser normalizados para instantes UTC antes do scorer. Data inválida não vira silenciosamente agora.
- A pontuação ordena candidatos; ela não prova identidade. Quantidade de candidatos elegíveis, segundo melhor score e diferença devem ser considerados.
- Nova captura manual representa por padrão um novo movimento; nunca fundir silenciosamente duas entradas manuais. Possível duplicidade vira revisão.
- Para vínculo automático: mesma direção, valor exato em centavos, não transferência/fatura incompatível, score alto e margem mínima de 0,08 sobre o segundo candidato plausível. Empate exige revisão.
- Valor divergente pode gerar sugestão, jamais vínculo automático sem explicação explícita de taxa/ajuste. Não aplicar essa igualdade à CLASSIFICAÇÃO EM LOTE.
- Limite de candidatos: buscar limite+1; quando ultrapassado, retornar `candidate_scan_truncated=true`, impedir vínculo automático e criar pendência. Não interpretar amostra limitada como ausência de ambiguidade.
- Uma evidência pode apontar para ledger OU provisional, com tipo e ID explícitos. Desfazer não apaga o evento original.
- Regressões: +26 versus -26; -26 versus -30; dois candidatos idênticos; duas compras manuais; mesma evidência reenviada; ISO versus datetime; 501 candidatos.

### A02/A05 — escrita e dependências

- Captura update/confirm/cancel, referência de saldo, categoria create/delete e conciliação de transferência devem ser transacionais, incluindo audit.
- Repetir a mesma decisão terminal retorna resultado equivalente. Decisão incompatível após mudança de estado retorna 409.
- Campos financeiros editados em captura ligada a compromisso exigem desfazer vínculo ou bloqueio explícito. Não atualizar só `actual_amount` e esquecer calendário.
- Transferência alterada em valor/data/contas perde IDs conciliados e volta a `aguardando_extratos`; guardar antes/depois no audit.
- Revisão de captura começa em 1 quando ausente. Clientes novos enviam `expected_revision`. Migração de revisão é aditiva; não mudar a identidade de registros.
- Regressões: falha no audit, falha na segunda coleção, dois escritores, repetição após timeout e alteração de transferência conciliada.

### A03/A04 — significado econômico

- Criar função única para classificar efeito econômico. Transferência/fatura/pagamento de cartão são neutros; gasto e estorno afetam consumo com sinais opostos; renda verdadeira afeta receita.
- Quitação exige débito real, confirmado, não neutro, ainda não usado. Verificar política tanto em sugestões quanto no POST.
- Estorno sem compra conhecida permanece estorno; mostrar atribuição pendente. Nunca inferir compra original apenas por valor.
- Regressões: mesma transação em ledger/captura produz efeito equivalente; compra+fatura não dobra gasto; transferência não quita; estorno não vira renda.

### A07 — âncora de recorrência

- Persistir `schedule_anchor_date`, `schedule_anchor_day`/semântica de fim do mês e avanço por ocorrência. Próximo vencimento é um resultado, não nova âncora.
- Novos compromissos usam data inicial explicitada. Para legado sem âncora, usar vencimento atual como âncora declarada e não tentar reconstruir retroativamente o dia original.
- Edição explícita de vencimento/frequência redefine a âncora com auditoria. Quitação normal preserva âncora.
- Testar 31/jan → 28/fev → 31/mar após cada pagamento, 29/fev anual, cancelado, pausado, última parcela, pagamento histórico.

### A08/A09/A14 — durabilidade

- Web: IndexedDB para bytes e metadados de arquivos; fila/rascunhos migrados sem apagar os originais antes do commit. URI `blob:` legada exige resgate enquanto válida ou pedido de reanexação; não simular recuperação impossível.
- Android: copiar arquivo do picker/câmera para `Paths.document` antes de enfileirar. Nome interno por UUID, não nome externo não sanitizado.
- Ao receber ACK persistido, remover fila e depois conteúdo. Falha de limpeza não deve reenviar movimento já aceito.
- Falha de leitura JSON não é fila vazia. Preservar bytes originais e apresentar erro recuperável.
- Rascunho mostra salvando/salvo/falhou; a confirmação visual corresponde ao último write concluído. Serializar writes; versões antigas não substituem novas.
- Todos os imports usam o mesmo content-addressed media_store, SHA256, temporário+rename e registro no Mongo. Não fazer rollback apagando arquivo que outra operação usa.
- Regressores: reload offline, quota, dois documentos, import parcial, ACK de outro evento, falha após persistir bytes e antes da fila.

### A10 — calendário

- Definir `APP_TIMEZONE=America/Sao_Paulo` em único módulo.
- `day_start(date)` converte meia-noite local em UTC. `day_window(a,b)` retorna `[day_start(a),day_start(b+1))`.
- BSON datetime ingênuo vindo de PyMongo representa UTC; data civil de compromisso codificada como BSON por compatibilidade não deve ser deslocada para véspera.
- Exibir instantes via timezone explícito. Data `YYYY-MM-DD` de compromisso deve ser exibida como data civil.
- Testar 21h/22h/23h59 em São Paulo, meia-noite, mês/ano e limites sem overlap. Janela seguinte deve incluir o primeiro microssegundo excluído da anterior.

### A11/A13 — filas e lote

- Contrato de página: `{items,total,has_more,next_cursor,limit}`. Cursor opaco contém data, ID e assinatura de filtro. Cursor inválido retorna 422.
- Ordenação determinística por data e ID; busca com limite+1. No próximo request usar estritamente depois da âncora.
- UI exibe “X de Y” e carregar mais; refresh substitui, carregar mais anexa deduplicando IDs. Mudou filtro: limpar cursor.
- Both `applySimilarSupplier` e `applySimilarClassifications` começam false. Mudou vendedor/item/prévia: revogar consentimento anterior.
- Mostrar prévia e número antes da gravação. Servidor reavalia elegibilidade; se conjunto difere do aceito, retornar 409 em vez de ampliar lote.

### A12/A15/A16 — operação

- Manifesto publicado contém versão/code/hash/size extraídos do artefato e timestamp. API só oferece download se arquivo confere com manifesto. Não confundir versão de fonte com binário compilado.
- Performance: evitar histórico integral quando janela conhecida; agregação por conta/período para grandes históricos; não ocultar falta de cobertura. Medir p50/p95 numa base de teste com escala declarada.
- Manter lock Python e pnpm; executar auditoria de dependências sem atualização major automática. Não sobrescrever keystore.
- Versionar fonte com ignore explícito de `.env`, dados, extratos, mídia, logs e chaves. CI não depende de dados pessoais nem segredo de produção.
- Backup conjunto: manifesto com checksums, dump e arquivos; restauração somente em diretório e DB de teste explícitos. Não afirmar backup restaurável antes de verificar restore.
- Refatorar somente nas fronteiras necessárias; não converter monólito inteiro em uma única alteração.

## 7. Funcionalidade F1 — fechamento comprovável

Entrada: período civil, opcional conta. Saída: totais econômicos, cobertura, lacunas, quantidade de ambiguidades, referências e trilhas.

Cada nó tem `{kind,id,label,date,amount,economic_role}`. Cada aresta tem `{from,to,relation,decision,source_id}`. Tipos de nó: ledger, capture, evidence, raw_event, source_record, obligation, bill, reconciliation, audit. Relações desconhecidas não são inventadas. Consultas de detalhe são por IDs e retornam faltantes explicitamente.

Cobertura: referências de saldo, data máxima do extrato por conta, capturas não atribuídas, conciliações abertas, documentos ausentes. “Completo” exige cobertura declarada, nunca só ausência de pendências. “Consistente” refere-se à leitura; “coberto” refere-se aos dados.

Desfazer vínculo: request_id, expected_revision e motivo. Reabrir revisão/captura quando aplicável, arquivar decisão da evidência em audit, sem apagar raw_event. Bloquear desfazimento que deixaria compromisso liquidado sem fonte e informar dependência. Undo de vínculo automático não é delete genérico de lançamento.

Aceite: compra+notificação+fatura+pagamento visíveis na trilha com um único gasto; vínculo inválido reportado; undo idempotente e auditado; exposição apenas via sessão protegida conforme configuração.

## 8. Funcionalidade F2 — cenários de decisão

Entrada validada: data inicial, horizonte 1–90 dias, saldo inicial conhecido ou explícito, reserva mínima, compra total, parcelas 1–60, primeira cobrança, alternativa de espera, valor reduzido, fluxos planejados com origem/conta e sinal. Saldo manual é PREMISSA, não saldo verificado.

Gerar baseline + comprar agora + esperar + reduzir. Dividir em centavos inteiros e distribuir resto nas primeiras parcelas; soma deve ser exatamente o preço. Vencimentos preservam âncora. Curva diária: saldo de abertura + entradas - saídas; indicar primeiro dia abaixo da reserva e saldo mínimo.

Obrigações e compromissos com mesmo vínculo não são somados duas vezes. Cobranças de cartão não viram débito bancário na data da compra; se fechamento/vencimento da fatura não estiver disponível, pedir premissa de data de pagamento e marcar lacuna.

Saída carrega premissas, excluded_sources, gaps, dados disponíveis e explicação por ocorrência. Nenhuma persistência no ledger. Aceite: centavos, último dia do mês, ausência de saldo, fatura+compra, renda incerta, parcelas fora do horizonte explicitadas.

## 9. Funcionalidade F3 — guardião

Coleção `financial_cases`: identity, revision, status (`open`,`awaiting_refund`,`resolved`,`dismissed`), kind, título, valor esperado, data esperada, source_refs, notes, refund_ref e timestamps. Valores resolvidos só contam como recuperados após referência verificada de crédito efetivo.

Detecções são sugestões, não acusações: cobrança após cancelamento, valor acima de contratado, parcela excedente e prazo de reembolso vencido. Match por fornecedor/contrato conhecido; não usar valor isolado. Detector read-only; criar caso exige ação explícita e chave idempotente.

Dossiê exportável JSON/Markdown inclui dados do caso, fontes, cronologia, lacunas e separa dinheiro esperado de recebido. Sem mensagens externas. Reembolso usado em um caso não pode ser contado novamente em outro; reservar referência única.

Aceite: alerta não cria despesa; caso repetido não duplica; resolver sem crédito falha; débito/transferência não resolve; concorrência/revisão; dados sensíveis só exportados por ação do usuário.

## 10. Funcionalidade F4 — liquidez por conta

Entrada: saldos com data de referência, reservas por conta, obrigações com conta e data, transferências permitidas/limites. Ausência de referência bloqueia aquela conta como fonte. Binance não é caixa bancário automaticamente.

Para cada dia: aplicar fluxos; identificar déficit; considerar doadores com excedente acima da reserva E do seu pior saldo futuro conhecido. Transferir no máximo o menor entre déficit, excedente e limite. Proposta preserva soma dos saldos. Se não há solução, retornar insuficiência e hipóteses, nunca transferir de saldo desconhecido.

Saída: `{proposals,uncovered,gaps,assumptions}`; cada proposta contém data, origem, destino, valor, motivo e obrigações cobertas. Não mover dinheiro, não marcar obrigações pagas. Para registrar transferência executada usar fluxo já existente e revisão normal.

Aceite: soma conservada, dois débitos concorrentes, conta sem saldo, reserva, cartão não tratado como conta financiadora, dia com insuficiência global, recálculo determinístico.

## 11. Funcionalidade F5 — experimentos

Coleção `saving_experiments`: id/revision/request_id, título, categoria/filtro, protocolo, baseline_start/end, start/end, target_reduction_pct, status, satisfaction (1–5), confounders. Só um experimento ativo por usuário no MVP; concorrência via documento único `experiment_locks._id=owner`, na mesma transação. A conclusão registra `actual_end` e congela o horizonte observado.

Comparar despesa econômica líquida em períodos de igual duração ou taxa diária com denominadores mostrados. Não incluir dias futuros no realizado. Comparar mesma cobertura quando possível; diferenças impedem afirmação forte. Não usar total negativo de estorno como “economia garantida”.

Saída inclui valores baseline/observado, dias, variação observada, alvo, cobertura e aviso de ausência de inferência causal. Concluir exige data válida, pode registrar satisfação e fatores externos. Não reclassificar transações para atingir meta.

Aceite: zero baseline não divide por zero; período parcial não compara com mês completo; sobreposição rejeitada; transferências neutras; estornos consistentes; somente um ativo; edição com revisão.

## 12. Matriz de validação e rollback

| Camada | Obrigatório | Não constitui prova |
|---|---|---|
| Domínio | unitários dos invariantes com exemplos adversariais | apenas lint |
| Persistência | rollback/conflito/idempotência em banco de teste replica set | mock sem transação |
| API | request/response JSON, erros 409/422/428, sessão | chamada direta de função isolada |
| Web | build, navegação, offline/reload, IndexedDB, layout | typecheck |
| Android | build, instalação, captura, sobrevivência, update | APK gerado |
| Operação | backup restaurado, hashes, migração dry-run | arquivo ZIP existente |

Rollback de código usa snapshot/commit conhecido e mantém dados aditivos. Nunca remover coleções novas automaticamente: versão anterior pode ignorá-las. Migração de arquivo local mantém fila original até confirmar bytes. Se mudança revelar inconsistência histórica, gerar proposta de reparo por ID com antes/depois; não aplicar por heurística global.

## 13. Handoff para outro LLM

Prompt de continuidade: “Leia NT-001 e IMPLEMENTATION-STATUS. Execute somente o primeiro Q incompleto com dependências concluídas, respeitando INV-01–INV-18. Verifique código atual antes de editar. Preserve dados, produza regressão do comportamento, rode checks proporcionais, atualize status com evidência. Não alegue implantação, dispositivo ou integração sem prova. Não amplie o lote de alterações nem apague testes para obter verde.”

Checklist de conclusão: cada A vinculado a código+teste; cada F com API+UI+erros; dados de exemplo identificados; nenhuma credencial em artefatos; perfis sem saldo continuam desconhecidos; publicação só a partir de artefato verificado; limitações externas registradas com passos concretos de validação.
