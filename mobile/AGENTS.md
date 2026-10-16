This is an Expo/React Native mobile application. Prioritize mobile-first patterns, performance, and cross-platform compatibility.

Captura por digitação mantém rascunho durável em `batch_drafts` com chave reservada `manual-capture`. Antes de enviar, persista o evento completo com UUID e instante; retentativa após falha de inserção/limpeza local reutiliza esse comando. Nunca anuncie digitação como movimentação salva: diferencie rascunho, fila local, recebimento pelo servidor e confirmação em Revisão. Execute `test-manual-capture.cjs`, `test-app-transitions.cjs` e `test-local-draft-deletion.cjs` ao alterar o fluxo.

Ao alterar categorias ou filtros, leia o contrato transversal em [UI-STABILITY.md](../docs/UI-STABILITY.md#categoria-ausente-nos-filtros-de-movimentações). Revise Início, Extrato, Detalhe, Revisão, Categorias, Experimentos e Fechamento, preservando categoria persistida, período econômico versus caixa, conta efetiva, paginação e identificação da consulta à qual pertencem linhas/totais. Execute `test-filter-consistency.cjs`, `test-filter-navigation.cjs` e as regressões de backend pertinentes.

## Regressão recorrente: menus, foco e estabilidade visual

Antes de alterar seletores, formulários, listas, rolagem, teclado, navegação ou ciclo de sessão, leia [UI-STABILITY.md](../docs/UI-STABILITY.md) e [SELECTOR-FOCUS.md](../docs/SELECTOR-FOCUS.md). O proprietário relatou mais de 20 tentativas de resolver o menu oscilando; em 2026-10-03 confirmou que a correção funcionou. Trate estes contratos como critérios de aceitação, inclusive em refatorações e mudanças de estilo.

- Preserve `ControlledChoice` + `ChoiceOverlayProvider` dentro da Activity. Não reintroduza `Modal` nativo para esses seletores.
- O painel precisa de altura definida independente da quantidade de opções (`height: '85%'` atualmente); a lista ocupa o espaço restante (`flex: 1`, `minHeight: 0`). `maxHeight` sozinho não substitui esse contrato. Não use medições da lista para realimentar a altura do painel.
- Ao abrir, dispense o teclado anterior; não adicione `autoFocus` na busca. Preserve foco, fechamento, dono da seleção, descarte de callbacks inválidos e bloqueio durante salvamento/carregamento.
- Não substitua seletores por inputs enquanto opções carregam; não remonte campos ao digitar, filtrar ou receber respostas. Respostas assíncronas não podem deslocar o editor ativo nem sobrescrever consultas recentes.
- Não tente corrigir a oscilação desabilitando privacidade, sessão ou cancelamento de requisições, adicionando temporizadores arbitrários ou mudando `key` a cada renderização.
- Execute os testes de foco com o seletor real, os de estabilidade e os de lote; verifique geometria e foco no navegador e, quando disponível, no aparelho. Testes React com mocks não medem layout nativo. Informe o limite quando não houver aparelho; não declare validação física a partir de APK, lint ou testes verdes.

O roteiro, os comandos, a matriz de telas e as evidências históricas estão em `../docs/UI-STABILITY.md`. Para esta manutenção, as rotas existentes ficam em `app/` e a composição principal em `App.tsx`; não mova a árvore para `src/app/` apenas por seguir um exemplo genérico de Expo Router.

## Regressão recorrente: estados, cancelamentos e confirmações

Rascunhos devem oferecer **Excluir rascunho** no formulário e, quando persistidos, em **Mais → Rascunhos**; consulte [o contrato de exclusão](../docs/UI-STABILITY.md#exclusão-explícita-de-rascunhos). Exclusão confirmada aguarda autosaves, invalida callbacks e não arquiva/recria as edições descartadas. Preserve fonte, comandos/ACKs e tentativas enviadas sem confirmação; não confunda `commitment-create` pendente com rascunho descartável. Verifique as condições de identidade/conteúdo em SQLite e web, falha de armazenamento e retomada após desmontagem.

Antes de alterar diálogos, ações assíncronas, fila ou recargas após salvar, leia o [aprendizado recorrente em UI-STABILITY.md](../docs/UI-STABILITY.md#aprendizado-recorrente-estados-e-transições). Em 2026-10-04, a falha de categorias revelou problemas semelhantes em outras telas; a correção deve preservar o contrato compartilhado e revisar todos os consumidores afetados.

- Use `utils/appAlert.ts` e `AppDialogHost` para confirmações, opções e avisos comuns dentro da Activity. Não reintroduza import direto de `Alert` nativo ou `{ inActivity: false }` em produção sem necessidade concreta documentada e reprodução de foco. Blur nativo pode chegar **depois** que o callback iniciou a escrita; esperar foco antes dele não resolve essa corrida. Biometria e seletores do sistema mantêm seu fluxo nativo protegido.
- Adquira a trava síncrona antes do primeiro `await`; retorne a Promise dos handlers. Descarte interação durante processamento, inclusive botões de diálogo e Voltar, sem replay.
- Aguarde ACK e persistência local obrigatória, além de leituras exigidas para o próximo token. Recargas secundárias não prolongam a gravação; falha posterior deve informar que já foi salvo. Não repita escrita para recuperar leitura.
- Suspensão da fila por `AbortError` não aumenta tentativas/backoff/quarentena nem dispara reinício imediato. Preserve comando, identidade, arquivo e recibo confirmado.
- Valide dono/geração em resultado, erro, `finally` e callback; invalide ao desmontar. Preserve persistência durável, rascunhos e identidade dos campos; não entregue resultados a outra tela. Invalidação de busca também libera seu spinner. Em 409, faça comparação de origem/rascunho/versão remota e obtenha aceitação explícita das alterações sobrepostas.
- Invalide dono/desmontagem no commit (`useLayoutEffect`) e confira validade antes de iniciar transporte ou janela do sistema. Cada abertura do seletor tem identidade própria; callbacks de uma abertura anterior não fecham nem selecionam na próxima. Retorne a Promise da seleção até o handler original.
- Ao remover um campo opcional, envie remoção explícita e confira o contrato backend; sugestões não viram novamente valor confirmado ao reabrir. Mantenha `natureza_sugerida` separada da natureza persistida/rascunho: salvar observações ou validar a revisão não aceita a inferência. Ao retomar rascunho legado sem natureza na fonte, preserve a versão original e apresente o valor antigo como não confirmado; não o envie sem nova seleção explícita. Preserve notas/outras edições e o marcador de intenção das escolhas futuras; metadados de rascunho ficam fora do payload. Monte patches com os campos alterados, preservando os demais valores e datas. A fila confere a identidade completa na tentativa, falha e descarte. Preserve comandos `409` retryable e recibos, mas permita corrigir rejeições definitivas.
- Após blur Android, `active` sozinho não significa foco da janela; preserve cobertura e suspensão até `focus`. Invalidação externa de `authState` também invalida biometria pendente e limpa a identidade da conta.
- Reproduza com componentes reais blur tardio, suspensão, ACK seguido de falha de recarga, resposta obsoleta, desmontagem, conflito e interação bloqueada. Os scripts de categorias, diálogos, estados e trava constam da validação canônica e do runner. Não enfraqueça privacidade/cancelamento, use timers arbitrários ou remonte a árvore como correção. Documente os limites de execução física.

## Biometria e prints Android

O proprietário pediu entrada direta com digital em 2026-10-03. Preserve a abertura automática do pedido nativo quando a biometria estiver habilitada, uma vez por ciclo de bloqueio; cancelamento não deve iniciar um loop. A leitura protegida de `SecureStore` já solicita a biometria: não adicione um segundo prompt antes dela. Preserve validação no servidor, expiração, senha alternativa e tolerância de 15 minutos. Consulte [ACCOUNT-SECURITY.md](../docs/ACCOUNT-SECURITY.md) e execute `scripts/test-biometric-entry.cjs` ao alterar esse fluxo.

Prints e gravações nativas Android são permitidos por pedido explícito do proprietário. Não introduza prevenção de captura ou `FLAG_SECURE`; preserve a liberação no início e retorno ao app e a cobertura opaca independente em segundo plano. Uma janela biométrica pertence ao sistema e não certifica comportamento de captura das telas do app. Testes sintéticos não comprovam o sensor nem o print no aparelho.

## Expo has changed — do not trust your training data

Expo ships breaking changes every SDK release. APIs you remember are likely renamed, moved, or removed. Before writing any code that touches an Expo, EAS, or React Native API:

1. Read the major version of the `expo` package in `package.json`.
2. Fetch the matching versioned docs: `https://docs.expo.dev/versions/v<major>.0.0/`
3. For anything else, fetch https://docs.expo.dev/llms.txt — an index of all Expo docs with corrections to common LLM misconceptions. Follow its links to the specific page you need; never answer from memory.

## Commands

Use `bunx` instead of `npx` if the project uses bun (`bun.lock` present).

```bash
npx expo install <package>  # ALWAYS use instead of npm/yarn/pnpm/bun add — resolves SDK-compatible versions
npx expo start              # start the dev server
npx expo lint               # lint
npx tsc --noEmit            # typecheck
npx expo-doctor             # diagnose dependency and config issues
npx expo install --fix      # fix incompatible package versions
```

Run lint and typecheck before declaring any task done.

## Navigation & Routing

- Use **Expo Router** for all navigation. Routes live in `src/app/` — every file there is a screen, `_layout.tsx` files define navigators. Keep non-route code (components, hooks, utils) outside `src/app/`.
- Import `Link`, `router`, and `useLocalSearchParams` from `expo-router`.
- Docs: https://docs.expo.dev/router/introduction.md

## Building with EAS

O release privado deste workspace também usa `scripts/build-android.ps1`. No Windows, o caminho longo do checkout pode fazer Ninja falhar com `Filename longer than 260 characters`; em 2026-10-03 esse erro reapareceu. A cópia física curta `C:\ft` é usada para builds locais. Antes de reutilizá-la, preserve diferenças existentes, sincronize as fontes pretendidas e verifique versões das dependências. Passe `-ProjectDirectory C:\ft -OutputDirectory <artifacts do checkout>` ao script. Confira a assinatura, a versão e as fontes do mapa do bundle contra o checkout original antes de anunciar o APK. Não apresente uma cópia antiga como a correção atual.

Use EAS to build, sign, and submit the app in the cloud (`eas build`, `eas submit`) and to ship over-the-air updates (`eas update`) — no local Xcode or Android Studio required. Run EAS CLI as `bunx eas-cli <command>` in Bun projects, or `npx eas-cli@latest <command>` otherwise; substitute that for bare `eas` in docs examples.
Docs: https://docs.expo.dev/eas/index.md

## Rules

- If `ios/` and `android/` directories do not exist, they are generated (Continuous Native Generation). Never create or edit them by hand — configure native behavior in `app.json` and config plugins.
- Expo Go only includes its bundled native modules. After adding a library with native code, the app needs a development build: `npx expo run:ios|android` locally, or `eas build --profile development`.
- Prefer recommended Expo modules over third-party libraries, and check your available skills before adding dependencies. Docs: https://docs.expo.dev/versions/latest/index.md
