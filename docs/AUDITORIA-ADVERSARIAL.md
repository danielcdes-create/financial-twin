# Auditoria adversarial — 15/10/2026

Julgamento da árvore local de `financial-app` a partir de `431aaf5`, com a correção de contagem dupla desta entrega, commit `cc7f562`. Dados sintéticos. O ledger ao vivo não foi lido, escrito, deduplicado nem reparado. O release privado no disco é Android 1.20.77 / versionCode 123, SHA-256 `B11A12A91149FA8EBE713726A3E33B88492C0D169AE6BB86935E15CE337039EE`, commit `c9b6d64`, com o certificado de debug anterior. A instalação física permanece unverified.

## Entrega 20/10/2026 — alias do vendedor

Commit do recurso `3fd8862`, empacotado em `c9b6d64`. APK ARM64 de 48116960 bytes, SHA-256 `B11A12A91149FA8EBE713726A3E33B88492C0D169AE6BB86935E15CE337039EE`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_performance_plan.py` (8) e `backend/tests/test_auth.py` (14). A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Alias | A busca do vendedor traz o alias inteiro. Lista incompleta faz a gravação reler o fornecedor antes de acrescentar o nome. |
| Conflito | Os códigos 24, 244 e 251 repetem o comando já gravado e respondem 409 com `X-Operation-Pending`. |
| Cache na escrita | Taxonomia, subcategorias, contexto histórico e identidade do dono leem fora da sessão da transação. |

## Entrega 20/10/2026 — cache de leitura

Commit do recurso `2f5b828`, empacotado em `f6b35e2`. APK ARM64 de 48116972 bytes, SHA-256 `5510F7F1D48F2064A7D2E9958E24809E4C25D6087B1B6E4A79204C34778806F8`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_performance_plan.py` (7), `backend/tests/test_telemetry_privacy.py` (5) e `backend/tests/test_auth.py` (14). A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Cache de referência | `unwrap_database` lê o banco no dicionário da instância. Os caches de classificação, contexto histórico, subcategorias e taxonomia passam a reconhecer o mesmo `Database`. |
| Captura | O `PATCH` de provisório não registra o caminho duas vezes e reaproveita os perfis já lidos. |
| Sessão | A identidade do dono fica em cache por 15 segundos e sai invalidada em login, logout, renovação e troca de conta ou senha. |
| Projeção | Compromissos, parcelamentos e obrigações pedem `batchSize` 2000. |
| Telemetria | A coleção fica ligada ao par conexão e pedido. O `getMore` usa o nome da coleção. |

## Entrega 20/10/2026 — leituras sem transação de consulta

Commit do recurso `03a8ce0`, empacotado em `76bde2f`. APK ARM64 de 48116960 bytes, SHA-256 `4649AB9546727392941366A510E36C3C72D80599553059339966A71C401B4FF4`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_performance_plan.py` (6), `backend/tests/test_auth.py` (14), `backend/tests/test_commitment_estimates.py` (15) e `mobile/scripts/test-overlay-transitions.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Consulta | `mongo_snapshot` não abre sessão nem `commitTransaction`. Num `Database` real o rótulo continua `snapshot`. Escritas seguem em `mongo_transaction`. |
| Cursor e classificação | Cursores de leitura pedem `batchSize` 2000. As opções de classificação saem de uma agregação por coleção. |
| Sessão | Um GET não grava `last_seen`. Numa escrita, a sessão segue numa thread e não segura a resposta. |
| Aba | Trocar de aba aborta o GET anterior. Contas e saldos já frescos não são pedidos de novo. No aparelho: unverified. |

## Entrega 18/10/2026 — cabeçalho ASCII

Commit do recurso `c217222`, empacotado em `01f41d6`. APK ARM64 de 48114604 bytes, SHA-256 `B28EF887D6277DE39DAF977DF386333F0019DD919C06BAE4245E692D4A6A91F7`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passou `mobile/scripts/test-telemetry-headers.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Login | `X-Client-Tab: Início` fazia o OkHttp rejeitar o pedido antes do envio (`char 0xed`). O cabeçalho sai como `Inicio`. |
| Ação | `Processando…` e `Voltando…` saem sem a reticência Unicode. |

## Entrega 18/10/2026 — telemetria e janelas do painel

Commit do recurso `89f3916`, empacotado em `5590258`. APK ARM64 de 48114040 bytes, SHA-256 `DD0A21A417AA14E510E72B5F6D40437E91A78859FCA62036507BADFD69A59A60`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_telemetry_privacy.py` (4), `backend/tests/test_performance_plan.py` (3), `backend/tests/test_commitment_estimates.py` (15), `test-user-operation.cjs`, `test-connection-recovery.cjs`, `test-interaction-lock.cjs`, `test-commitment-lists.cjs` e `test-app-transitions.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Telemetria | `POST /v1/telemetry/events` e `GET /v1/telemetry/report` exigem sessão. O registro guarda tempos e rótulo de rota. Consulta, valor, identificador e texto de erro ficam de fora. |
| Janela do painel | a competência usa faixa indexável e o filtro de mês válido. Intervalos sobrepostos da mesma leitura são fundidos. O snapshot continua sendo a fonte do período. |
| Índices | seis índices aditivos na subida: competência, revisão, evidência e auditoria. O ledger não é reescrito. |
| Vendedor em lote | candidatos de pagamento e a página do extrato enriquecem a página com uma consulta, não uma por linha. |
| Projeção inicial | abrir Compromissos não dispara `/v1/commitment-projection` de novo. Uma gravação ainda atualiza o gráfico. |

## Entrega 18/10/2026 — aba visível e contexto histórico

Commit do recurso `804b79a`, empacotado em `1509ed6`. APK ARM64 de 48097660 bytes, SHA-256 `E169973B1FF6CCEA0682700B2AC529A658542D1D17E77ED8CC279BA26B61B99F`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_category_catalog_consistency.py` (6) e a compilação dos módulos alterados. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Troca de aba | a rolagem nativa permanece. Só a aba visível busca de novo; as outras atualizam ao abrir. |
| Confirmação na revisão | o item sai da lista na hora. A página recarrega em segundo plano, sem pedir de novo as opções de classificação. |
| Contexto histórico | fica em memória por 120 segundos e cai junto com o cache de classificação numa escrita. |
| Gravação de captura preenchida | não varre o histórico para repetir vendedor e plataforma. |
| Fila de revisão e saldos | eventos, capturas, lançamentos e extratos vinculados saem em consultas `$in`. |
| Catálogo de categorias | `categories` é lida uma vez. |

## Entrega 18/10/2026 — formatação e leituras repetidas

Commit do recurso `273e317`, empacotado em `2807461`. APK ARM64 de 48095372 bytes, SHA-256 `0520D938561486CA1B3340194365E940720F4412E6A2DB8CDD0C5E61834C505E`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_commitment_estimates.py` (15) e a compilação dos módulos alterados. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Formatadores de moeda e data | reutilizados no módulo. Listas, cards e gráficos não criam um `Intl` novo a cada render. |
| Figuras do quadro de compromissos | calculadas uma vez por card. O painel fechado não é limpo de novo. |
| Rascunho da revisão e do lote | a fonte é serializada uma vez. A gravação SQLite concorrente fica na última linha pedida. |
| Rótulos de parcelamento | a busca dos vínculos é `$in`, com texto e `ObjectId` válidos. Não há varredura da coleção. |
| Contexto histórico da página | capturas, extrato, contas e subcategorias são lidos uma vez e reutilizados na sugestão. |
| Lista de compromissos | vigência, extrato vinculado e nome do cartão saem em lote. |
| Cache de classificação | prévia e revisão não derrubam as opções. A trava de inserção incrementa uma vez por transação. |

## Entrega 18/10/2026 — fila, compartilhamento e mídia órfã

Commit do recurso `a7c071f`, empacotado em `5b31361`. APK ARM64 de 48095572 bytes, SHA-256 `94D3BB0F984BA651D3C689EF7571D3CF18CF9361E2B042FFCBFC2439B0B42841`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_media_recovery_time.py` (3), `backend/tests/test_auth.py` (14), `mobile/scripts/test-node-forge-security.cjs` e `mobile/scripts/test-braces-security.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| PS-R02, share com sessão bloqueada | defect fixed no código. A intenção fica em `__financialTwinPendingShare` e segue para `/handle-share` depois do desbloqueio. App frio, bloqueio e biometria no aparelho: unverified. |
| PS-R01, inbox sem orçamento | defect fixed no código nativo. A fila guarda no máximo 500 eventos e 64 entradas de recuperação. Fila grande e disco baixo no aparelho: unverified. |
| Exceção GHSA do `node-forge` e do `braces` | a janela auditada passa a 2027-11-01T00:00:00Z. O hash e a rejeição RSA/AST continuam conferidos. Não houve release oficial que substitua o pacote. |
| Mídia órfã | defect fixed. Depois de 7 dias sem referência, o arquivo sai do disco e o registro fica `purged`. |
| Limite de login do proprietário | defect fixed. Um login válido zera o balde compartilhado `private-owner`. |
| Nome padronizado do vendedor | o índice `merchant_standard_name` em `merchants.nome_padrao` é consultado antes da varredura. |

## Entrega 18/10/2026 — sessão biométrica

Commit do recurso `ef97ad5`, empacotado em `dcbd443`. APK ARM64 de 48095144 bytes, SHA-256 `4AC9D24CD14C8AC3189290226EDD55CBE8AB99C8D32A906C28EB8F0225B2D790`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_auth.py` (14), `mobile/scripts/test-account-security.cjs` e `mobile/scripts/test-auth-transitions.cjs` (10). A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Sessão nativa de 12 horas | defect fixed. No aplicativo, a sessão de dispositivo e a biometria duram 30 dias e se renovam a cada validação em `/v1/auth/account`, com o mesmo `jti`. No navegador continua 12 horas. |
| Segundo pedido de digital ao renovar | defect fixed. A renovação usa o envelope já protegido pelo hardware. Um blur ou uma oscilação de rede depois da leitura do sensor não abre outro `BiometricPrompt`. |
| Samsung Pass reinicia a biometria | defect fixed. Na tela bloqueada, o preenchimento automático não apaga os campos nem recomeça o ciclo biométrico. Cancelar o sensor, trocar a digital ou receber 401 ainda pede senha. |

## Entrega 17/10/2026 — selo e esforço em Cabe na minha vida

Commit do recurso `89ddc4a`, empacotado em `49253ef`. APK ARM64 de 48087604 bytes, SHA-256 `7CF20E65021FC28CE5B4E2A055C7F2AC4C38B7FF51A338F894510C4EC8EDE38F`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `mobile/scripts/test-purchase-copy.cjs`, `mobile/scripts/test-purchase-card.cjs`, `backend/tests/test_purchase_horizon.py` e `backend/tests/test_purchase_check.py`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Análise sem síntese no topo | defect fixed. **Sua análise** abre com um selo que cruza caixa, reserva mínima e efeito na meta. Sem saldo inicial, o selo fica no esforço e na meta. |
| Esforço só em horas totais | defect fixed. A tela mostra a parcela mensal, os dias úteis de 8 horas, as horas de cada parcela e a renda-hora considerada. |
| Alternativas sem destaque prático | defect fixed. O texto compara o saldo final com e sem a compra. Comprar agora, esperar 30 dias e a opção 20% mais barata ganham um destaque de parcela, fôlego ou economia. |
| Cartão sem a parcela | defect fixed. O cartão leva preço, parcelas, horas, dias úteis e adiamento. Salário, saldo, dívida, produto, loja e nome da meta continuam de fora. Teste `test-purchase-card.cjs`. |

## Entrega 17/10/2026 — seleção e parcelas em Cabe na minha vida

Commit do recurso `7f16fd3`, empacotado em `27cd105`. APK ARM64 de 48076468 bytes, SHA-256 `9C0F69848E950EF1D96DF13A0CABCAB62839F6A507BAF760846564628E040DA5`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_purchase_horizon.py` e `mobile/scripts/test-purchase-copy.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Compra parcelada fora da lista | defect fixed. `purchase-context` inclui `installment_plans` ativos e compromissos `parcelamento`, com nome, data e valor. Parcela já vinculada não entra de novo. |
| Selecionar Tudo e Limpar seleção | defect fixed. O topo marca ou limpa todos os compromissos listados. Cada compromisso, inclusive financiamento e compra parcelada, tem **Selecionar todo o Compromisso** e **Limpar seleção**. A obrigação importada que não é fatura continua desmarcada até o toque. |
| Vencido antes do início da análise | defect fixed. A ocorrência anterior ao início entra no primeiro dia do período e guarda a data original. |

## Entrega 17/10/2026 — lista de Cabe na minha vida

Commit do recurso `ef9e218`, empacotado em `e868558`. APK ARM64 de 48075140 bytes, SHA-256 `B1254751610714B7F3B801D38A820803E17E09BC65C58C79C67B6CD0CB6F1D6F`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram `backend/tests/test_purchase_horizon.py`, `backend/tests/test_purchase_check.py` e `mobile/scripts/test-purchase-copy.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Compromisso pago no cartão fora da lista | defect fixed. `purchase-context` devolve cada ocorrência com nome, data e valor. O planejamento geral continua sem repetir a compra quando a fatura entra. |
| Fatura consolidada na mesma lista | defect fixed. `fatura_cartao` e `fatura` não entram nas linhas nem nas sugestões dessa tela. |
| Selecionar Todos | defect fixed. Um controle marca as ocorrências de um compromisso; outro marca todos os compromissos listados. A obrigação importada que não é fatura continua desmarcada até o toque. |
| Restante de R$ 0,00 | defect fixed. Com todas as parcelas dentro do período, o texto diz que o período já inclui todas as parcelas desta compra. O restante só aparece quando é maior que zero. |
| Menor saldo sem a renda informada | defect fixed. Com renda líquida mensal, a avaliação credita esse valor uma vez por mês civil. Sem renda, o saldo anterior permanece. Nenhum lançamento é criado. |

## Entrega 17/10/2026 — pré-preenchimento da captura pelo histórico

Commit do recurso `11b9f2f`, empacotado em `da40064`. APK ARM64 de 48072916 bytes, SHA-256 `6EBA45E048CEA22084FF28EC54A5F807418CCBE7318997A2D7B39D769FC738C7`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram 6 testes de plataformas e `mobile/scripts/test-vendor-suggestions.cjs`. A suíte completa do cliente e a homologação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Descritor de maquininha chega sem vendedor | defect fixed. A ingestão e a leitura consultam capturas confirmadas e o extrato do mesmo fluxo. Correspondência exata, prefixo truncado e plataforma mais prefixo da loja preenchem os campos vazios. |
| Alias descartado quando o vendedor já existe | defect fixed. Confirmar ou corrigir um descritor com identificação da loja além da plataforma grava o alias no cadastro. Texto só de plataforma não vira alias. |
| Fila já aberta não herda o histórico | defect fixed. A leitura anexa `historical_defaults`. A revisão preenche só campos vazios e avisa para conferir antes de confirmar. |
| Chip some por causa do prefixo da maquininha | defect fixed. O prefixo deixa de derrubar o score abaixo de 0,28. Tocar no chip preenche os campos vazios com o perfil daquele vendedor. |

## Entrega 17/10/2026 — correções P0–P2

Commit das correções `a36867b`, empacotado em `8ddf172`. APK ARM64 de 48063132 bytes, SHA-256 `BC8B3B7A3DBD74E9F7D511D2AC2FC59F76246C371FF4EA0133B9E00B9E67F5C0`, assinante `FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`. O ledger ao vivo não foi lido nem escrito. Nesta passagem passaram 6 testes de perfil, 2 de corte, 15 de compromissos e 11 de projeção. A suíte completa do cliente e a instalação física não foram repetidas.

| Correção | Veredito |
| --- | --- |
| Perfil não regrava extrato pago, vinculado ou protegido | defect fixed. Valor e data só alcançam o lançamento em aberto. Reclamação `ledger:<id>`, reconciliação e vínculo com `paid_date` ou `settled_next` bloqueiam a regravação. |
| Limpeza de subcategoria, vendedor e plataforma | defect fixed. Subcategoria limpa sai por `$unset`. Trocar o vendedor limpa `merchant_id`. Trocar a plataforma limpa `platform_id`. |
| Âncora do cadastro gerido | defect fixed. Nova data de vencimento grava `schedule_anchor_day` e `schedule_anchor_end`. |
| Identidade da gravação | defect fixed. Perfil e compromisso futuro devolvem `request_id`. O perfil toma `lock_movement_insert`. |
| Data-base da parcela | defect fixed. A agenda e o vínculo usam `base_due_date`. `settled_next` falso com `occurrence_key` não consome a ocorrência. A próxima cobrança avança pela data-base. |
| Corte da projeção | defect fixed. A projeção mensal usa `commitment_cutoff` da data da consulta, não o relógio de hoje. O piso continua 2026-10-01. |
| Data civil no cliente | held. Ano, mês e dia saem das partes de `America/Sao_Paulo`, para `Intl.format` não reordenar o dia. |
| Fila local | held. O diário passa a guardar perfil, desfazer de compromisso e as rotas de pagamento. |
| Classificação em revisão dentro da transação | defect fixed. O rollback não apaga o comando `needs_review`. Trocar a categoria de uma captura provisória incrementa `revision`. |

Cada superfície recebe um veredito: `held`, `defect fixed`, `still open` ou `owner-blocked`. O que só um aparelho pode fechar fica `unverified`.

| Superfície | Veredito |
| --- | --- |
| Invariantes do ledger | defect fixed |
| Dinheiro e sinal | defect fixed |
| Data civil `America/Sao_Paulo` | held |
| Classificação armazenada | held |
| Compromissos e corte móvel de pago assumido | held |
| Captura e fila local | held |
| Cartões e contas | defect fixed |
| Dashboard | defect fixed |
| Autenticação e privacidade | held |

| Residual absorvido | Veredito |
| --- | --- |
| E1, editar parte das parcelas futuras de um compromisso gerido | defect fixed. O menu abre o editor também no cadastro gerido. A alteração fica em `installment_overrides`, sem regravar o extrato. |
| Comportamento novo de obrigação pontual | owner-blocked. O `kind obrigacao` da nota v5 já existe. Não houve comportamento além dessa nota. |
| Corte móvel de pago assumido | defect fixed. O corte é o dia 1 do mês civil, e nunca anterior a 2026-10-01. |
| Schema ou semântica do ledger, inclusive total de banco diferente de parcela × quantidade | owner-blocked. Não implementado. |
| SEC02, trocar a chave de debug | owner-blocked. Não implementado. |
| UI-R01, leitura depois do ACK | held no helper compartilhado. Medição no aparelho: unverified. |
| UI-R02, blocos acima do campo ativo | still open. IME físico: unverified. |
| PS-R01, inbox sem orçamento de retenção | still open. Fila grande e disco baixo: unverified. |
| PS-R02, share com sessão bloqueada | still open. App frio, bloqueio e biometria: unverified. |
| Homologação física (instalação, IME, gestos, TalkBack, biometria, câmera, notificação, share) | unverified. Sem ADB. O APK 1.20.70 está no disco; a instalação física não foi feita. |
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

held para a apresentação já entregue, com o corte móvel a partir de 16/10/2026. O piso continua `2026-10-01`. No mês corrente, o corte é o dia 1 desse mês. 30/09 sem vínculo é Pago (assumido). Uma cobrança do mês anterior sem vínculo passa a Pago (assumido) quando o mês vira. Uma cobrança do mês corrente sem vínculo, com o dia já passado, é Vencido · sem vínculo e entra em A conciliar. Uma cobrança futura sem vínculo é Previsto e fica fora de A conciliar. Vínculo continua Pago vinculado. Âncora 31/01, 28/02, 31/03. Obrigação de 100 em 3 parcelas: 33,33 + 33,33 + 33,34. Sugestão automática não casa setembro com o vencimento de outubro. A parcela 1 do extrato não substitui o total do plano. Testes `test_fixed_cutoff_schedule_and_suggestion_window`, `occurrenceState`, `reconcileCharge`, `obligationShare`, `anchoredDue` e `parcelamentoPurchaseSummary`.

| Residual absorvido | Veredito |
| --- | --- |
| E1, editar só parte das parcelas futuras de um compromisso gerido | defect fixed. “Editar parcelas futuras” abre no cadastro gerido. Cada parcela guarda valor, vencimento ou cancelamento em `installment_overrides`, pela data-base. |
| Comportamento novo de obrigação pontual | owner-blocked. O `kind obrigacao` já existe. Não houve comportamento além da nota v5. |
| Corte que acompanha o mês | defect fixed. `commitment_cutoff` usa o primeiro dia do mês civil, com piso em 2026-10-01. |
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
| Homologação física: instalação, IME, gestos, TalkBack, biometria, câmera, notificação e share | unverified. Não há aparelho ADB nesta sessão. O APK 1.20.70 está no disco; a instalação física não foi feita. |

## O que as auditorias anteriores deixavam aberto

Os registros datados foram absorvidos por esta nota e apagados. A evidência em `audit_work` permanece. NT-001, o runbook, UI-STABILITY, FUTURE-COMMITMENTS e os contratos de área continuam sendo a regra viva. Onde esta nota e um status antigo discordam, vale o veredito desta nota para o estado do código, e a NT-001 para o invariante.

owner-blocked e não implementados: obrigação pontual além da nota v5, mudança de schema ou de semântica do ledger, e SEC02. E1 e o corte que acompanha o mês, com piso em 2026-10-01, passaram a valer com o OK de 16/10/2026. Checagens que exigem aparelho, ADB ou o ledger ao vivo permanecem unverified.
