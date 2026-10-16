# Auditoria adversarial do Financial Twin — Android

Registro histórico da auditoria anterior às correções. A implementação e as evidências atuais dos 19 achados e SEC01 estão em [Recuperação Android](RECUPERACAO-ANDROID-2026-10-09.md); os probes originais foram preservados. Publicação e homologação física são registradas separadamente.

Data: **09/10/2026**, Brasília. Escopo: app atual, backend, fluxos Android, persistência, publicação e recuperação. Resultado: **19 achados confirmados — 17 de comportamento e 2 operacionais**. Prioridades: **6 P1, 12 P2 e 1 P3**. Nenhum P0 confirmado. **Dois itens de segurança** e **quatro riscos ainda não confirmados como defeitos** são contados separadamente. PS02 e BE06 descrevem a mesma colisão de voz e foram consolidados em BE06.

A principal fragilidade está na preservação da intenção entre camadas: uma captura nova pode ser tratada como repetição; uma edição remota pode ser substituída sem comparação; e uma fatura pode continuar visível em um lugar e deixar de ser oferecida em outro. Na interface, ainda há divergências entre erro e vazio, resultado e premissa, diálogo e seu dono, além da navegação por Voltar. Os controles compartilhados existentes funcionaram nos cenários protegidos, mas alguns consumidores escapam deles.

Esta passagem produziu diagnóstico e plano. **Não alterou código de produção, dependências, serviço ou dados financeiros reais e não gerou novo APK.** As únicas novas entregas no projeto são os dois documentos desta auditoria; provas, bancos sintéticos e relatório interativo ficam em `audit_work/adversarial-android-2026-10-09`. Não há medição de quantos registros reais foram atingidos. Não usar os exemplos sintéticos para corrigir seu histórico por inferência.

## Método, escopo e confiança

Foram lidos os contratos atuais, incluindo `mobile/AGENTS.md`, UI-STABILITY, SELECTOR-FOCUS e documentos financeiros pertinentes. Houve revisão paralela de interface, persistência/captura e backend, seguida de revisão crítica independente das reproduções. Os achados de domínio foram executados em coleções sintéticas e depois em MongoDB local real com transações/snapshots. Componentes React e código Kotlin atuais foram executados com recursos nativos/transportes sintéticos. O navegador renderizou componentes reais, não telas desenhadas para a auditoria.

- **Confirmado por reprodução:** saída inadequada observada no código atual com entradas especificadas. Mocks, JVM e Mongo real são identificados em cada trilha.
- **Confirmado por inspeção/artefato:** estado operacional verificável, como ausência de commits e divergência de versão documental.
- **Risco:** mecanismo suspeito ou limite observado que ainda exige comprovar impacto no uso real.
- **Lacuna:** dimensão não executada; não conta como defeito.
- **P1:** ameaça de omissão/sobrescrita ou incoerência financeira relevante; **P2:** recuperação, interação, validação ou operação degradada; **P3:** consistência documental. A ordem dos lotes considera dependências, além da severidade.

Ausência de aparelho ADB impede certificar IME, TalkBack, biometria, foco por fabricante, compartilhamento, disco nativo, kill e instalação. Não confundir inspeção ampla com prova exaustiva de todas as sequências possíveis. Não houve pentest externo, carga longa ou avaliação da qualidade da extração pela IA externa.

## Estado verificado

| Controle | Resultado atual | Limite |
|---|---|---|
| Backend unitário | 457 testes aprovados | Não cobre os novos cenários por si só |
| Cliente | 49 scripts de regressão aprovados | Widgets/recursos nativos simulados em grande parte |
| Integração | 11 suítes aprovadas, MongoDB isolado `127.0.0.1:27029` | Não usou a base financeira real |
| Probes adicionais backend | 6 defeitos / 7 cenários confirmados também em MongoDB real | Corridas novas específicas ainda são critérios de aceite |
| TypeScript / ESLint / compatibilidade Expo | Aprovados | Não provam comportamento físico |
| Dependências mobile | Gate aprovado com duas exceções estreitas existentes, patches verificados | node-forge e braces; exceções expiram em 01/11/2026 |
| Dependências backend | **Falhou:** 3 advisories em 1 pacote, PyMongo 4.18.1 | SEC01; exploração deste app não demonstrada |
| Rotas `/v1` | 107 operações; 102 guardadas por sessão; 5 públicas esperadas | Inventário estrutural, não pentest completo |
| Serviço local | `/live` e `/health` 200; contas e projeção sem sessão 401 | Apenas probes públicos, sem autenticação financeira |
| Renderização | 12 componentes × 320/390 px = 24 casos; sem erros de console na amostra | Estados e dados sintéticos; não cobre todos os gestos |
| Artefato Android | 1.20.32 / 78; hash do arquivo, manifesto e download local iguais | Instalação no aparelho não verificada |
| Proveniência do bundle | Bundle no APK igual ao build; 85 fontes embarcadas conferidas sem divergência | Fonte-map de JS/TS não certifica execução nativa |
| Assinatura | APK v2 válido, certificado Android Debug | SEC02, separado de adulteração |

SHA256 do APK: `648A758B435E73B64F403B2A80A2A53371106EB04A9F7981DD3CE0722064C0A4`; tamanho **47.836.832 bytes**. O aparelho instalado e sua versão permanecem desconhecidos.

A integração inclui segurança de conta, núcleo, cronogramas, lotes, exclusão, convergência, regressões adversariais, perfis de conta, regressões C, estrutura e reinício. No núcleo foram aprovados rollback quando a auditoria falha, paginação de 241 itens de mesmo instante, backup/restauração sem perda e conservação de Decimal128. Isso demonstra controles úteis no ambiente isolado, sem certificar backup atual de produção.

## Achados confirmados

| ID | Prioridade | Achado |
|---|---|---|
| BE01 | P1 | Exclusão de conta ignora compromissos vinculados e cria referência órfã |
| BE03 | P1 | Fatura aberta desaparece do planejamento de caixa e de compra após edição válida |
| BE04 | P1 | Correspondência automática absorve compra distinta pelo mesmo valor e comerciante no dia seguinte |
| BE06 | P1 | Duas evidências de voz independentes com o mesmo texto recebem ACKs distintos para uma só captura antiga |
| PS01 | P1 | Compras aprovadas são descartadas se o texto também mencionar limite disponível |
| UI01 | P1 | Conflito nas parcelas futuras reaplica valor sobreposto sem mostrar diferença remota |
| BE02 | P2 | Fechar ou arquivar uma conta impede cancelar e editar o compromisso já vinculado |
| BE05 | P2 | Última parcela quitada produz cadastro que o próprio editor não consegue salvar |
| OP01 | P2 | Fontes e workflow não formam uma versão recuperável no Git |
| PS03 | P2 | Fila nativa ilegível bloqueia novas capturas e o bridge pode apresentá-la como vazia |
| PS04 | P2 | Notificações locais ainda não importadas desaparecem da contagem enquanto o servidor está offline |
| PS05 | P2 | Falha ao limpar rascunho de evidência de voz reabre texto já enviado como nova captura |
| UI02 | P2 | Simulação financeira conserva resultado antigo após editar premissas |
| UI03 | P2 | Parser monetário aceita agrupamento inválido e envia outro número |
| UI04 | P2 | Diálogo de cartão órfão inicia DELETE após desmontagem do dono |
| UI06 | P2 | Buscar mesma consulta não recupera a revisão após falha |
| UI07 | P2 | Falha de sugestões deixa seletores vazios sem erro ou retomada |
| UI08 | P2 | Voltar Android não percorre subtelas mantidas em estado local |
| OP02 | P3 | README anuncia como atual um release anterior ao APK servido |

### BE01 — Exclusão de conta ignora compromissos vinculados e cria referência órfã (P1)

**Cenário:** Criar conta personalizada; criar assinatura com account_id=profile:<key>; abrir prévia/excluir conta sem outros vínculos.

**Observado:** account_usage retorna {}; exclusão bem-sucedida mantém assinatura apontando para chave removida; tentativa de cancelar o compromisso mantendo essa conta recebe 422.

**Causa/local:** O inventário de uso não consulta commitments. Criação/edição de compromissos também não compartilha a trava de movimentações usada pela exclusão, deixando uma lacuna concorrente a cobrir na correção. [account_profiles.py:154](C:/Users/danie/FIN/financial-app/backend/app/account_profiles.py:154); [main.py:2028](C:/Users/danie/FIN/financial-app/backend/app/main.py:2028).

**Recuperação:** Adicionar compromissos e regras de valor ao inventário de referências; serializar exclusão e criação/alteração dos vínculos. Inventariar órfãos em leitura e recuperar apenas por identidade/documentos originais, sem recriar movimentos.

**Aceite:** Conta usada por compromisso ativo, pausado, cancelado ou concluído não pode ser excluída; deve ser arquivável. Corrida criar compromisso versus excluir conta tem uma saída consistente, sem órfão. Prévia, execução e mensagens Android concordam.

**Qualificação:** A exclusão sequencial foi reproduzida em MongoDB real. A corrida simultânea criar compromisso/excluir conta é risco adicional a validar na recuperação.

**Evidência:** [trilha backend](../../audit_work/adversarial-android-2026-10-09/backend/report.md).

### BE03 — Fatura aberta desaparece do planejamento de caixa e de compra após edição válida (P1)

**Cenário:** Corrigir de R$100 para R$120 uma fatura não paga e ainda não vencida, com obrigação futura vinculada.

**Observado:** Editor grava status aberta. Sugestão de -100.00 antes da edição some completamente depois; gráfico mensal continua mostrando bill_balance=120.00.

**Causa/local:** A seleção de obrigações do planejamento cobre futura, vencida e parcialmente_paga, omitindo aberta que o próprio editor produz. [insights.py:198](C:/Users/danie/FIN/financial-app/backend/app/insights.py:198); [cards.py:207](C:/Users/danie/FIN/financial-app/backend/app/cards.py:207).

**Recuperação:** Centralizar elegibilidade de obrigações em aberto e usar o mesmo contrato em contexto de compra, caixa, projeção e cartões. Preservar separação compra/fatura e conferir sobreposições explicitamente.

**Aceite:** Faturas aberta/futura/vencida/parcialmente_paga com saldo positivo aparecem uma vez por obrigação. Edição de valor/vencimento não elimina a obrigação do seletor. Paga/cancelada/excluída não retorna como nova dívida; cliente mostra o valor atualizado.

**Qualificação:** A obrigação é oferecida desmarcada. O defeito elimina a possibilidade de incluí-la após editar; não foi demonstrada redução automática de saldo em toda análise existente.

**Evidência:** [trilha backend](../../audit_work/adversarial-android-2026-10-09/backend/report.md).

### BE04 — Correspondência automática absorve compra distinta pelo mesmo valor e comerciante no dia seguinte (P1)

**Cenário:** Extrato contém compra de R$8 em LOJA EXEMPLO no Itaú em 08/10 às12h; chega nova notificação de compra em09/10 às12h, novo event_id, mesmo banco/valor/comerciante, sem referência bancária estável.

**Observado:** Resultado matched, score=0.8623, evidência anexada à compra anterior, zero nova captura para o gasto distinto. Datas explícitas diferem24h.

**Causa/local:** A elegibilidade automática usa valor, sinal e proveniência bancária, com instrumento validado quando informado, sem exigir identidade temporal suficiente. A pontuação ainda ultrapassa o limiar automático para a compra distinta 24 horas depois. [matching.py:26](C:/Users/danie/FIN/financial-app/backend/app/matching.py:26); [matching.py:62](C:/Users/danie/FIN/financial-app/backend/app/matching.py:62); [transaction_service.py:225](C:/Users/danie/FIN/financial-app/backend/app/transaction_service.py:225).

**Recuperação:** Restringir absorção automática a identidade durável ou evidência temporal inequívoca por tipo de fonte; encaminhar ambiguidade à revisão. Auditar em leitura vínculos automáticos históricos com diferença temporal, sem desfazer por heurística.

**Aceite:** Duas compras distintas de mesmo valor/comerciante em dias diferentes permanecem distintas/revisáveis. Mesmo evento repetido com mesma identidade mantém ACK idempotente. Referência bancária igual e escopada por conta/provedor continua conciliável; casos ambíguos não desaparecem.

**Evidência:** [trilha backend](../../audit_work/adversarial-android-2026-10-09/backend/report.md).

### BE06 — Duas evidências de voz independentes com o mesmo texto recebem ACKs distintos para uma só captura antiga (P1)

**Cenário:** Em dias distintos, salvar como evidência a frase 'Gastei oito reais na lanchonete hoje' com IDs de fila novos.

**Observado:** ACKs de ambos event_id são persisted=true, mas persisted_event_id é igual. Uma só raw_event/captura, data permanece08/10 embora segunda operação seja09/10. Extração roda só uma vez.

**Causa/local:** O cliente cria UUID por operação, mas extract_document_data substitui a identidade por UUID5 receipt:<provider>:<hash bytes>. complete_upload faz ACK do segundo comando apontando para primeiro evento. Hash de arquivo identifica conteúdo, não ocorrência da fala. [App.tsx:697](C:/Users/danie/FIN/financial-app/mobile/App.tsx:697); [captureSubmission.ts:11](C:/Users/danie/FIN/financial-app/mobile/utils/captureSubmission.ts:11); [main.py:1734](C:/Users/danie/FIN/financial-app/backend/app/main.py:1734); [operation_identity.py:34](C:/Users/danie/FIN/financial-app/backend/app/operation_identity.py:34).

**Recuperação:** Separar armazenamento endereçado por hash de identidade de captura. Para mobile_voice, preservar identidade/instante da declaração e reusar apenas o mesmo comando em retry. Conservar deduplicação de um recibo original idêntico conforme sua finalidade.

**Aceite:** Mesmo UUID+bytes repetido gera uma captura e ACK reproduzível. UUIDs diferentes em dias diferentes com texto igual geram declarações distintas e datas corretas, sem absorção cega posterior pelo matching. UI/fila só remove comando após reconhecer o evento pretendido; histórico existente não é reconstruído por semelhança.

**Qualificação:** Aplica-se a salvar o texto de voz como evidência, não a todo uso de voz. Reproduzido independentemente por duas frentes e em MongoDB local real. Corrigir junto com PS05, para não transformar retentativa de rascunho em captura duplicada.

**Evidência:** [trilha backend](../../audit_work/adversarial-android-2026-10-09/backend/report.md).

### PS01 — Compras aprovadas são descartadas se o texto também mencionar limite disponível (P1)

**Cenário:** Origem com.nu.production; título Compra aprovada. Texto: Compra aprovada de R$ 32,90 em LOJA TESTE. Limite disponível: R$ 1.500,00. Invocar onNotificationPosted; fila fica vazia. Remover o rodapé produz um evento.

**Observado:** 0 eventos para compra aprovada com rodapé; 1 evento no controle. Outro caso com CANCELADO no nome do estabelecimento também é descartado.

**Impacto:** Omissão silenciosa antes da persistência: não aparece na fila, na revisão, em erro ou em quarentena; nenhum retry pode recuperar esse evento pelo app.

**Causa/local:** Filtro negativo aplicado ao título e corpo inteiros com containsMatchIn, sem vincular a negação ao estado da compra. [NotificationCaptureService.kt:43](C:/Users/danie/FIN/financial-app/mobile/modules/notification-capture/android/src/main/java/expo/modules/notificationcapture/NotificationCaptureService.kt:43).

**Recuperação:** Separar intenção de compra de rodapés de saldo/limite e do nome do estabelecimento. Preservar notificações financeiras ambíguas como evidência sem inventar movimentação. Rever fontes bancárias disponíveis para lacunas históricas; não fabricar compras a partir desta reprodução.

**Aceite:** Aprovação com rodapé de limite entra uma vez; recusa/estorno de compra permanece fora da criação automática. Negação em nome de comerciante não implica recusa. Repost idêntico não duplica; compras distintas continuam distintas. Fixture real por banco e dispositivo, incluindo EXTRA_BIG_TEXT, resumo e atualização.

**Evidência:** [trilha persistence](../../audit_work/adversarial-android-2026-10-09/persistence/report.md).

### UI01 — Conflito nas parcelas futuras reaplica valor sobreposto sem mostrar diferença remota (P1)

**Cenário:** Parcela 100, edição local 110, PUT recebe409 após remoto virar200. Carregar cronograma atual; revisar e confirmar.

**Observado:** Segundo PUT usa token v2 e envia110.00. Valor remoto200 não aparece; conflict é limpo.

**Causa/local:** Rebase reaplica campos locais sem comparação tripla e sem aceitação de sobreposição. [FutureCommitmentEditor.tsx:40](C:/Users/danie/FIN/financial-app/mobile/components/FutureCommitmentEditor.tsx:40).

**Recuperação:** Comparar origem/local/remoto por campo e exigir escolha explícita nas sobreposições.

**Aceite:** Campos disjuntos convergem; valor, data ou cancelamento sobrepostos bloqueiam escrita até decisão explícita.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### BE02 — Fechar ou arquivar uma conta impede cancelar e editar o compromisso já vinculado (P2)

**Cenário:** Criar assinatura numa conta aberta; encerrar ou arquivar a conta; cancelar a assinatura mantendo sua referência.

**Observado:** Nos dois casos o backend responde 422: selecione uma conta aberta e não arquivada para uma nova movimentação. A ação era cancelar um cadastro existente.

**Causa/local:** validate_commitment_refs recebe existing, mas a validação de conta usa selectable=True incondicionalmente. [main.py:2010](C:/Users/danie/FIN/financial-app/backend/app/main.py:2010); [main.py:2071](C:/Users/danie/FIN/financial-app/backend/app/main.py:2071).

**Recuperação:** Distinguir identidade mantida de nova escolha. Preservar conta histórica em cancelamento/edições compatíveis; validar novas contas abertas. Atualizar o contrato e seletor para conservar opção histórica.

**Aceite:** Encerrada e arquivada: cancelar/pausar e editar nota funcionam com mesma conta e revisão. Trocar vínculo para outra conta encerrada continua rejeitado. Histórico e pagamentos vinculados mantêm mesma identidade.

**Evidência:** [trilha backend](../../audit_work/adversarial-android-2026-10-09/backend/report.md).

### BE05 — Última parcela quitada produz cadastro que o próprio editor não consegue salvar (P2)

**Cenário:** Cadastrar parcelamento1/1, vincular a saída da última parcela, abrir Editar compromisso e mudar apenas observações.

**Observado:** Pagamento grava status concluido e next_installment_number=2 para installment_count=1. Schema rejeita próxima parcela maior que total, e guard Android também impede salvar.

**Causa/local:** Transição final usa contador n+1 como sentinela, enquanto API de edição/UI só permitem1..n para todos os estados. [main.py:2243](C:/Users/danie/FIN/financial-app/backend/app/main.py:2243); [commitments.py:49](C:/Users/danie/FIN/financial-app/backend/app/commitments.py:49); [CommitmentManager.tsx:77](C:/Users/danie/FIN/financial-app/mobile/components/CommitmentManager.tsx:77).

**Recuperação:** Definir representação canônica de conclusão (próxima ocorrência ausente ou sentinela explicitamente aceita apenas no estado concluído), manter compatibilidade para registros existentes e alinhar schema/formulário/leitura.

**Aceite:** Após quitação1/1 e600/600, editar nota sem mexer nos pagamentos funciona. Reativação exige agenda explícita coerente e não duplica última parcela paga. API de leitura e editor aceitam todos os estados que o backend produz.

**Qualificação:** Pagamento, estado persistido e rejeição pelo schema foram reproduzidos em MongoDB real. O bloqueio do formulário Android foi confirmado por inspeção do guard, não por execução deste componente no aparelho.

**Evidência:** [trilha backend](../../audit_work/adversarial-android-2026-10-09/backend/report.md).

### OP01 — Fontes e workflow não formam uma versão recuperável no Git (P2)

**Cenário:** Identificar o commit exato do release e a execução automática dos quality gates.

**Observado:** A raiz financial-app não é repositório Git. mobile possui .git, mas master não tem commits; components, utils, app, modules e scripts não estão rastreados. Workflow está fora desse repositório e pressupõe backend/mobile no mesmo checkout.

**Causa/local:** Estrutura local de entrega ainda não está registrada em uma raiz versionada coerente com o workflow. [quality.yml:1](C:/Users/danie/FIN/financial-app/.github/workflows/quality.yml:1).

**Recuperação:** Preservar alterações, fazer baseline integral sem credenciais/dados/builds, escolher raiz coerente com backend/mobile, registrar commit/tag e demonstrar execução dos gates. Conservar cópias/hashes existentes.

**Aceite:** Release identificável por commit+configuração+lockfiles; checkout limpo em outro diretório reproduz testes; rollback e fontes do APK têm registro auditável.

**Qualificação:** Há backups e fingerprints locais; ausência de commits não significa ausência de toda possibilidade de recuperação. Nenhum repositório remoto foi consultado ou criado.

**Evidência:** [trilha root](../../audit_work/adversarial-android-2026-10-09/root/operational-baseline.json).

### PS03 — Fila nativa ilegível bloqueia novas capturas e o bridge pode apresentá-la como vazia (P2)

**Cenário:** Injetar objeto JSON em KEY_EVENTS onde a rotina exige JSONArray. Entregar notificação de compra válida: onNotificationPosted lança JSONException e não guarda a nova evidência. No bridge JS, JSON truncado ou falha nativa retorna []; JSON válido do tipo objeto retorna objeto em vez de CapturedEvent[].

**Observado:** Bytes originais ficam preservados, mas toda leitura/append posterior depende do mesmo conteúdo inválido. Parse/read failure e fila vazia são indistinguíveis no bridge.

**Impacto:** Captura pode permanecer operacionalmente parada sem uma ação de recuperação compreensível. O usuário pode ver zero itens apesar de armazenamento ilegível.

**Causa/local:** Sem envelope validado, quarentena separada, diagnóstico durável ou recuperação; catch do bridge suprime falhas. [NotificationCaptureService.kt:78](C:/Users/danie/FIN/financial-app/mobile/modules/notification-capture/android/src/main/java/expo/modules/notificationcapture/NotificationCaptureService.kt:78); [index.ts:10](C:/Users/danie/FIN/financial-app/mobile/modules/notification-capture/index.ts:10).

**Recuperação:** Preservar bytes originais em registro de recuperação e expor estado indisponível/precisa de recuperação, distinto de vazio. Validar array e cada evento no limite nativo/JS, isolando itens inválidos sem descartar os válidos. Permitir novos eventos em armazenamento íntegro separado sem apagar a fonte danificada. Propagar falhas de commit/read para diagnóstico visível e recuperável.

**Aceite:** JSON inválido e erro de leitura exibem falha explícita e mantêm exportável a fonte original. Uma entrada malformada não impede captura posterior válida. Após recuperação, itens íntegros mantêm event_id e ACK; nenhum clear/rebuild destrutivo. Falha de commit nunca se apresenta como captura armazenada.

**Evidência:** [trilha persistence](../../audit_work/adversarial-android-2026-10-09/persistence/report.md).

### PS04 — Notificações locais ainda não importadas desaparecem da contagem enquanto o servidor está offline (P2)

**Cenário:** Seed de uma notificação no inbox nativo e SQLite vazio. Falhar GET /health com a sessão ainda válida. Executar prepare da fila; ler contagem local/interface.

**Observado:** 0 leituras do inbox, 0 registros SQLite e contagem UI 0. Controle online lê inbox e insere 1.

**Impacto:** Usuário não consegue saber se a compra foi capturada offline ou aguarda envio; pode registrar novamente por achar que falhou. A fonte nativa continua preservada.

**Causa/local:** Importação de dados locais subordinada ao health check remoto; contagem ignora inbox não importado. [App.tsx:321](C:/Users/danie/FIN/financial-app/mobile/App.tsx:321); [App.tsx:327](C:/Users/danie/FIN/financial-app/mobile/App.tsx:327); [App.tsx:275](C:/Users/danie/FIN/financial-app/mobile/App.tsx:275).

**Recuperação:** Separar ingestão local e transporte: ler/validar/persistir o inbox sob sessão adequada antes de testar disponibilidade da rede. Mostrar contagem e estágio de origem nativa + SQLite sem dupla contagem por event_id. Manter markSynced somente depois do ACK durável ou descarte explícito.

**Aceite:** Com rede/servidor indisponível, compra recebida aparece como salva no aparelho/aguardando envio. Religar a rede conclui exatamente uma vez com os mesmos IDs. Falha SQLite preserva origem nativa e mostra erro em vez de zero enganoso. Cobertura/bloqueio de sessão não publica informações privadas.

**Evidência:** [trilha persistence](../../audit_work/adversarial-android-2026-10-09/persistence/report.md).

### PS05 — Falha ao limpar rascunho de evidência de voz reabre texto já enviado como nova captura (P2)

**Cenário:** Salvar transcrição como evidência; permitir INSERT na event_queue. Falhar DELETE de voice_drafts disparado pelo autosave após a limpeza visual. Sair e reabrir o editor; o texto antigo retorna com botão Salvar texto como evidência. Salvar novamente.

**Observado:** Primeiro envio cria 1 comando; UI oculta transcrição, apresenta sucesso e erro de rascunho. Remount recupera texto antigo; retry cria segundo UUID e segundo arquivo/fila para o mesmo texto.

**Impacto:** Estado visual e estado durável divergem. O usuário recebe convite para criar novamente algo já enfileirado; editar o rascunho restaurado torna ambígua a intenção de correção versus nova evidência.

**Causa/local:** Identidade do comando não faz parte do rascunho de voz; limpeza/sucesso dependem de estado React e autosave em useEffect, fora da transição durável do envio. [VoiceCapture.tsx:170](C:/Users/danie/FIN/financial-app/mobile/components/VoiceCapture.tsx:170); [VoiceCapture.tsx:172](C:/Users/danie/FIN/financial-app/mobile/components/VoiceCapture.tsx:172); [VoiceCapture.tsx:81](C:/Users/danie/FIN/financial-app/mobile/components/VoiceCapture.tsx:81); [App.tsx:699](C:/Users/danie/FIN/financial-app/mobile/App.tsx:699).

**Recuperação:** Persistir comando selado/receipt no rascunho antes de enqueue, com retomada explícita da finalização local. Somente apresentar rascunho vazio quando a transição durável estiver concluída; falha preserva status já enfileirado e mesma identidade. Distinguir Editar nova captura de Concluir envio/limpeza pendente.

**Aceite:** Falha de limpeza e kill entre enqueue e limpeza restauram estado enviado com mesmo UUID. Repetir finalização não cria novo arquivo/comando. Sucesso da fila não é revertido para falha financeira por problema de rascunho. Exclusão de rascunho não apaga fonte, fila, ACK ou bytes compartilhados.

**Evidência:** [trilha persistence](../../audit_work/adversarial-android-2026-10-09/persistence/report.md).

### UI02 — Simulação financeira conserva resultado antigo após editar premissas (P2)

**Cenário:** Calcular saldo1000 e compra100, resultado mínimo900; alterar compra para500.

**Observado:** Mínimo900 continua visível sem rótulo de premissas antigas. Liquidez usa o mesmo mecanismo por inspeção.

**Causa/local:** Setters de premissas não invalidam simulation/liquidity ou identificam snapshot calculado. [FinancialLab.tsx:101](C:/Users/danie/FIN/financial-app/mobile/components/FinancialLab.tsx:101).

**Recuperação:** Associar resultado a snapshot de todas as premissas e invalidar ou rotular explicitamente ao editar.

**Aceite:** Preço, saldo, reserva, parcelas, datas e escolhas de fluxo invalidam resultados anteriores; resposta pertence à geração vigente.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### UI03 — Parser monetário aceita agrupamento inválido e envia outro número (P2)

**Cenário:** Editar total de fatura para1.2,34 e confirmar.

**Observado:** PATCH envia valor_total12.34 sem advertência. Mesmo parser em MonthClose:21, FinancialLab:15, PurchaseCheck:24 por inspeção.

**Causa/local:** Remove todos os pontos antes de validar agrupamento original. [CardManager.tsx:129](C:/Users/danie/FIN/financial-app/mobile/components/CardManager.tsx:129).

**Recuperação:** Parser estrito compartilhado com sinal e precisão por domínio.

**Aceite:** Aceitar formatos legítimos; rejeitar1.2,34,1..234,56,expoente e precisão inválida sem transporte.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### UI04 — Diálogo de cartão órfão inicia DELETE após desmontagem do dono (P2)

**Cenário:** Abrir Excluir cartão; desmontar CardManager mantendo diálogo global; confirmar Excluir.

**Observado:** DELETE /v1/card-catalog/card iniciado após unmount. CardManager:72 captura validade só no callback e:75 só verifica após API.

**Causa/local:** Confirmação não captura dono ao abrir e mutação não revalida antes do transporte. [CardManager.tsx:142](C:/Users/danie/FIN/financial-app/mobile/components/CardManager.tsx:142).

**Recuperação:** Capturar dono/geração antes da confirmação e conferir antes de iniciar transporte.

**Aceite:** Callback após unmount ou troca de dono envia zero escritas; callback vigente envia uma e preserva ACK.

**Qualificação:** Desmontagem induzida no harness. Caminho físico que desmonte mantendo diálogo ainda precisa de teste; não alegar ocorrência natural observada.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### UI06 — Buscar mesma consulta não recupera a revisão após falha (P2)

**Cenário:** Falhar primeiro GET de Revisar registros; fechar aviso; Buscar mantendo texto vazio.

**Observado:** Só um GET ao todo; nenhum retry inline. Recuperação depende de mudar consulta/filtro ou remontar.

**Causa/local:** applySearch atribui estados iguais sem executar load; erro desaparece com diálogo. [RecordReview.tsx:40](C:/Users/danie/FIN/financial-app/mobile/components/RecordReview.tsx:40).

**Recuperação:** Erro inline e retry explícito; mesma consulta deve admitir atualização.

**Aceite:** Retry da mesma consulta faz uma nova leitura e recupera lista/totais sem resposta obsoleta.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### UI07 — Falha de sugestões deixa seletores vazios sem erro ou retomada (P2)

**Cenário:** Falhar classification-options; abrir Categoria.

**Observado:** Zero opções, nenhuma mensagem de falha, nenhum retry.

**Causa/local:** catch vazio trata erro como catálogo vazio e encerra loading. [TransactionEditor.tsx:55](C:/Users/danie/FIN/financial-app/mobile/components/TransactionEditor.tsx:55).

**Recuperação:** Estados loading/empty/error/stale explícitos; retry sem remontar editor.

**Aceite:** Erro recuperável, rascunho e identidade do seletor preservados; retry repopula opções sem aceitar sugestão automaticamente.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### UI08 — Voltar Android não percorre subtelas mantidas em estado local (P2)

**Cenário:** App e UserOperationBoundary reais: Mais→Decisões, hardwareBackPress em repouso.

**Observado:** Único handler é a trava e retornafalse; subtela local continua Decisões. Botão visual usa setTab(Mais). Referências App:186,805,1615; UserOperationBoundary:13; app/_layout:31.

**Causa/local:** Subtelas locais não pertencem à pilha Router nem têm adaptação de Back. [App.tsx:1604](C:/Users/danie/FIN/financial-app/mobile/App.tsx:1604).

**Recuperação:** Definir hierarquia Android e integrar Back à transição visual, preservando rascunhos/filtros.

**Aceite:** Back no Detalhe retorna ao extrato; em subtelas de Mais retorna a Mais; overlay fecha primeiro e escrita descarta Back.

**Qualificação:** Default nativo do Router não executado; não foi alegado encerramento físico nem perda de rascunho.

**Evidência:** [trilha ui](../../audit_work/adversarial-android-2026-10-09/ui/report.md).

### OP02 — README anuncia como atual um release anterior ao APK servido (P3)

**Cenário:** Consultar a seção Release privado atual para escolher o APK ou diagnosticar uma interação.

**Observado:** README anuncia 1.20.26/código72; app.json, aapt, manifesto e GET /v1/app-update confirmam 1.20.32/código78.

**Causa/local:** Texto manual de release atual não converge com a promoção do artefato. [README.md:84](C:/Users/danie/FIN/financial-app/README.md:84).

**Recuperação:** Separar histórico datado de ponteiro vigente; atualizar o ponteiro a partir do manifesto validado e checá-lo no fluxo de publicação.

**Aceite:** README, manifesto, APK e atualizador concordam no candidato publicado sem reescrever as evidências históricas.

**Evidência:** [trilha root](../../audit_work/adversarial-android-2026-10-09/root/operational-baseline.json).

## Segurança — contagem separada

### SEC01 — Gate de dependências Python falha: três advisories em PyMongo 4.18.1 (P1)

pip-audit --no-deps --disable-pip retornou exit1 e CVE-2026-96747, CVE-2026-96748, CVE-2026-96749. .venv importa PyMongo4.18.1 e extensão BSON nativa. Fornecedor informa correção4.18.2.

Não houve exploit nem prova de exploração remota. KMS exige criptografia do cliente e acesso ao key vault; URI exige host influenciado por entrada não confiável, não identificado na configuração estudada; overflow BSON depende de volume muito grande. Três avisos correspondem a um pacote, não a três defeitos financeiros.

**Recuperação:** Atualizar pin/lock/ambiente para versão corrigida após compatibilidade; executar scanner, unitários e integração; reiniciar serviço na etapa de implantação e conferir sua versão efetiva.

**Aceite:** Scanner sem os três avisos no lock e ambiente implantado, mesmas garantias de transação/ACK e testes relevantes verdes.

Fontes: [documentação oficial 1](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/release-notes/), [documentação oficial 2](https://www.mongodb.com/resources/products/alerts).

### SEC02 — APK distribuído usa certificado Android Debug (P2)

apksigner verify confirma assinatura v2 válida e DN CN=Android Debug; release usa signingConfigs.debug. Certificado SHA256 fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c.

Assinatura atual é criptograficamente válida e historicamente usada no canal privado. Não é evidência de APK adulterado nem de chave roubada. Troca simples de chave pode impedir atualização; precisa planejamento.

**Recuperação:** Definir chave privada de distribuição e proteção/backup; planejar migração compatível com instalações existentes, com prova de atualização e preservação de SQLite, arquivos, rascunhos e fila. Não trocar assinatura nem desinstalar como atalho.

**Aceite:** Artefato assinado por chave de release protegida; atualização/migração validada em aparelho sobre o app anterior e dados locais intactos.

Fontes: [documentação oficial 1](https://developer.android.com/studio/publish/app-signing), [documentação oficial 2](https://developer.android.com/google/play/app-updates).

## Cobertura de interface e transições

| Superfície | O que foi confrontado | Evidência / limite |
|---|---|---|
| Início, Extrato, detalhe | Filtros, competência, conta, seleção de item, edições, retorno, dados obsoletos | App/contratos/suítes atuais; UI08 e UI07; não ensaio físico integral |
| Digitar | Rascunho durável, comando selado, erros, tipos, conta e pagamento | Suítes manual-capture/app-transitions/local-draft-deletion e renderização |
| Voz | Gravação/transcrição/uso/exclusão, rascunho, fila, ACK | PS05, BE06; microfone e rede externa simulados |
| Foto, arquivo, PDF e importação | Picker, bytes duráveis, extração, lote, comparação, confirmação, retries | Suítes de captura/arquivos/lotes + integração; câmera/ContentProvider reais pendentes |
| Notificações | Origem, aprovado/negado, rodapés, replay, inbox, offline, corrupção | PS01/03/04; Kotlin atual compilado; sem callback Android físico |
| Revisar captura e registros | Texto original, classificação, sugestão, erros, busca, paginação, conflito | UI06/07, componentes reais e regressões; busca idêntica após erro falhou |
| Revisar lote | Conta, linha/descrição/data/valor, sinal, tipo, duplicidade, rascunho | Integração + UI real em navegador; edição de linha preservada na inspeção |
| Contas | Criar/editar/saldo/status/arquivo/excluir e referências | BE01/02 em Mongo real, UI consumidora revisada e renderizada |
| Cartões e faturas | Catálogo, edição, exclusão, valor, confirmação, conflito, recarga | UI03/04; BE03; release/ACK e estados de fatura |
| Categorias/subcategorias | Criar/renomear/substituir/excluir, catálogo e opções | Suítes atuais e inspeção compartilhada; sem novo defeito dedicado confirmado |
| Compromissos/projeção | Cadastro, estimativa, cronograma, pagamento, parcelas, conclusão | BE01/02/05, UI01; controles de projeção atuais; novos casos reais locais |
| Decisões, Simular, Liquidez | Período, premissas, análises e retorno | UI02; parser UI03 por inspeção; renderização e testes de motores |
| Fechamento | Mês, provas, progresso, estado e operações | Suítes atuais + renderização; fonte de saldos/movimentos sem reparo real |
| Cabe na minha vida | Preço, parcelas, fluxos, premissas, screenshot compartilhável | Suítes atuais e renderização; contraste positivo de invalidação do resultado |
| Mais, Ajustes, rascunhos, comandos pendentes | Navegação, opções, exclusão local, replay e identidade | UI08; suítes draft/queue/mutations; bytes reais do celular não acessados |
| Login, conta, biometria e privacidade | Dono, sessão, blur/focus, background, cancelamento, troca de senha | Suítes de auth/biometric-entry e integração; sensor/TalkBack físicos pendentes |
| Atualização e share | Versão, manifesto, assinatura, download, rota protegida e intenção | Proveniência atual; SEC02; PS-R02 depende de aparelho |
| Controles compartilhados | Seletores, diálogos, foco, fundo inerte, teclado, bloqueio síncrono | Trava e seletor reais inspecionados; consumidores com exceções destacados |

Na amostra visual de 24 estados, a largura de rolagem do contêiner não excedeu sua largura (318/388 px internos); isso não é uma afirmação universal sobre texto truncado. A abertura do seletor focou Fechar, sem autofocus na busca, e sua altura permaneceu **661,64 px** com lista cheia e vazia em 390 px. A trava tornou o fundo inerte e abriu confirmação após ACK. No TransactionEditor, o campo Observações permaneceu em **517,20 px** relativos ao painel após inserir a prévia: controle negativo web para deslocamento, com Android ainda pendente. O clique auxiliar da fixture mudou foco, por isso não foi interpretado como defeito do editor.

## Riscos sem promoção indevida a defeitos

- **UI-R01 — Leitura depois do ACK conserva a trava global (P2):** Probe UI05: ACK com item/token novo recebido, Alteração salva aberta e gate busy até GET terminar. AccountPositions:67 e diversos await refresh têm padrão relacionado. Contrato permite leitura necessária ao próximo token. Não classificar automaticamente como defeito. Verificar necessidade, recuperar leitura sem repetir escrita.

- **UI-R02 — Inserções assíncronas acima do campo ativo (P2):** Blocos de preview aparecem acima de campos. QA web root em390px conservou Observações na posição relativa517.1999969px por scroll anchoring. Não há salto web confirmado. Medir Android/IME fisicamente antes de afirmar regressão.

- **PS-R01 — Capacidade e retenção sem orçamento explícito (P3):** Listener reescreve todo JSONArray em SharedPreferences.commit; finalização mantém bytes/ACK deliberadamente e não há coletor reference-safe chamado em produção. Medir filas grandes, tamanho de arquivos/ACK e disco baixo; apresentar ocupação e planejar retenção com referências e preservação histórica, sem limpar dados automaticamente.

- **PS-R02 — Compartilhamento recebido com sessão bloqueada pode perder a intenção de retorno (P2):** app/+native-intent.ts encaminha a /handle-share, rota disponível só sob Stack.Protected authenticated; não foi localizado journal explícito de intenção. Validar share com app frio, bloqueado, sessão expirada e biometria cancelada; persistir/restaurar intenção somente se reprodução confirmar falha.

## Plano priorizado

[Plano completo, dependências e gates](PLANO-RECUPERACAO-ANDROID-2026-10-09.md).

- **R0 — Preservar e fixar a versão de referência:** OP01. Backup restaurado em destino vazio e isolado; referência de código/artefato verificável; nenhuma limpeza de fila ou reconstrução de movimentos.
- **R1 — Impedir omissões e sobrescritas silenciosas:** PS01, BE04, BE06, PS05, UI01. Duas compras legítimas de mesmo valor/comerciante em dias distintos permanecem duas; duas evidências de voz novas permanecem duas; replay do mesmo comando produz uma. Falha após ACK não gera UUID novo. 409 nunca substitui valor/data/cancelamento remoto sem escolha explícita.
- **R2 — Alinhar cadastro, obrigações e planejamento:** BE01, BE02, BE03, BE05. Prévia e exclusão concordam; zero órfãos em testes de corrida; cancelar compromisso mantendo a conta histórica funciona; fatura aberta continua elegível para seleção do planejamento; nota em parcela concluída salva sem reabrir dívida.
- **R3 — Recuperar interações e tornar o estado visível:** UI02, UI03, UI04, UI06, UI07, UI08, PS03, PS04. Cada fluxo mantém rascunho, foco e dono em erro/retomada. Segundo toque, edição e Back durante mutação são descartados sem replay. ACK seguido de GET falho continua sendo salvo. Pendência local aparece mesmo com servidor indisponível.
- **R4 — Tratar segurança e fechar o processo de publicação:** SEC01, SEC02, OP01, OP02. Scanner sem os avisos identificados; fontes e build rastreáveis; versão/hash convergentes. Atualização/migração mantém fila, mídia, SecureStore e rascunhos.
- **R5 — Homologar o Android e reconciliar eventual impacto histórico:** Todos, UI-R01, UI-R02, PS-R01, PS-R02. Sem P1 aberto; scanner resolvido; testes atuais + novos probes de aceitação verdes; instalação sobre versão anterior comprovada; impacto histórico explicitamente medido ou declarado pendente.

## Homologação Android obrigatória para encerrar a recuperação

Registrar modelo/Android, versão/código/hash do APK, escala de fonte e teclado. Executar numa cópia de teste com dados sintéticos; preservar o app e histórico pessoais. A atualização final sobre a instalação existente precisa preservar armazenamento e identidade de assinatura.

| Eixo | Sequência adversarial | Resultado exigido |
|---|---|---|
| Navegação | Início→Extrato→Detalhe→Editar; Mais→cada subtela; Voltar físico/gesto/visual | Mesmo destino por hierarquia; overlay fecha primeiro; rascunho protegido |
| Seletores/teclado | Teclado aberto, abrir campo, buscar muitos/zero resultados, selecionar/limpar, repetir | Sem oscilação/remontagem; foco e texto estáveis; opções antigas não atuam na nova abertura |
| Mutações | Duplo toque, toque em outra aba, editar, Back; ACK normal, timeout e resposta perdida | Uma operação; ações posteriores descartadas sem replay; comando e recibo preservados |
| Diálogos | Cancelar, confirmar, blur tardio, background, troca de dono/desmontagem | Callback inválido não inicia transporte; confirmação vigente opera uma vez |
| Leituras | Resposta fora de ordem, erro inicial, GET falha após ACK, repetir mesma busca | Estado ligado à consulta vigente; erro e retry visíveis; nunca repetir escrita para corrigir leitura |
| Conflitos | 409 em categoria/cartão/conta/compromisso/cronograma e campo sobreposto/disjunto | Rascunho preservado; origem/local/remoto visíveis; sobreposição exige decisão |
| Persistência | Fechar processo durante digitação, gravação/arquivo, fila, ACK e limpeza local | Recuperação com identidade estável; nenhuma perda ou duplicação silenciosa |
| Offline | Notificação, digitação, voz e arquivo com modo avião; reabrir e reconectar | Pendências locais visíveis antes da rede; transporte só após permitido; ACK correto |
| Captura nativa | Notificação aprovada/recusada/rodapé, duas compras iguais; câmera/galeria/PDF | Origem e ocorrência preservadas, revisão para ambiguidade; arquivos sobrevivem |
| Share | App frio, já aberto, bloqueado, sessão expirada, biometria cancelada; duas imagens | Intenção não some nem se aplica à captura errada; comprovar PS-R02 |
| Segurança | Blur sem background, active antes de focus, expiração e invalidação externa | Cobertura e rede privada respeitam sessão; sensor não entra em loop |
| Acessibilidade | TalkBack, fonte 200%, tela pequena, textos longos e erro/loader | Nome/papel/estado anunciados; fundo inacessível sob diálogo; ações legíveis e alcançáveis |
| Capacidade | Muitos itens/arquivos, armazenamento quase cheio e JSON corrompido | Erro visível sem apagar fonte; falha não equivale a fila vazia; medir latência/ANR |
| Atualização | Instalar sobre versão anterior com filas e rascunhos preenchidos | Dados e comandos intactos; assinatura/versão corretas; retomada e replay idempotentes |

Lacunas atuais: aparelho/IME/sensores/TalkBack; incidência no histórico real; carga prolongada/ANR/disco; extração externa e formatos bancários reais; recuperação de produção a partir de backup atual. A integração local de backup/restauração não preenche a última lacuna.

## Evidências e reexecução

Pasta: `C:\Users\danie\FIN\audit_work\adversarial-android-2026-10-09`.

- [Relatório interativo](../../audit_work/adversarial-android-2026-10-09/review.html), [inventário consolidado](../../audit_work/adversarial-android-2026-10-09/findings.json), [verificação](../../audit_work/adversarial-android-2026-10-09/verification.json) e [plano estruturado](../../audit_work/adversarial-android-2026-10-09/recovery-plan.json).
- [Backend e comandos](../../audit_work/adversarial-android-2026-10-09/backend/report.md), [Mongo real](../../audit_work/adversarial-android-2026-10-09/backend/mongo-results.json), [revisão crítica](../../audit_work/adversarial-android-2026-10-09/ui/validation-review.md).
- [Interface](../../audit_work/adversarial-android-2026-10-09/ui/report.md), [persistência/Kotlin/voz](../../audit_work/adversarial-android-2026-10-09/persistence/report.md), [integrações](../../audit_work/adversarial-android-2026-10-09/root/integration/integrations.json).
- [Proveniência APK](../../audit_work/adversarial-android-2026-10-09/root/apk-provenance.json), [assinatura](../../audit_work/adversarial-android-2026-10-09/root/apk-signature.log), [advisories Python](../../audit_work/adversarial-android-2026-10-09/root/python-advisories.json), [advisories mobile](../../audit_work/adversarial-android-2026-10-09/root/dependency-advisories.json).
- [Matriz visual](../../audit_work/adversarial-android-2026-10-09/root/browser-matrix-loaded.json) e [fingerprints iniciais](../../audit_work/adversarial-android-2026-10-09/root/sources-before.json).

**Atenção à semântica dos resultados:** os probes adversariais passam quando reproduzem a falha atual; isso não quer dizer que a funcionalidade foi aprovada. Os gates de regressão passam quando seus contratos existentes são atendidos. As duas contagens não devem ser somadas como testes de aprovação.

Os bancos sintéticos e seus arquivos foram preservados no diretório da auditoria. Para reexecutar integração, subir explicitamente o Mongo local isolado e usar a URI registrada; nunca usar o `.env` financeiro como alvo de teste. Não foi emitida conclusão de ausência de falhas para superfícies apenas inventariadas ou não exercitadas fisicamente.
