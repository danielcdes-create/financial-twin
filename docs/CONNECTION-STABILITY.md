# Conexão e recuperação local

## Inbox Android — protocolo 1.20.33

Leitura, inclusão e confirmação nativas usam a mesma trava. O envelope `inbox_v2` é separado do armazenamento legado, cujos bytes permanecem intocados; entradas inválidas, conflitos de identidade e arquivos de recuperação inválidos são preservados em `recovery_v2`. O bridge distingue `ready`, `needs_recovery` e `unavailable`. Erro de armazenamento/leitura não equivale a fila vazia. **Mais** permite tentar recuperar e exportar a fonte original explicitamente, sob sessão válida.

Antes de consultar `/health`, o App importa os eventos locais para SQLite e confirma a identidade/conteúdo persistidos. A contagem é a união das identidades nativas e SQLite, distinguindo quarentena e etapas de finalização. A origem nativa só sai após ACK durável; erro posterior retoma finalização, sem repetir a escrita financeira. Offline mantém a evidência local visível e preservada. O transporte e a exportação continuam sujeitos à sessão e à privacidade.

Notificações separam aprovação/recusa/estorno do contexto da compra, comerciante e rodapés. Compra aprovada com limite disponível é capturada; mensagem financeira ambígua segue como evidência para revisão. Correspondência automática exige referência bancária igual, provedor e conta duráveis iguais e as validações financeiras; similaridade sugere revisão. Valores/data/comerciante semelhantes não identificam uma compra. [Resultado e limites](RECUPERACAO-ANDROID-2026-10-09.md).

O backend permanece neste PC, conforme a escolha do usuário. PC, sessão Windows e Tailscale precisam estar ativos; suspensão ou desligamento interrompem o acesso. Não foram alteradas as preferências de bateria/suspensão, nem foi contratado um serviço externo.

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
