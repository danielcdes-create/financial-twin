# Estabilidade da interface: foco, estados e transições

Atualizado em 2026-10-09. Este contrato se aplica a todas as telas do cliente, inclusive novas telas e refatorações. Leia-o antes de alterar seletores, diálogos, ações de gravação, fila, teclado, formulários, listas, navegação, privacidade ou requisições que atualizam a interface.

## Recuperação da auditoria Android — 1.20.33

Os controles compartilhados e seus consumidores devem obedecer às mesmas regras:

- **Conflitos:** conservar origem, rascunho e versão remota até decidir. Comparar por campo e identidade de parcela; mesclar apenas alterações disjuntas. Valor, data e cancelamento sobrepostos exigem escolha explícita. Parcela paga ou removida não pode receber o patch anterior. Uma segunda recarga ou um novo 409 não aceita diferenças pendentes nem transforma o remoto em origem sem decisão.
- **Consultas:** vincular cada resultado ao snapshot integral de premissas, contexto e geração. Alterar valor, prazo, cobrança, fluxos selecionados, conta ou objetivo invalida a análise. Descartar respostas antigas. Erro de leitura permanece visível com retentativa da mesma consulta; carregamento, catálogo vazio e falha são estados diferentes. O editor, os seletores e seu foco continuam montados.
- **Valores:** `utils/moneyInput.ts` valida o texto original antes de remover agrupamentos. `1.234,56` e `1234.56` são válidos; `1.2,34`, expoentes e casas extras são recusados. Cada domínio informa sinal, zero e limite próprios. Contas/saldos, cartões, compromissos, revisão, lotes, captura, fechamento e decisões compartilham o parser.
- **Dono:** capturar a validade antes de abrir uma confirmação e conferir novamente imediatamente antes do transporte. Desmontagem, troca de registro/editor ou nova geração invalida callbacks; o commit de desmontagem usa `useLayoutEffect`.
- **Voltar:** `utils/localBack.ts` centraliza a ordem trava → diálogo → seletor → editor → detalhe/subtela → Router/sistema. O App registra o listener somente quando sua rota está ativa, inclusive `/handle-share`. O cronograma e o detalhe na camada da Activity entram nessa ordem, acima da página: o menu aberto fecha antes do painel, e um listener próprio não pode fechar o painel sem passar pelo dispatcher. No web essa camada não assina o Voltar nativo. Ações durante mutação são descartadas sem replay. Rascunhos duráveis são sincronizados antes de sair; campos voláteis alterados permanecem protegidos no editor.

Regressões de aceitação: `test-android-audit-ui.cjs`, `test-android-local-back.cjs`, `test-schedule-back.cjs`, `test-native-inbox-offline.cjs`, `test-voice-evidence-recovery.cjs` e as suítes compartilhadas descobertas por `run-client-tests.cjs`. Geometria e foco usam os componentes reais em 320/390 px. Esses resultados locais não comprovam IME, gestos, TalkBack, biometria ou armazenamento no aparelho. [Auditoria atual](AUDITORIA-ADVERSARIAL.md).

## Incidente e evidência

O proprietário relatou que, ao capturar uma compra e tocar em categorias, o menu ficava subindo e descendo repetidamente, mesmo após mais de 20 tentativas de correção. Após a alteração de geometria e teclado descrita abaixo, respondeu: **“Deu certo, revise aspectos semelhantes em todas as telas do app”**. Essa confirmação refere-se ao problema original; não certifica todos os fluxos revisados depois.

Há dois mecanismos distintos a preservar na documentação:

1. **Foco da janela Android:** o antigo `Modal` nativo criava um Dialog e provocava `AppState.blur` com a Activity ainda ativa. Isso interagia com a cobertura de privacidade e suspensão das leituras. A reprodução automatizada demonstrou blur, suspensão e cancelamento; não demonstrou fisicamente um ciclo completo de desmontagem/reabertura. Veja [SELECTOR-FOCUS.md](SELECTOR-FOCUS.md).
2. **Geometria do painel e teclado:** um painel dimensionado pelo conteúdo de uma lista virtualizada permite que medições e quantidade de linhas alterem seu próprio espaço disponível. Focar a busca automaticamente também abre o teclado e redimensiona a janela durante a abertura. A correção mantém altura definida, lista flexível dentro dela e busca por toque explícito. Não atribua todo movimento visual a autenticação ou a um loop de renders sem medir.

## Aprendizado recorrente: estados e transições

Em 2026-10-04, renomear ou excluir categorias/subcategorias voltou a apresentar “Consulta cancelada”, mudança para “Conectando” e bloqueio em “Processando...”. A revisão transversal encontrou falhas semelhantes em outras áreas. Esses sintomas exigem investigar o contrato compartilhado de foco, execução e persistência, além da tela onde aparecem. O [registro desta revisão](#registro-de-2026-10-04--revisão-transversal) conserva as evidências e seus limites.

**Um diálogo nativo pode emitir blur depois que seu callback de confirmação já iniciou a gravação.** Esperar a retomada do foco antes de iniciar o callback não cobre essa corrida. Essa constatação substitui a antiga orientação de tratar a espera de retomada do `Alert` nativo como solução geral. Confirmações, opções e avisos comuns devem usar `utils/appAlert.ts` e `AppDialogHost` dentro da Activity. Não importar `Alert` nativo diretamente nem optar por `{ inActivity: false }` em produção sem necessidade concreta documentada e reprodução do ciclo de foco. Biometria e seletores de arquivos do sistema mantêm seu fluxo nativo e sua proteção de sessão.

Ao implementar ou revisar qualquer fluxo assíncrono, preserve estes critérios:

1. **Uma ação por vez:** adquirir `userOperation` antes do primeiro `await`, retornar a Promise em toda a cadeia de handlers e ignorar toques, edição, navegação e Voltar durante a operação. Não enfileirar nem executar essas interações quando a trava for liberada. Botões do diálogo também verificam a trava no callback.
2. **Gravação e recarga são etapas distintas:** só confirmar sucesso após o ACK exigido e a persistência local obrigatória. Leituras necessárias para o próximo token/conflito continuam aguardadas; recargas de agregados ficam em segundo plano. Falha de leitura após o ACK deve comunicar “salvo; não foi possível atualizar”, preservando o estado confirmado. Não repetir uma escrita financeira para recuperar uma leitura cancelada.
3. **Suspensão não é uma tentativa fracassada da fila:** `AbortError` provocado pela suspensão interrompe o ciclo, sem aumentar tentativas, backoff ou quarentena e sem reinício imediato. Preservar comando, identidade imutável, arquivo e recibo. Uma retomada deve respeitar o protocolo de ACK e idempotência existente.
4. **Todo resultado tem um dono:** capturar dono/geração antes do `await` e validar resultado, erro, `finally` e callbacks de interface; invalidar na desmontagem. Concluir a persistência durável obrigatória mesmo se a tela saiu, mas não abrir avisos, navegar nem sobrescrever outra tela depois disso. Invalidar uma busca também deve liberar seu indicador de carregamento.
5. **Conflito preserva o rascunho:** após 409, recuperar o registro e token atuais e comparar versão de origem, rascunho e versão remota. Campos intocados usam o valor remoto; alterações sobrepostas exigem aceitação explícita. Não descartar edições nem sobrescrever silenciosamente a versão nova.
6. **Identidade e geometria permanecem estáveis:** manter provedores de autenticação, SQLite e overlays montados. Usar `key` apenas para a identidade do registro, nunca texto, erro, consulta ou contador. Para os seletores, preservar altura independente do conteúdo, lista restrita, teclado dispensado e busca sem foco automático. Medir posição relativa ao viewport/contêiner, rolagem e foco após estabilização; posição absoluta isolada não comprova regressão.
7. **A invalidação precisa ocorrer no commit:** alterações de dono e desmontagem invalidam callbacks em `useLayoutEffect`, antes de callbacks de layout de outros componentes. Verificar validade também antes de iniciar transporte ou outra janela do sistema. Seleção e fechamento pertencem à instância da abertura do painel, não apenas ao dono que pode reabri-lo.
8. **Vazio explícito não é campo omitido:** ao limpar um campo opcional, enviar a remoção e validar seu efeito no backend. Categoria e escolhas obrigatórias não admitem branco. Ao mudar categoria, remover a subcategoria antiga. Sugestões são apresentadas separadamente; reabrir o editor não pode restaurar uma plataforma removida pelo usuário. `natureza_sugerida` não preenche a natureza persistida nem o rascunho: editar uma observação ou validar a revisão conserva a natureza anterior, inclusive vazia em registros antigos. Somente uma seleção explícita aceita a sugestão. Rascunhos antigos sem natureza na fonte podem conter a inferência preenchida pela versão anterior: preserve a cópia original antes de migrar, mantenha nota e demais edições, e mostre essa natureza como valor antigo ainda não confirmado. A versão de intenção `2` do rascunho conserva escolhas explícitas futuras; seus metadados e o valor antigo não entram no payload financeiro. O patch envia alterações do usuário e remoções explícitas; abrir/salvar o formulário não confirma vendedor, localidade, datas ou outros campos inferidos. As regressões de intenção de classificação em `test-domain-transitions.cjs` e `backend/tests/test_domain_classification_fixes.py` cobrem nota, validação, rascunho legado, retomada após seleção explícita e escolha explícita nas duas origens.
9. **A fila usa identidade completa:** envio, falha, tentativa manual e descarte conferem `id`, `event_id`, rota, payload e identidade do arquivo. Mudanças concorrentes não podem herdar falhas nem ter seu estado apagado. ACK e identidade do comando continuam preservados; rejeição definitiva permite corrigir, enquanto `409` retryable mantém a mesma operação pendente.
10. **Activity ativa não implica foco Android:** após `blur`, o evento `active` não libera sozinho a cobertura nem a rede privada. A validação biométrica espera foco e possui cancelamento próprio. Toda invalidação de `authState`, inclusive externa ao provider, invalida a geração anterior.

Não corrigir esses sintomas desligando privacidade, sessão ou cancelamento, forçando remontagens ou acrescentando atrasos arbitrários. Uma regressão em componente compartilhado exige revisar seus consumidores em todas as telas. Atualize este contrato e a orientação em `mobile/AGENTS.md`; um registro de release isolado não basta para orientar futuras alterações.

Para validar, reproduza também blur posterior à confirmação, suspensão durante a fila, ACK seguido de falha de recarga, resposta antiga após edição/desmontagem, conflito e interação durante processamento. Use os componentes reais com dados sintéticos e preserve o histórico financeiro. Os testes relevantes incluem `test-category-transitions.cjs`, `test-dialog-transitions.cjs`, `test-state-transitions.cjs`, `test-user-operation.cjs` e `test-interaction-lock.cjs`, já incluídos no runner. Confira foco/geometria renderizados e registre separadamente o que foi executado no aparelho; testes simulados e APK assinado não comprovam execução física.

## Contratos que devem permanecer

### Exclusão explícita de rascunhos

Todo formulário com edições descartáveis oferece **Excluir rascunho**, inclusive durante conflito de versão. A confirmação usa `appAlert` dentro da Activity, identifica as edições que serão descartadas e preserva o registro de origem. Na Revisão, a ação aparece no editor e no cartão de retomada; lotes e voz também oferecem exclusão local. **Mais → Rascunhos** lista revisões, lotes, voz e cópias preservadas, permitindo excluir uma cópia específica sem atingir outras do mesmo registro. Formulários em memória retornam aos valores de origem ou ao formulário vazio de cadastro.

A decisão explícita de excluir não arquiva novamente o rascunho. Aguarde a cadeia de salvamento local, invalide callbacks da versão descartada e mantenha uma referência às gravações mesmo após a desmontagem. Comparações e transcrições tardias não podem recriar o conteúdo. Abrir/reabrir um formulário sem novas edições não cria outro rascunho. No painel, confira a identidade e o conteúdo listado; a exclusão usa esses valores como condição tanto em SQLite quanto no armazenamento web. Se o conteúdo mudou, peça atualização da lista. Falha de armazenamento conserva o conteúdo e permite tentar novamente; só anuncie sucesso após a exclusão persistida.

Rascunho e tentativa financeira enviada são estados diferentes: `review_commands`, ACKs, fila e `commitment-create` sem recibo continuam protegidos. O painel identifica o cadastro enviado como **Cadastro aguardando confirmação** e oferece conferir a tentativa em Compromissos, para não permitir duplicação após um resultado incerto. Excluir o rascunho de revisão não exclui seu comando pendente, captura, lote, arquivo original ou movimentação. O rascunho de voz perde as referências ao áudio/transcrição; bytes que possam ser compartilhados com fila ou cópia preservada não são apagados sem comprovação de ausência de referências.

As regressões `test-local-draft-deletion.cjs`, `test-review-draft-deletion.cjs`, `test-voice-draft-deletion.cjs` e `test-local-draft-discard.cjs` exercitam confirmação/cancelamento, persistência, falha de limpeza, respostas atrasadas, remount, dono do diálogo e bloqueio de interações. O teste de armazenamento usa SQLite real em memória e o adaptador web de produção com dados sintéticos; não comprova armazenamento, teclado ou microfone físicos no Android. `test-app-transitions.cjs` cobre também descarte da captura manual e navegação até o painel.

A validação de 2026-10-06 aprovou 48 scripts do cliente, TypeScript, lint, compatibilidade SDK e exportação desktop. A inspeção dos componentes reais em 320/390 px cobriu Revisão com conflito, Lote, Voz e painel de Rascunhos; confirmou ausência de overflow e exclusão persistida após recarregar, usando dados sintéticos em origem separada do app real. Uma revisão independente reproduziu a corrida entre gravações de montagens diferentes: `trackDraftWrite` mantém um conjunto de Promises por banco/tipo/identidade e a exclusão aguarda todas. [Evidência visual](../../audit_work/draft-deletion-2026-10-06/visual/verification.json), [regressões](../../audit_work/draft-deletion-2026-10-06/client-final.log). Nenhum aparelho ADB estava conectado; não houve execução física do Android nem alteração do histórico financeiro real.

A versão Android **1.20.27/código 73** foi compilada e disponibilizada no atualizador. O APK mantém o certificado anterior, verifica assinatura v2, inclui 82 fontes do projeto iguais ao checkout validado e usa o endereço Tailscale configurado. O download integral pelo endpoint do atualizador conferiu tamanho e SHA-256 com o pacote gerado; a versão 1.20.26 foi preservada. [Verificação consolidada e limites](../../audit_work/draft-deletion-2026-10-06/verification.json).

### Categoria ausente nos filtros de movimentações

O agrupamento “Sem categoria” do Início e os filtros do Extrato/Revisão devem usar o mesmo significado: campo ausente, `null`, string vazia ou o rótulo histórico explícito “Sem categoria”. Não buscar apenas o rótulo literal nem gravá-lo nos registros para corrigir a apresentação. A consulta compartilhada está em `backend/app/category_filter.py`; aplica-se ao ledger, às capturas, à paginação por cursor e à compatibilidade por offset. Filtro vazio continua significando todas as categorias, e conta, datas e status continuam restringindo a consulta.

Em 05/10/2026, a reprodução com o banco atual mostrou R$ 281,03 em nove capturas confirmadas sem categoria, enquanto o filtro literal retornava zero. `backend/tests/test_uncategorized_movements.py` cobre equivalência com o agregado, ausência/null/vazio/rótulo histórico, categorias normais, cursor sem repetição, offset, contas, datas e revisão. A conferência de dados reais fica em `../audit_work/uncategorized-verification.json`. A correção é de consulta no servidor, sem reclassificação financeira.

Na extensão transversal de 05/10/2026, preserve também estes contratos:

- Um toque em **Gastos por categoria** consulta `period_basis=economic&spending_only=true`: competência declarada, data em Brasília quando não houver competência, apenas capturas confirmadas e papéis econômicos de despesa/estorno. O Extrato geral mantém o período de caixa e os estados de captura existentes. Atualização, cache e próximas páginas conservam todos os filtros; Remover/Limpar restaura a consulta geral.
- Cursor e offset compartilham a mesma consulta, incluindo conta efetiva revisada, transferências com as duas contas e busca por vendedor/plataforma. Contagens e linhas são lidas no mesmo snapshot de MongoDB em Extrato, transações e Revisão. Cursores novos pertencem aos filtros de origem; datas invertidas retornam erro. Os cursores antigos permanecem legíveis por compatibilidade.
- Categoria persistida e inferência são separadas. Em capturas, `categoria_sugerida` é o campo financeiro existente; a inferência posterior usa `categoria_inferida`. Não preencher o campo financeiro nem o rascunho apenas ao ler o registro. Início, Extrato, Detalhe, Revisão, Experimentos e Fechamento usam a classificação efetivamente persistida, incluindo o agrupamento ausente.
- Não truncar os agregados por categoria: limites de apresentação não podem fazer uma categoria válida parecer ter gasto zero em outra tela. O catálogo retorna nomes como strings; consumidores aceitam strings ou objetos legados. Contagens históricas e gastos de um período têm legendas distintas.
- Catálogo, opções de classificação e contexto de subcategorias somam o histórico do ledger, as categorias personalizadas ativas e as classificações persistidas das capturas `provisional`, `enriched` e `confirmed`. Capturas canceladas/descartadas preservam o histórico, mas não recriam uma opção excluída, bloqueiam sua recriação explícita ou servem de categoria substituta. Criação e exclusão usam esse mesmo catálogo.
- Linhas e totais de um filtro anterior não aparecem sob o novo filtro. Extrato, Revisão e Cartões identificam a consulta à qual os resultados pertencem; somente a resposta correspondente é exibida. Contas e o filtro de pendências do Fechamento filtram localmente o mesmo conjunto carregado; trocar mês já limpa os dados anteriores.

Regressões: `backend/tests/test_filter_consistency.py`, `backend/tests/test_category_catalog_consistency.py`, `mobile/scripts/test-filter-consistency.cjs` e `mobile/scripts/test-filter-navigation.cjs`, além das suítes de contas, cartões, revisão, fechamento e estados. Foram aprovados 424 testes do backend, 43 suítes do cliente, TypeScript e lint. A matriz com dados atuais e hashes fica em `../audit_work/filter-consistency-2026-10-05/verification.json`. Componente real com transporte/nativo simulados não certifica layout ou instalação no aparelho.

### Integridade financeira na captura e revisão

As correções NF01–NF12 de 04/10/2026 acrescentam estes critérios ao fluxo compartilhado:

- Moeda da fonte e referência bancária permanecem preservadas. Tabelas sem moeda exigem confirmação explícita de BRL; moeda estrangeira não admite conversão implícita. Mesma descrição/data/valor indica coincidência para revisão, não identidade de transação; a confirmação de movimento distinto é individual e auditada.
- Conta efetiva de uma captura usa a atribuição explícita revisada. Reatribuir conta sincroniza as referências nova/legada na mesma transação e conserva a proveniência original. Filtros, comparação de duplicidades, saldos e fechamento seguem a mesma precedência.
- Competência governa o resumo econômico; data efetiva governa caixa. Vínculos de transferência são resolvidos independentemente do recorte. Ocorrências de compromisso usam vencimento próprio, separado da data de pagamento; a ocorrência final paga sai da projeção.
- Horário impresso sem fuso é interpretado em Brasília. Um `Z` produzido pela IA não comprova UTC: o adaptador exige indicação de fuso no documento. Instantes de transporte/persistência continuam UTC; a apresentação e o calendário financeiro usam Brasília. O mês que o Início envia ao resumo é esse dia civil, não o fuso do aparelho. Editar outra classificação não reenvia o horário intocado nem altera correções anteriores.
- Recuperação registra classe/código técnico do erro, sem mensagens/tracebacks que possam copiar `consideredValue` ou dados financeiros. Preserva comando, tentativas, backoff e identidade.

Regressões: `backend/tests/test_domain_*_fixes.py`, `test_domain_account_time_privacy.py`, `mobile/scripts/test-domain-transitions.cjs` e `test-capture-batches.cjs`. As evidências desta implementação ficam em `../audit_work/fixes-NF01-NF12-2026-10-04/`. Testes sintéticos não certificam câmera, extração externa ou comportamento físico Android.

| Área | Regra e razão | Código de referência |
| --- | --- | --- |
| Janela do seletor | Um `ChoiceOverlayProvider` estável na árvore existente da Activity; `ControlledChoice` compartilha esse host. Não reintroduzir Dialog/`Modal` nativo para opções. | `mobile/components/ChoiceOverlay.tsx`, `ControlledChoice.tsx` |
| Altura | Painel com altura definida (`85%` atualmente), independente de filtros, linhas renderizadas e resultados vazios. Lista com `flex: 1` e `minHeight: 0`. Apenas `maxHeight` não define o espaço disponível. Mudança de proporção exige nova verificação; 85% não é uma constante universal de design. | `ChoiceOverlay.tsx`, estilos `sheet` e `list` |
| Teclado | Dispensar teclado do campo anterior antes de abrir. Busca sem `autoFocus`; usuário toca para digitar. Não adicionar compensações de teclado concorrentes nem temporizadores para ocultar oscilação. | `ChoiceOverlay.tsx`, `open` e busca |
| Foco web | Foco inicial em Fechar, fundo inerte, Tab restrito ao diálogo e Escape para fechar. Restaurar foco sem rolar, somente com destino conectado e documento visível. | `ChoiceOverlay.tsx` |
| Dono e eventos tardios | Fechar/desmontar afeta apenas o dono atual. Revalidar callback, opções e bloqueio ao selecionar. Dois toques não podem consumir uma opção duas vezes. Background fecha o painel e impede nova abertura. | `ControlledChoice.tsx`, `ChoiceOverlay.tsx` |
| Carregamento e gravação | Desabilitar e fechar seletores quando a ação exige bloqueio; callback já enfileirado também deve ser rejeitado. Com tarefas simultâneas, liberar apenas quando todas terminarem. | Consumidores de `ControlledChoice`; `FinancialLab.tsx`, `pendingTasks` |
| Ações em processamento | Controles de interação compartilham uma trava síncrona. Um segundo toque, edição ou Voltar durante a ação é ignorado, sem execução posterior. Handlers e wrappers precisam **retornar a Promise**, inclusive callbacks de confirmação; `void save()` encerra o bloqueio cedo demais. Consultas automáticas continuam com proteção de geração, sem bloquear a interface por conta própria. | `InteractionControls.tsx`, `UserOperationBoundary.tsx`, `utils/userOperation.ts` |
| Confirmação da gravação | Mostrar sucesso somente após o ACK exigido pelo fluxo. Recargas de agregados podem ocorrer em segundo plano; consultas locais necessárias para o próximo token continuam protegidas. Uma falha posterior de leitura deve informar que a gravação foi salva. Todos os `Alert` do app usam o host da Activity por padrão: o blur tardio do diálogo nativo pode cancelar a escrita ou sua leitura posterior. O fundo fica bloqueado, botões ignoram toques durante processamento, e blur externo/background/lock invalida a confirmação sem repetir callbacks. Biometria e seletores de arquivo continuam usando os recursos do sistema com a proteção de sessão própria. | `utils/appAlert.ts`, `AppDialogHost.tsx`, `AuthProvider.tsx` |
| Identidade do editor | Categorias e outros campos controlados continuam sendo seletores antes/depois da consulta, mesmo sem opções. Não alternar `TextInput` e seletor com base no retorno da rede. Preservar rascunho e identidade do campo. | `TransactionEditor.tsx`, `ProvisionalReview.tsx` |
| Resultados variáveis | Resumo, carregamento e evidência de comparação ficam depois dos editores, identificados por linha. Não inserir blocos variáveis acima do campo ativo ou entre editores anteriores a ele. | `StatementBatchReview.tsx` |
| Respostas fora de ordem | Aplicar resultados, erros e fim de carregamento somente se pertencerem à consulta vigente. Invalidar ao desmontar. Cancelamento sozinho não substitui a verificação de geração quando uma resposta já chegou. | `RecordReview.tsx`; proteção de geração dos demais fluxos |
| Datas e consultas | Separar texto em edição do período aplicado; atualização explícita usa período completo e informa qual está exibido. Em fluxos já automáticos, manter debounce e descarte de respostas antigas. Não transformar cada tecla em recarga. | `FinancialLab.tsx`; comparação automática de lotes |
| Rolagem e chaves | Identidade distinta por tela/lista (`movements`, `review`, `tab` atualmente); estável enquanto se digita na mesma tela. Não usar consulta, contagem, relógio ou valor editado como `key` do formulário. | `mobile/App.tsx` |
| Texto nativo | Texto deve estar em `Text`. Evitar espaços literais entre condições dentro de `View`; usar condições booleanas explícitas, evitando expressões que retornem strings vazias ou números. | `App.tsx`, `RecordReview.tsx`, `ProvisionalReview.tsx` |
| Privacidade e sessão | Preservar host, navegação e fila; distinguir blur de saída real. Não desligar privacidade, autenticação, cancelamento ou tolerância de 15 minutos como solução visual. | `mobile/components/AuthProvider.tsx`; histórico de foco |

## Como investigar uma recorrência

1. Identifique tela, campo, versão instalada e plataforma. Verifique se o artefato instalado contém a alteração; fonte local e APK distribuído podem divergir.
2. Registre abertura com teclado fechado e já aberto, quantidade de opções e busca com muitos, poucos e nenhum resultado. Observe também erro e atraso da rede.
3. Meça altura e posição do painel, posição do campo no viewport, rolagem e foco antes/depois da atualização. Não basta medir apenas a posição no documento: o navegador pode compensar deslocamentos com rolagem.
4. Separe mudança de tamanho, remontagem do componente, transferência de foco, estado da Activity e requisição obsoleta. Use eventos/contadores e geometria; não presuma que todo sintoma é `Maximum update depth`.
5. Faça uma reprodução com componentes reais e dados sintéticos. Corrija o mecanismo identificado no componente compartilhado ou consumidor responsável. Não substitua o seletor por um stub no teste destinado a comprovar seu comportamento.
6. Acrescente uma regressão que falhe com o comportamento anterior quando possível. Execute os testes relacionados e verifique o comportamento visual. Preserve dados financeiros e rascunhos; não limpe o banco, a fila ou o armazenamento do usuário para fazer o problema desaparecer.

## Validação antes de encerrar uma alteração relacionada

Execute a partir de `financial-app/mobile`, usando as dependências locais:

```powershell
node scripts/test-choice-focus.cjs
node scripts/test-ui-stability.cjs
node scripts/test-batch-review.cjs
node scripts/test-accessibility.cjs
node scripts/run-client-tests.cjs
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js App.tsx components app scripts/test-ui-stability.cjs scripts/test-batch-review.cjs scripts/test-accessibility.cjs
```

O runner já inclui os testes individuais; os quatro primeiros servem para diagnóstico rápido, sem exigir execução duplicada quando a suíte completa já passou. Inclua no lint outros arquivos que a alteração tocar. Não reduza as asserções para fazer uma mudança passar.

| Teste | O que protege | O que não comprova |
| --- | --- | --- |
| `test-choice-focus.cjs` | Componentes reais de autenticação, seletor, overlay, lote e sessão; eventos de foco simulados, ciclos de abrir/fechar, background, callbacks e edições. | Layout, teclado e animações reais do Android. |
| `test-ui-stability.cjs` | Opções tardias sem troca de controle, bloqueio e callback tardio, consultas fora de ordem, período aplicado explicitamente, tarefas concorrentes. | Geometria renderizada no aparelho. |
| `test-batch-review.cjs` | Comparação e rascunho, resultados depois dos editores, preservação do nó do campo, regras de confirmação. | Posição efetiva na tela com teclado nativo. |
| `test-accessibility.cjs` | Rótulos, papéis e texto literal fora de contêineres de texto. | Experiência completa com leitor de tela. |
| `test-user-operation.cjs` e `test-interaction-lock.cjs` | Trava antes do primeiro await, rejeição sem replay, liberação em falhas, resultado/confirmação sem deadlock, edição/Voltar/teclado bloqueados e seleção real preservada. | Latência da rede do usuário, gestos/teclado no aparelho ou Voltar externo do navegador. |
| `test-category-transitions.cjs` | Confirmação dentro da Activity, renomeação/exclusão com substituição, interrupção sem replay, rascunho, renovação do token após 409, leitura obsoleta e recuperação de leitura cancelada. | Foco, renderização e teclado reais do Android. |
| `test-dialog-transitions.cjs` e `test-state-transitions.cjs` | Host compartilhado, callbacks descartados, fila suspensa sem backoff, recibo após ACK, CPF e senha salvos apesar de falha de recarga, conflito de cartão com comparação, busca de vendedor interrompida, atualização/exclusão após desmontagem e liberação após ACK de cronograma. | Sensor biométrico, transporte e arquivos reais, instalação e teclado Android. |
| `test-auth-transitions.cjs` | Invalidação externa, retorno biométrico tardio, foco separado de Activity ativa, transporte privado suspenso, conexão e conta protegidas contra respostas obsoletas. | Sensor e sequência física de eventos Android. |
| `test-domain-transitions.cjs` | Confirmações com dono, conflito com comparação, captura de voz durável, remoção explícita de campos opcionais e preservação após reabrir. | Microfone, compartilhamento e armazenamento nativos reais. |
| `test-overlay-transitions.cjs` | Identidade por abertura, Promise de seleção, descarte durante trava, invalidação no commit e transporte rejeitado antes do início. | Geometria, teclado e animação Android. |
| `test-app-transitions.cjs` | ACK local separado da contagem e sincronização, callback antigo de captura, suspensão após ACK, permissão tardia, descarte e retry com identidade completa. | Picker, câmera e commit nativo físicos. |
| `test-queue-adversarial-transitions.cjs` e `test-queue-sqlite-identity.cjs` | Ciclos interrompidos, comando pendente, arquivos imutáveis, identidade completa e limpeza concorrente; SQL de produção executado em SQLite real em memória. | Executor Expo SQLite no aparelho e IndexedDB físico do usuário. |

## Registro de 2026-10-04 — auditoria adversarial ampliada

Esta auditoria posterior à revisão transversal abaixo corrigiu 52 pontos de falha catalogados: nove de sessão/conta, doze de fila/comandos, dezesseis de telas e quinze de infraestrutura/integração. Vários compartilham o mesmo mecanismo; a contagem representa os cenários catalogados, não 52 mecanismos independentes. As fontes anteriores foram preservadas e os casos reproduzidos contra elas. Controles negativos verificaram também comportamentos que já estavam corretos.

A validação integrada aprovou 40 scripts do cliente, 349 testes do backend, TypeScript, lint e exportação web. A inspeção renderizada exercitou 32 casos em 320/390 px com UI real e serviços/nativos simulados, incluindo altura/foco do seletor, descarte durante processamento, recuperação de erro e rascunho preservado. A limpeza de campos opcionais foi verificada também no contrato backend e na propagação para linhas do lote. Os testes não consultaram nem modificaram o histórico financeiro real.

[Relatório por cenário, risco e critério de aceitação](../../audit_work/adversarial-transitions-2026-10-04/review.html), [verificação estruturada](../../audit_work/adversarial-transitions-2026-10-04/verification.json), [regressões do cliente](../../audit_work/adversarial-transitions-2026-10-04/client-final.log), [regressões do backend](../../audit_work/adversarial-transitions-2026-10-04/backend-after.log).

Nenhum aparelho ADB estava conectado. Biometria, teclado, microfone, câmera, compartilhamento, gestos e persistência Expo nativos permanecem sem validação física. Esta etapa alterou as fontes e não gerou um novo APK nem reiniciou o serviço em execução; o registro da versão 1.20.23 abaixo pertence à revisão anterior.

## Registro de 2026-10-04 — revisão transversal

A revisão encontrou nove falhas reproduzidas contra as fontes anteriores: confirmação nativa compartilhada; suspensão contabilizada como erro da fila; bloqueio/reversão de configuração de CPF por leitura antiga; diálogo de atualização após sair; cartão sem recuperação de conflito; busca de vendedor presa ao apagar o texto; callback de exclusão depois de sair; bloqueio após ACK de cronograma; bloqueio e ambiguidade após troca de senha confirmada. Os recibos já preservados após ACK permaneceram protegidos.

As correções também aplicam descarte por dono/desmontagem e separação de recargas secundárias em contas, compromissos, lotes, capturas provisórias, decisões e compra. O rascunho e as etapas locais de persistência continuam sendo preservados. A versão 1.20.23/código 69 passou em 34 scripts de regressão, TypeScript e lint. A inspeção web usou componentes reais com dados sintéticos em 320 e 390 px; não substitui execução física. [Relatório e limites](../../audit_work/state-transitions-2026-10-04/review.html), [evidências](../../audit_work/state-transitions-2026-10-04/verification.json).

Na verificação visual, cobrir ao menos larguras 320 e 390 px, filtros vazios e preenchidos, abertura repetida/Voltar, troca de tela após rolar e resposta atrasada enquanto se digita. Confirmar que o texto, o foco e a posição visível do editor permanecem estáveis. No web, verificar Tab, Shift+Tab, Escape e bloqueio do fundo.

No Android disponível, repetir no aparelho: teclado já aberto, abertura da categoria, busca por toque, nenhum resultado, seleção, Voltar, background/retorno, resposta atrasada e rascunho preservado. Registrar aparelho, versão do app e resultado. **Sem aparelho, declarar que teclado/animações nativas não foram validados.** Export web, APK assinado, testes verdes e confirmação de outro release não substituem essa etapa.

## Cobertura de telas

O contrato também se aplica a login, conta, privacidade e recebimento de imagem compartilhada. Para revisão transversal, percorrer Início; Movimentações/Extrato e detalhe/edição; Capturar; Revisão provisória e lotes; Mais; Contas; Cartões/faturas; Categorias/subcategorias; Compromissos; Ajustes; Decisões e evidências; Fechamento do mês; Cabe na minha vida. Ao acrescentar uma tela ou seletor, inclua-o na revisão e use os componentes compartilhados.

## Registro de 2026-10-03 — histórico, não garantia futura

A revisão alterou dez arquivos de produção. Foram aprovadas 20 suítes do cliente, TypeScript e lint; cinco casos novos de ciclo de vida foram cobertos, quatro reproduzidos como falhas contra as fontes anteriores. No navegador, 14 áreas foram inspecionadas com componentes reais e APIs, autenticação, armazenamento e recursos nativos simulados.

Na busca de categorias, altura do menu permaneceu em 663 px e posição relativa em 117,8 px nos três estados de busca medidos. Na comparação de lote em 390 px, o campo manteve posição visível de 483,425 px, rolagem de 659,2 px e foco antes/depois. Esses números pertencem àquela configuração de teste; futuras validações devem comparar estabilidade dentro da própria configuração, não exigir pixels absolutos idênticos.

Evidências: [relatório](../../audit_work/ui-stability-2026-10-03/review.html), [verificação estruturada](../../audit_work/ui-stability-2026-10-03/verification.json), [log de testes](../../audit_work/ui-stability-2026-10-03/client-tests.log). Não havia aparelho ADB conectado. Login/biometria e recebimento Android foram cobertos por inspeção e testes pertinentes, não por execução física. A revisão não gerou nem distribuiu um novo APK. Revalide o checkout e o artefato antes de transportar qualquer conclusão histórica para uma nova entrega.

## Captura por digitação — 2026-10-09

O preenchimento manual anterior existia apenas na memória até Salvar; fechar o processo podia perder uma compra ainda não enviada. A investigação não teve acesso ao armazenamento do celular e não comprova que essa foi a causa do caso relatado. O formulário agora guarda cada edição em rascunho durável, recupera após remontagem e permite excluir no formulário ou em Mais → Rascunhos. Usa a chave reservada `manual-capture` em `batch_drafts`; isso não representa lote financeiro nem comando de compromisso.

Antes de inserir na fila, persista o evento completo e seu UUID/instante. Uma falha posterior de inserção ou limpeza reutiliza esse comando após reinício; editar inicia um novo preenchimento e preserva a fila anterior. Limpar o rascunho aguarda autosaves, invalida gerações anteriores e confere conteúdo em exclusão pelo gerenciador. Falha de autosave preserva os campos e informa o problema; retentativa de Salvar tenta a persistência novamente.

Digitação não equivale a envio. Distinguir rascunho, salvamento na fila local, recebimento no servidor e confirmação em Revisão. Mostrar pendências/erro de sincronização e acesso à Revisão. Conta e pagamento opcionais passam ao evento e à projeção; transferência própria usa exclusivamente origem/destino. Valor deve ter centavos válidos, sem expoente, sinal negativo, agrupamento inválido ou valores abaixo de R$ 0,01.

Validação: `test-manual-capture.cjs` executa web e SQLite real em memória para retomada, identidade selada, exclusão condicionada e quota; `test-app-transitions.cjs`, `test-local-draft-deletion.cjs`, suítes de captura/estado/filtros e backend completam os contratos. Não houve acesso ao aparelho do usuário; teclado, animações e armazenamento nativos físicos precisam de confirmação no novo aplicativo.
