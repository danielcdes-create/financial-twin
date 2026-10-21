# Conexão e recuperação local

## Inbox Android — protocolo 1.20.33

Leitura, inclusão e confirmação nativas usam a mesma trava. O envelope `inbox_v2` é separado do armazenamento legado, cujos bytes permanecem intocados; entradas inválidas, conflitos de identidade e arquivos de recuperação inválidos são preservados em `recovery_v2`. O bridge distingue `ready`, `needs_recovery` e `unavailable`. Erro de armazenamento/leitura não equivale a fila vazia. **Mais** permite tentar recuperar e exportar a fonte original explicitamente, sob sessão válida.

Antes de consultar `/health`, o App importa os eventos locais para SQLite e confirma a identidade/conteúdo persistidos. A contagem é a união das identidades nativas e SQLite, distinguindo quarentena e etapas de finalização. A origem nativa só sai após ACK durável; erro posterior retoma finalização, sem repetir a escrita financeira. Offline mantém a evidência local visível e preservada. O transporte e a exportação continuam sujeitos à sessão e à privacidade.

Notificações separam aprovação/recusa/estorno do contexto da compra, comerciante e rodapés. Compra aprovada com limite disponível é capturada; mensagem financeira ambígua segue como evidência para revisão. Correspondência automática exige referência bancária igual, provedor e conta duráveis iguais e as validações financeiras; similaridade sugere revisão. Valores/data/comerciante semelhantes não identificam uma compra. [Auditoria atual](AUDITORIA-ADVERSARIAL.md).

O backend permanece neste PC, conforme a escolha do usuário. PC, sessão Windows e Tailscale precisam estar ativos; suspensão ou desligamento interrompem o acesso. Não foram alteradas as preferências de bateria/suspensão, nem foi contratado um serviço externo.

## Confirmação de compra — 1.20.77

A projeção do vendedor passa a trazer o alias inteiro, com `valor`, `normalizado`, `status` e `source_record_ids`. Antes, `aliases.normalizado` fazia o MongoDB devolver o alias sem os campos obrigatórios. Ao acrescentar um nome novo, a gravação reenviava essa lista incompleta, o validador recusava com código 121 e a Revisão mostrava “Operação indisponível; nenhuma gravação parcial deve ser considerada confirmada.” Se a lista recebida estiver incompleta, a função relê o fornecedor antes de gravar.

O tempo esgotado de trava (código 24) e os abortos de transação 244 e 251 entram no mesmo caminho de repetição do comando já gravado. A montagem do cache de taxonomia, subcategorias, contexto histórico e identidade do dono lê o banco fora da sessão da escrita.

## Cache de leitura — 1.20.76

`unwrap_database` tira o invólucro `_SessionDatabase` lendo o atributo `db` no dicionário da instância. O acesso anterior `getattr(db, "db", db)` fazia o PyMongo devolver uma coleção nova chamada `db` a cada chamada, e o cache de referência nunca reconhecia o mesmo banco. Com isso passam a valer `classification_cache`, `historical_context_cache`, `subcategory_catalog_cache` e `taxonomy_overrides_cache`.

O `PATCH /v1/provisional/{provisional_id}` deixa de registrar o mesmo caminho duas vezes e reaproveita os perfis de conta já lidos na confirmação. A identidade do dono fica em `owner_cache` por 15 segundos, com cópia na saída e descarte imediato em login, logout, renovação de sessão e troca de conta ou senha. A projeção mensal pede `batchSize` 2000 em compromissos, parcelamentos e obrigações. A telemetria guarda a coleção pelo par conexão e pedido e nomeia o `getMore` pela coleção, em vez de `unknown` ou do identificador do cursor.

## Leituras — 1.20.75

As leituras deixam de abrir uma transação Mongo só para consultar. `mongo_snapshot` devolve o banco já aberto. Num `Database` real o rótulo continua `snapshot`; num banco de teste continua `best_effort`. Não há `start_session` nem `commitTransaction` nesse caminho. As escritas seguem em `mongo_transaction`.

Cursores de leitura pedem `batchSize` 2000. As opções de classificação saem de uma agregação por coleção, não de um `distinct` por campo. Contas, saldos, estimativas e o catálogo reutilizam as linhas já lidas na mesma consulta. O cache de classificação dura 300 segundos e também invalida o catálogo de subcategorias.

Um GET não grava `last_seen` da sessão. Numa escrita, essa gravação segue numa thread e não segura a resposta. A renovação biométrica pode persistir a sessão do mesmo modo.

No cliente, sair de uma aba aborta o GET que ela deixou aberto. Contas e saldos já frescos não são pedidos de novo. O desmonte de um editor aborta o controlador de leitura registrado no escopo. **Sincronizar agora** continua no refresh completo, que já chama a fila.

## Cabeçalhos de telemetria — 1.20.74

`X-Client-Tab` e `X-Client-Action` saem só em ASCII. O OkHttp recusa o acento de `Início` (`char 0xed`) e a reticência de `Processando…`, e a rejeição acontece antes do pedido sair do aparelho. `Início` segue como `Inicio`. O nome da aba na interface não muda.

## Telemetria e janelas — 1.20.73

O cliente mede troca de aba, o tempo até a aba visível, leituras, capturas e mutações. O buffer fica em SQLite (`perf_telemetry`) e sobe em lote autenticado para `POST /v1/telemetry/events`. `GET /v1/telemetry/report` devolve P50, P95 e P99 por rota e por evento do cliente. Os cabeçalhos `X-Trace-Id`, `Server-Timing` e `X-Perf-Metrics` separam rede, Python e MongoDB. Essas rotas não invalidam o cache de classificação e não entram no journal de mutação.

A consulta, o identificador do documento, o valor e o texto de erro ficam fora do registro. O processo grava em `logs/perf_telemetry.live.jsonl`, que fica de fora do Git. O instantâneo publicado é `logs/perf_telemetry.jsonl`. Os demais arquivos de `logs/` e os outros `*.jsonl` continuam de fora.

No painel, a competência usa uma faixa indexável de `competencia` e o mesmo filtro de mês válido. Intervalos sobrepostos da mesma leitura são fundidos. Seis índices novos, criados de forma idempotente na subida, cobrem competência, status de revisão, evidência e rótulos de auditoria. `taxonomy_overrides` reutiliza o cache de classificação por 300 segundos. Candidatos de pagamento e a página do extrato enriquecem o vendedor em lote. Abrir Compromissos não pede `/v1/commitment-projection` de novo: a projeção já carrega na montagem. Uma gravação ainda atualiza o gráfico.

O painel continua calculando o período sobre o snapshot já lido. Não há tabela extra de totais por mês. O ledger não é reescrito.

## Abas e leituras — 1.20.72

Trocar de aba não recria o `ScrollView`. Depois de gravar, só a aba visível busca de novo; as outras perdem a marca de carregadas e atualizam quando forem abertas. Confirmar uma captura ou uma revisão tira o item da lista na hora e recarrega a página em segundo plano, sem pedir de novo as opções de classificação. Mais, Capturar e Compromissos reutilizam contas e perfis já lidos na sessão. O recálculo de saldos fica na aba Contas.

No servidor, a sugestão de vendedor e plataforma reutiliza um contexto histórico em memória por 120 segundos. Ele cai junto com o cache de classificação em qualquer escrita que já invalidava esse cache. A gravação de uma captura preenchida não varre o histórico para repetir vendedor e plataforma. A fila de revisão busca eventos, capturas e lançamentos em três consultas `$in`. O saldo de conta lê os extratos vinculados de uma vez. O painel agrupa o mês econômico numa passada e reaproveita o intervalo quando o período pedido já é esse mês. O catálogo de categorias lê `categories` uma vez.

## Leituras repetidas — 1.20.71

Listas, cards e gráficos reutilizam um `Intl.NumberFormat` e um `Intl.DateTimeFormat` de módulo. O quadro de compromissos calcula as figuras de cada card uma vez por mudança das entradas e não limpa o painel de agenda quando ele já está fechado. O rascunho da revisão e do lote serializa a fonte uma vez, e gravações SQLite concorrentes ficam na última linha pedida.

No servidor, rótulos de parcelamento buscam os lançamentos vinculados por `$in`, com o identificador original, o texto e o `ObjectId` quando ele é válido. Não há varredura da coleção quando um vínculo não aparece. A página de capturas e a de movimentações montam um contexto histórico uma vez e o reutilizam ao sugerir vendedor e plataforma. A lista de compromissos resolve vigência, extrato vinculado e nome do cartão em lote. O cache de opções de classificação não cai em `POST`/`PATCH` de prévia ou revisão. A trava de inserção incrementa o documento `movement-insert` uma vez por transação MongoDB.

## Servidor

- `backend/run_dev.py` supervisiona um único processo HTTP em `run_server.py`. Um mutex Windows impede supervisores duplicados entre o atalho e a tarefa `Financial Twin Backend`.
- O supervisor verifica `/live` a cada 5 segundos, com timeout de 3 segundos. Após três falhas consecutivas, reinicia apenas sua própria árvore de processos. Há 60 segundos de tolerância na inicialização e 2 segundos de intervalo antes de repor um processo encerrado.
- `/live` verifica a resposta HTTP sem acessar o banco. `/health` mantém o ping MongoDB. Indisponibilidade do banco não reinicia um servidor HTTP responsivo.
- O worker mantém a fábrica Windows `SelectorEventLoop`, evitando o caminho Proactor/AcceptEx afetado pela falha anteriormente observada.
- Logs do supervisor, worker e consultas ficam em `data/backend-startup/`. O atalho desktop usa a tarefa agendada existente; se ela não existir, inicia o mesmo supervisor, com logs por execução. A tarefa preserva a recuperação após saída inesperada do supervisor.
- Nenhum processo MongoDB, Tailscale ou listener externo é encerrado pelo supervisor. Não há alteração de registros financeiros ou limpeza de armazenamento.

## App 1.19.3 / código 39

- Consultas GET compartilham a mesma requisição em andamento e admitem duas novas tentativas, após 600 e 1.200 ms, para falhas transitórias. O orçamento total continua de 20 segundos; cancelamento e troca de sessão interrompem espera e requisição.
- POSTs e uploads não recebem essa repetição automática. A fila mantém o mesmo evento até confirmação explícita de persistência e identidade; tentativas posteriores usam o fluxo idempotente existente.
- O indicador deriva da disponibilidade observada pela API: `Conectando`, `Conectado` ou `Reconectando`. Sucesso de um dashboard não transforma uma falha de saúde em indicador verde.
- Enquanto há falha de conexão ou consulta pendente de recuperação, o app verifica a recuperação a cada 5 segundos quando está em primeiro plano. Consultas que falharam são refeitas mesmo se pertencem a outra aba; sucesso remove o aviso antigo. Dados já carregados permanecem na tela.
- O agendamento de nova tentativa da fila chama a sincronização de fato, além de atualizar os dados visíveis. Um item aguardando seu horário de nova tentativa não depende mais do intervalo normal de dois minutos.
- Falha 503 da extração xAI não é tratada como queda do servidor inteiro. Falhas de autenticação e validação não recebem repetição automática de GET.

## Verificação

131 testes Python incluem um servidor isolado que encerra e trava com o processo vivo, recuperação dos dois casos, mutex e banco indisponível com HTTP vivo. Casos do cliente cobrem GET compartilhado, POST sem repetição, ACK, preservação da fila, 401, cancelamento, orçamento de timeout, recuperação de recursos e isolamento da extração.

Evidências de tela, conexão e APK: `../../audit_work/connection-stability-2026-09-30/`. Os testes de tela usam armazenamento isolado e respostas sintéticas, sem gravações na base financeira. A instalação e o comportamento em aparelho físico precisam ser conferidos no celular; não há aparelho conectado via ADB.
