# Seleção de conta e cartão — 1.20.14

Este documento preserva a investigação e a entrega da versão 1.20.14. Para alterações atuais, leia também o [contrato de estabilidade da interface](UI-STABILITY.md), atualizado após a confirmação do usuário em 2026-10-03. A troca do Dialog por um overlay resolveu um mecanismo de perda de foco, mas não basta para impedir oscilação de altura: o painel também precisa de geometria independente do conteúdo virtualizado e busca sem foco automático. Não confunda as duas investigações nem trate a validação histórica abaixo como prova de uma nova versão.

Ao revisar um extrato, abrir o seletor de conta/cartão usava um `Modal` nativo. No React Native 0.86.3 instalado, ele cria um Dialog Android e transfere foco. O app recebia `AppState.blur` mesmo com a Activity ativa, ocultava a interface e suspendia a sessão e as leituras privadas. O usuário relatou erros repetidos e travamento nesse momento.

O teste executa os componentes reais `AuthProvider`, `ControlledChoice`, `StatementBatchReview` e o cliente de sessão, simulando os eventos nativos de foco. Com o seletor anterior, comprova uma abertura de Dialog, um blur, uma suspensão e uma requisição cancelada enquanto a Activity está ativa. Não reproduz fisicamente o loop completo: a inspeção do Fabric instalado não sustentou a hipótese de desmontagem/reabertura automática do Dialog por `display:none`. Essa distinção está registrada nas evidências.

`ChoiceOverlayProvider` oferece um único painel de opções dentro da janela existente da Activity. Ele preserva busca, indicação da opção atual, limpeza quando permitida, fechamento pelo botão Voltar e Escape no web. O componente conserva a versão atual do callback e rejeita uma opção que deixou de existir. Fechamento e desmontagem só afetam o dono da seleção. Dois toques antes da próxima renderização consomem a escolha uma única vez.

A cobertura de privacidade mantém a geometria da árvore privada: opacidade, bloqueio de toques e acessibilidade, com uma camada opaca por cima. O host das opções continua montado junto à navegação e à fila. Sair do app fecha a seleção, inclusive quando abertura e background ocorrem no mesmo turno de eventos. Tentar abrir já em background é ignorado. Voltar preserva edições e não reabre nem repete a comparação. O período de 15 minutos permanece.

## Componentes semelhantes

Todos os usos de `ControlledChoice` compartilham a correção. A revisão de código incluiu os consumidores abaixo; não equivale a uma sessão física completa em cada tela.

| Componente | Escolhas afetadas |
| --- | --- |
| `StatementBatchReview` | Conta/cartão do extrato e tipo de cada linha |
| `CapturePanel` | Tipo de movimentação, conta de origem e destino |
| `ProvisionalReview` | Conta, natureza, categoria, subcategoria e método de pagamento |
| `TransactionEditor` | Campos de classificação com opções controladas |
| `AccountPositions` | Tipo e status da conta |
| `FinancialLab` | Motivo do caso |

Na versão 1.20.14 descrita neste registro, `AppDialogHost` usava `Modal` somente no web, e confirmações nativas usavam o diálogo do sistema com espera de retomada. A revisão de 2026-10-04 mudou o padrão para confirmações dentro da Activity em todas as telas: blur tardio do diálogo do sistema também pode ocorrer depois de seu callback iniciar uma escrita. Veja o [contrato atual](UI-STABILITY.md) e o [relatório da versão 1.20.23](../../audit_work/state-transitions-2026-10-04/review.html).

## Validação

As regressões usam os componentes reais, sem substituir o seletor por um stub. Cobrem conta e cartão, doze ciclos de abrir/Voltar/trocar tipo, busca sem acento, callbacks executados uma vez, ausência de erro de profundidade de renderização, quantidade limitada de requisições, saída para background e preservação de edições. A revisão de lote mantém cobertura de prévia automática, evidência de duplicidade, seleção das novas, respostas antigas, remoção e limpeza somente após confirmação persistida.

Foram executados 19 scripts do cliente, TypeScript, lint e export web. O APK ARM64 1.20.14/código 60 tem assinatura compatível com 1.20.13; cinco fontes relevantes conferem com o mapa do bundle e o bundle extraído do APK confere com o compilado. O atualizador local serviu os 47.618.264 bytes com SHA256 `A903C47F6172EC6722651CB77E14043936850188A56ED0A6CB2F4B350D826838`. A tarefa só leu o banco real; contagens e hashes de dez coleções financeiras e de lotes permaneceram iguais, incluindo o rascunho pendente de 42 linhas.

Evidências: [verification.json](../../audit_work/selector-focus-2026-10-02/verification.json). Não havia aparelho ADB conectado ou emulador configurado. A validação nativa completa no aparelho do usuário e a transferência pela rede do celular continuam sem comprovação nesta tarefa.
