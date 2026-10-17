# Capturar — revisão de interface 1.19.1

A tela deixa de exibir dois gravadores de voz e dois fluxos completos na mesma página. O seletor inicial oferece **Digitar**, **Voz** e **Foto / arquivo**.

- Digitar mostra o formulário de compra/pagamento, receita ou transferência.
- Voz usa um único editor, com destino explícito: preencher uma movimentação ou guardar texto como evidência. Depois de preencher os campos, a transcrição fica recolhida, com **Editar transcrição**. Apagar o texto inteiro não remove o campo de edição.
- Foto / arquivo separa um comprovante de faturas/extratos com várias movimentações. Ambos têm botões próprios de imagem e PDF. Faturas/extratos também aceitam CSV/Excel na mesma revisão; os limites de PDF aparecem junto ao botão.
- Notificações ficam em um bloco secundário, com estado e acesso à configuração.

Datas digitadas usam DD/MM/AAAA; a interpretação por voz continua aceitando ISO. A validação rejeita datas inexistentes e preserva os campos após erro. Datas salvas mantêm o calendário civil de Brasília, com offset -03:00. Valores preenchidos pela voz aparecem com vírgula decimal.

Atualização de 04/10/2026: comprovantes com horário sem fuso são interpretados em `America/Sao_Paulo`. A extração inclui `timezone_evidence`, que só aceita um offset UTC/numérico quando ele é sustentado pela indicação impressa; um `Z` acrescentado pela IA sem essa indicação conserva o relógio impresso como Brasília. Instantes internos e `captured_at` continuam UTC para transporte e persistência. A edição manual de horário com offset explícito o preserva, e salvar apenas uma nota não reenvia data/valor intocados. As capturas antigas e suas correções manuais são conservadas.

Capturar carrega as contas ao abrir a aba, respeitando o cache de navegação. Transferências continuam exigindo origem e destino escolhidos pelo usuário. A conta de origem não aparece entre as opções de destino; trocar a origem para o destino atual limpa esse destino para nova escolha.

Um bloqueio por operação impede toques repetidos de enfileirar a mesma ação simultaneamente. O resultado do salvamento aparece junto ao formulário. Evidência por voz continua na fila de documentos, com `source_provider=mobile_voice`; preencher pela voz não salva uma movimentação. Salvar uma movimentação limpa seu áudio e transcrição para a próxima captura.

O áudio e texto de voz permanecem ao alternar os métodos **dentro de Capturar**. Trocar de método ou de destino fica bloqueado durante gravação e processamento. Sair da aba ainda desmonta o editor; não foi implementada persistência do rascunho de voz após sair da tela ou reiniciar o aplicativo. A fila de capturas já salvas permanece durável.

Validação reproduzível e capturas visuais: `../../audit_work/capture-ux-2026-09-30/verify-capture.cjs`. Todos os endpoints usados pelo teste de interface são interceptados por dados simulados, sem escrita financeira. A revisão abrange falha do provedor, preservação do texto, aplicação e edição, limpeza após salvar, evidência, tipos de arquivo, transferências e larguras de 320, 390 e 768 px. O resultado efetivo fica em `verification.json`; build e distribuição ficam no subdiretório `build` e no relatório de release.

A instalação física e o microfone Android dependem de verificação no telefone. O APK mantém as permissões e os módulos nativos da versão 1.19.0.

Na revisão da captura, um descritor já visto preenche os campos vazios com o perfil histórico do mesmo texto e do mesmo fluxo (saída ou entrada). A tela mostra "Pré-preenchido pelo histórico (...). Confira antes de confirmar." Vendedor, plataforma, categoria, subcategoria, natureza, tipo, meio de pagamento, localidade e conta movimentada entram só quando o histórico é compatível e o campo ainda está vazio. Transferência e captura já confirmada não recebem esse preenchimento. Sem histórico, Vendedor e Plataforma continuam vazios até a escolha.

Os chips usam o mesmo ranqueamento da digitação. Com o campo vazio, o texto capturado ordena os vendedores parecidos, e a plataforma sugerida ordena as plataformas; nomes distantes ficam de fora. O prefixo da maquininha (CAPPTA, IFD, Rappi e equivalentes) não conta contra o nome da loja, então um descritor truncado como CAPPTA LAN alcança o vendedor já usado nesse prefixo. Assim que há texto digitado, ele passa a valer no lugar do texto capturado. Tocar no chip do vendedor, ou sair do campo com um nome, preenche os demais campos vazios com o perfil daquele vendedor. Confirmar ou corrigir um descritor que identifica a loja além da plataforma grava o alias no cadastro, mesmo quando o vendedor já existe. Um texto só de plataforma, como IFD*IFOOD, não vira alias. A regressão está em `mobile/scripts/test-vendor-suggestions.cjs` e `backend/tests/test_platforms.py`.
