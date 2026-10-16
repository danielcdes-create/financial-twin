# Recuperação Android — 19 achados e SEC01

Implementação autorizada em 09/10/2026. A referência anterior é **1.20.32 / 78**, commit `e05bcf60d26c17054481dba2d409452224e5cd61`, tag `baseline-android-1.20.32`. A entrega prevista é **1.20.33 / 79**. Este registro distingue correção local, publicação e homologação física; os resultados finais são acrescentados após os gates.

## Preservação e rastreabilidade

Foi criada uma raiz Git em `financial-app`, preservando previamente os metadados originais de `mobile/.git`. Credenciais, chaves, dados pessoais, banco, mídia, dependências instaladas e artefatos gerados permanecem fora do versionamento. O bundle inicial foi verificado como recuperável; fontes, lockfiles, APK anterior, manifesto e hashes foram preservados. Evidências privadas: `../../audit_work/recovery-android-2026-10-09/`.

Com as gravações do serviço suspensas, foi coletado um backup canônico BSON e mídia. Restauração em destino vazio e isolado: **36 coleções, 6.865 documentos, 15 mídias, nenhuma mídia ausente**. Contagens, validadores/opções e hashes dos arquivos foram conferidos. A primeira restauração revelou perda do tipo BSON `Int64` no JSON relaxado; o utilitário passou a usar JSON canônico e ganhou regressão. O backup inicial e o destino incompleto ficaram preservados; a comprovação usa o novo destino isolado.

Testes de correção usam somente namespaces sintéticos em replica set local, com guardas antes de gravar. A base financeira não recebeu reparos por valor, data ou comerciante semelhantes. Fingerprints de oito coleções financeiras/de cadastro foram registrados para comparação após implantação: `transactions`, `obligations`, `credit_card_bills`, `installment_plans`, `accounts`, `commitments`, `commitment_amount_rules` e `account_profiles`.

## Resultado individual

| ID | Resultado | Correção e prova de aceitação local |
|---|---|---|
| BE01 | Corrigido e validado localmente | `account_usage` inclui compromissos/regras em todos os estados; transações compartilham a trava da exclusão. Corridas criação/alteração × exclusão não deixam órfãos. |
| BE02 | Corrigido e validado localmente | Manutenção conserva conta histórica encerrada/arquivada; novo vínculo exige conta selecionável. API e editor permitem editar/cancelar sem trocar a conta. |
| BE03 | Corrigido e validado localmente | Política comum para `futura`, `aberta`, `vencida`, `parcialmente_paga` com saldo positivo. Fatura corrigida de 100 para 120 permanece no planejamento, compra e projeção, sem dupla contagem. |
| BE04 | Corrigido e validado localmente | Automático exige referência bancária, provedor e conta duráveis iguais, além de compatibilidade financeira. Compras independentes iguais permanecem distintas; similaridade fica em revisão; replay conserva identidade. |
| BE05 | Corrigido e validado localmente | Sentinela `count + 1` somente em concluído: 1/1 e 600/600 aceitam observações, preservam pagamentos/datas e não projetam próxima cobrança. Reativação inválida é recusada. |
| BE06 | Corrigido e validado localmente | Nova voz usa UUID/instante da declaração; hash identifica conteúdo. Duas declarações iguais são duas capturas; replay é uma. Contexto/recibos legados concluídos continuam iguais. |
| PS01 | Corrigido e validado localmente | Parser nativo separa contexto da compra, comerciante e rodapé. Aprovação com limite disponível entra; recusa/estorno e ambiguidade não são transformados em compra certa. Backend conserva a evidência ambígua. |
| PS03 | Corrigido e validado localmente | Inbox v2 sob trava, fonte legada intocada, isolamento/exportação dos bytes inválidos e estados explícitos. Corrupção do próprio arquivo de recuperação também é preservada sem bloquear novas capturas. |
| PS04 | Corrigido e validado localmente | Importação local precede `/health`; contagem usa união de identidades/estágios. Origem só sai após ACK durável. Falha offline e interrupção após ACK retomam a etapa local, sem reenviar. |
| PS05 | Corrigido e validado localmente | Comando de voz selado antes da fila; arquivo conferido, retentativa mantém UUID/texto/instante. Falha de limpeza conserva finalização pendente. Recibo impede recriar arquivo/enqueue após remontagem. |
| UI01 | Corrigido e validado localmente | Comparação origem/local/remoto por parcela/campo; mescla disjunta, escolha para sobreposição e bloqueio de paga/removida. Conflitos consecutivos exigem nova comparação. |
| UI02 | Corrigido e validado localmente | Resultado associado ao snapshot integral e geração; mudar premissas/contexto/fluxos invalida e respostas antigas são descartadas. |
| UI03 | Corrigido e validado localmente | Parser compartilhado valida agrupamento original; consumidores de revisão, lote, compromissos, contas, cartões, captura e decisões conservam sinal/zero/limites próprios. |
| UI04 | Corrigido e validado localmente | Confirmação guarda validade do dono antes do diálogo e a confere imediatamente antes de transportar. Desmontagem/troca de editor impede DELETE/PATCH tardio. |
| UI06 | Corrigido e validado localmente | Erro persistente e retry da mesma consulta em Revisão; rascunho/editor não remontam. |
| UI07 | Corrigido e validado localmente | Sugestões distinguem carregamento, vazio e erro, com retry e geração; campos/foco preservados, resultado antigo recusado. |
| UI08 | Corrigido e validado localmente | Hierarquia de Back compartilhada com trava, diálogo, overlay, editor, subtela e Router. `/` e `/handle-share` têm um dispatcher ativo; App oculto não intercepta. Back durante mutação é descartado sem replay. |
| OP01 | Corrigido e validado localmente | Baseline Git local, commits/tags, execução dos gates em checkout limpo e bundle restaurável. Sem alegação de CI remoto. |
| OP02 | Corrigido e validado localmente | Promoção confere manifesto, configuração, assinatura e fontes; README deriva o ponteiro da versão/hash validada e conserva o histórico anterior. |
| SEC01 | Corrigido e validado localmente | PyMongo fixado em **4.18.2** no lock/ambiente; scanner incluído no gate local e workflow. Versão efetiva registrada pelo processo implantado. |

As provas anteriores da auditoria permanecem em `../../audit_work/adversarial-android-2026-10-09/`. As regressões atuais exigem o comportamento corrigido. SEC01 é separado dos 19 achados; **SEC02**, migração da chave de assinatura, permanece para outra entrega por decisão explícita do proprietário. A assinatura compatível atual será conservada.

## Validação e entrega

Os **19 achados e SEC01 estão corrigidos e validados localmente**; SEC02 continua separado conforme acordado. O commit de fonte do APK é `09c72dfdfda1fc5f42681a6564e95ede1f5e7ee2`. O gate `scripts/validate.ps1 -Integration -WebExport` terminou com código 0 em checkout limpo `C:\fin-r79`: **476 testes de backend**, scanner de dependências sem advisories conhecidos, **54 regressões do cliente**, verificações Expo/TypeScript/ESLint e exportação web. A exceção documentada para GHSA-vfj7-8cjw-p6xm permanece limitada à dependência Metro braces 3.0.3 já corrigida por guardas de profundidade e hashes revisados, e expira em 01/11/2026.

A validação Mongo em replica set local isolado passou **27 verificações em 23 namespaces sintéticos**, incluindo oito corridas de criação/alteração × exclusão. O processo executado e os lockfiles registram PyMongo **4.18.2**. Kotlin compilou e passou **46 assertions** do inbox nativo; isso simula as APIs Android e não substitui a execução em aparelho. Os componentes reais passaram **24 combinações de tela/largura** (12 telas em 320 e 390 px), sem overflow horizontal ou erro de console. O seletor de categoria manteve foco e altura do painel com muitas, uma e nenhuma opção; a geometria web não mede o teclado físico.

O APK ARM64 **1.20.33 / versionCode 79**, pacote `com.financialtwin.app`, foi compilado e promovido ao atualizador privado. SHA-256: `490892E5A87207A306D42E4F70660CFB7B340C13BC629E6B22F697966F4D78E8` (47.881.400 bytes). `apksigner` validou a assinatura v2 e o certificado coincide com o APK anterior (`FAC61745DC0903786FB9EDE62A962B399F7348F0BB6F899B8332667591033B9C`). O mapa vinculou **91 origens** às fontes Git limpas. O README foi atualizado pelo manifesto validado, e o alias/manifesto/proveniência anteriores foram preservados pelo script de promoção. O download integral por `/v1/app-update/apk` devolveu o nome esperado e bytes cujo tamanho e hash coincidem com o manifesto.

O backend local foi reiniciado pela tarefa oficial do Windows e está saudável em `127.0.0.1:8000`, com Mongo conectado e autenticação exigida. O log do processo registra PyMongo 4.18.2 vindo do `.venv` do projeto. `/v1/app-update` publica a versão/hash acima, as três rotas protegidas testadas retornaram 401 sem sessão e o OpenAPI inclui `captured_at` no multipart de extração. Após a implantação, fingerprints canônicos de oito coleções (`transactions`, `obligations`, `credit_card_bills`, `installment_plans`, `accounts`, `commitments`, `commitment_amount_rules` e `account_profiles`) coincidiram byte a byte com o registro anterior; nenhuma transação financeira foi reparada nem regravada.

A coleta consistente restaurada em destino vazio teve **36 coleções, 6.865 documentos e 15 arquivos de mídia, zero mídias ausentes**; contagens, validadores/opções e hashes foram conferidos. Evidências e logs reexecutáveis ficam fora do Git em `../../audit_work/recovery-android-2026-10-09/`, incluindo `backup-restoration.json`, `clean-gates.log`, `candidate-source-provenance.json`, `release-check.log`, `backend/versioned-gate-final/android-recovery-results.json`, `visual/checks.json` e `visual/selector-checks.json`.

## Limites e homologação pendente

Sem aparelho conectado ao ADB, seguem pendentes atualização sobre a instalação anterior, SharedPreferences/SQLite do telefone, teclado/IME, gestos, TalkBack/fonte ampliada, biometria, câmera/galeria, notificações reais e share sob bloqueio. Executar a [matriz física do plano](PLANO-RECUPERACAO-ANDROID-2026-10-09.md#homologação-android-obrigatória-para-encerrar-a-recuperação) no APK identificado. A release está publicada e validada no servidor; homologação física Android continua pendente.

O histórico será inspecionado somente em leitura. Contagens/provas de incidência serão registradas separadamente; ausência de acesso às filas do aparelho ou à intenção original não permite deduzir perda, duplicação ou reparo a partir de texto semelhante. Também permanecem os riscos investigativos UI-R01, UI-R02, PS-R01 e PS-R02, sem convertê-los em novos achados confirmados.
