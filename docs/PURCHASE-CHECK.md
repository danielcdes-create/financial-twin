# Cabe na minha vida?

## Síntese da análise — 17/10/2026

**Sua análise** abre com um selo. Ele cruza o caixa, a reserva mínima e o efeito na meta: pressiona o caixa, cabe no caixa e trava ou adia a meta, ou cabe com a reserva protegida. Sem saldo inicial, o selo fica só no esforço e na meta.

A mesma tela traduz o esforço. Com mais de uma parcela, mostra o valor mensal. Com renda e horas preenchidas, mostra os dias úteis de 8 horas, as horas de cada parcela e a renda-hora considerada. O raio-X compara o saldo no fim do período com a compra e sem ela, e aponta o menor fechamento diário depois das entradas quando ele é mais alto que o menor saldo.

Comprar agora destaca a parcela mensal ou o toque na reserva. Esperar 30 dias destaca o fôlego antes da primeira cobrança. A alternativa 20% mais barata mostra a economia total, a economia mensal e as horas de trabalho poupadas.

O cartão compartilhável continua sem salário, saldo, dívida, produto, loja e nome da meta. Passa a incluir a parcela, os dias úteis de 8 horas e, quando a meta sai da janela de dez anos, essa frase, sem o nome da meta. Testes: `mobile/scripts/test-purchase-copy.cjs` e `mobile/scripts/test-purchase-card.cjs`. A análise não cria lançamento. A instalação no aparelho não foi verificada.

## Lista, renda e parcelas dentro do período — 17/10/2026

Em **Cabe na minha vida?**, cada compromisso ativo do período entra como linhas com nome, data e valor, inclusive o que é pago no cartão e as compras parceladas (`installment_plans` e `parcelamento`). A fatura consolidada (`fatura_cartao` ou `fatura`) não aparece nessa lista. O planejamento geral (`/v1/insights/planning-context`) continua sem repetir a compra do cartão quando a fatura é a saída de caixa, e continua oferecendo a fatura para conferência.

A lista tem **Selecionar Tudo** e **Limpar seleção** no topo para todos os compromissos listados, e em cada compromisso (incluindo **Financiamento**, assinaturas e compras parceladas) os botões **Selecionar todo o Compromisso** e **Limpar seleção**. Cada linha continua podendo ser marcada sozinha. Obrigações importadas que não são fatura de cartão permanecem desmarcadas até o toque.

Se a renda líquida mensal está preenchida, o menor saldo da compra soma essa renda uma vez em cada mês civil do período, junto com o saldo inicial, os compromissos marcados e as parcelas do cenário. Sem renda, o saldo usa só o saldo inicial, os compromissos e as parcelas. A renda não entra no simulador geral de 90 dias.

Quando o período já cobre todas as parcelas do cenário, o texto diz que o período já inclui todas as parcelas desta compra. “Ainda há … em parcelas” só aparece quando o valor posterior é maior que zero. A análise continua sem criar lançamento.

Testes sintéticos: `backend/tests/test_purchase_horizon.py`, `backend/tests/test_purchase_check.py` e `mobile/scripts/test-purchase-copy.cjs`. O ledger ao vivo não foi lido nem escrito. A instalação no aparelho não foi verificada.

## Correção da leitura de títulos longos — 30/09/2026

Um print real do Galaxy Ring reproduziu `503 invalid_output`: a xAI retornou preço total `1873.14`, 12 parcelas e parcela `156.15`, mas o título completo do anúncio ultrapassou os 120 caracteres aceitos por `product_label`. A resposta inteira era descartada por `string_too_long`, embora os valores financeiros fossem válidos.

O título, usado apenas para exibição, agora normaliza espaços e, se necessário, limita a 120 caracteres com reticências. O prompt solicita marca/modelo e variante em um nome curto. Os validadores de centavos, sinal, quantidade de parcelas, campos extras e ambiguidades permanecem exigidos. A extração continua somente em memória, com `needs_review=true` e `persisted=false`; essa correção não cria captura, evidência, obrigação ou lançamento.

Falhas de validação registram apenas nomes de campos conhecidos e categorias de erro. Valores, transcrições, nomes de campos extras recebidos e credenciais não são registrados. Cinco regressões novas cobrem o título original, limites financeiros, resposta HTTP e privacidade dos logs. A suíte total passou com 119 testes. Evidências: `../../audit_work/purchase-scan-2026-09-30/`, incluindo a resposta anterior e a verificação HTTP da imagem original. A alteração é de backend e funciona com o APK 1.19.1 existente.

Funcionalidade implementada em 29/09/2026. Entrada: **Mais → Cabe na minha vida?**. O Android também recebe um print pela opção de compartilhar imagem de outro aplicativo.

## Fluxo

1. Escolher um print PNG/JPEG ou preencher preço e parcelas manualmente.
2. Se desejado, tocar em **Ler print com xAI**. A imagem só é enviada nesse momento. Conferir e corrigir o preço total, número de parcelas e data do primeiro pagamento.
3. Informar premissas opcionais: saldo inicial, reserva, renda líquida, horas de trabalho e uma meta com aporte mensal.
4. Conferir os compromissos previstos. Cada compromisso ativo, inclusive o pago no cartão e as compras parceladas, aparece com nome, data e valor. A fatura consolidada do cartão fica de fora. Obrigações importadas que não são essa fatura começam desmarcadas. **Selecionar Tudo** e **Limpar seleção** controlam a lista inteira, e **Selecionar todo o Compromisso** e **Limpar seleção** controlam cada compromisso individualmente (inclusive **Financiamento**).
5. Avaliar o selo no topo, as horas de trabalho, a parcela mensal, o caixa no período de pelo menos número de parcelas + 1 mês (mínimo de 90 dias) e o eventual adiamento da meta. Comparar comprar agora, esperar 30 dias e uma alternativa 20% mais barata.
6. Abrir a prévia do cartão. No Android, compartilhar a imagem pela folha nativa; no desktop, salvar o PNG. O cartão leva preço, parcelas, horas, dias úteis e adiamento. Salário, saldo, dívida, produto, loja e nome da meta ficam de fora.

## Invariantes

- A análise não cria transação, evidência financeira, compromisso ou obrigação. Não executa compra ou transferência.
- Preços extraídos são sugestões revisáveis. A xAI recebe somente o print; o cálculo posterior usa valores estruturados confirmados pelo usuário.
- Toda aritmética monetária no backend usa `Decimal`; divisão em parcelas preserva o total e os centavos.
- Horas = preço × horas mensais / renda líquida mensal. Sem as duas premissas, não há resultado de horas.
- Sem saldo inicial explícito, o caixa é insuficiente para concluir capacidade de compra. Horas e meta podem continuar disponíveis.
- A meta assume que compra e aportes disputam o mesmo dinheiro, sem rendimentos. Aportes mensais seguem a âncora da data inicial; a busca termina em 120 meses. Parcelas do mesmo dia são descontadas antes do aporte.
- Datas usam o calendário civil de Brasília na interface. Lista e cálculo compartilham `purchase_horizon.py`: número de parcelas + 1 mês de calendário, contado da primeira cobrança, preservando o mínimo de 90 dias. O cenário de espera também tem pelo menos um mês após sua última parcela. Ex.: 12 parcelas com início e primeira cobrança em 30/09/2026 usam até 30/10/2027. A interface mostra as datas exatas.
- `GET /v1/insights/purchase-context?start=...&installments=...&first_payment=...` usa esse mesmo intervalo para compromissos e obrigações existentes. O compromisso pago no cartão entra na data de vencimento dele. A obrigação `fatura_cartao` ou `fatura` não entra nessa resposta. Alterar parcelas/data (inclusive por print ou voz) recarrega a lista; respostas antigas são descartadas. A análise aguarda o contexto vigente, com erro e nova tentativa disponíveis. Seleções por identidade são preservadas e obrigações novas, que não são fatura de cartão, continuam desmarcadas. A lista agrupa as ocorrências de cada compromisso, sem criar parcelas ou pagamentos.
- Com `monthly_net_income`, a avaliação da compra credita essa renda uma vez por mês civil do horizonte, no primeiro dia civil do mês que cai dentro do período. Sem esse campo, o caixa permanece o de antes. `/simulate` não recebe essa renda.
- Somente a compra admite horizonte até 3.660 dias, mantendo até 60 parcelas; `/simulate` continua limitado a 90 dias e `/planning-context` a 367 dias. A geração alcança também compromissos semanais antigos; o cálculo indexa eventos por dia para períodos longos.
- APK 1.19.2 / código 38 contém a tela dinâmica. Sete regressões adicionais cobrem calendário, 12/60 parcelas, adiamento, saldo ausente, limites e contexto somente leitura; com as duas regressões da inicialização Windows, suíte total de 128 testes. Evidências em `../../audit_work/purchase-horizon-2026-09-30/`.
- Na validação ao vivo, o Windows Proactor parou de aceitar conexões após `WinError 64`, embora o processo continuasse vivo. `run_dev.py` agora passa uma fábrica explícita `SelectorEventLoop` ao Uvicorn no Windows; outros sistemas mantêm `auto`. A fábrica retorna uma instância, conforme o contrato do Uvicorn instalado; não depende de políticas globais. O backend local não usa subprocessos assíncronos. A troca evita o caminho `AcceptEx` afetado ([CPython #93821](https://github.com/python/cpython/issues/93821)); há verificação com 100 resets de conexões de leitura e 20 consultas concorrentes de saúde, sem gravações financeiras.
- O cartão captura somente sua própria View. A função que prepara seu texto aceita preço, parcelas, horas, adiamento e o aviso de meta fora de dez anos. Salário, saldo, dívida, produto/loja e nome da meta não entram no cartão.
- Mudanças nas premissas invalidam a análise e sua prévia anterior.

## Contratos de API

As duas rotas usam a autenticação já existente do app. Consulte os tipos completos no OpenAPI do backend ativo.

### POST /v1/insights/purchase-scan

Multipart com campo `file`, PNG/JPEG de até 10 MiB. O backend verifica assinatura do arquivo e usa extração estruturada com `store: false`.

Resposta: `product_label`, `total_price`, `installment_count`, `installment_amount`, `ambiguity`, `needs_review: true`, `persisted: false`. Valores ausentes ou ambíguos permanecem nulos; não há multiplicação de parcelas para inventar um total ausente. Preços riscados ou condicionais exigem confirmação.

Erros: 413 para limite excedido, 415 para assinatura não aceita, 503 para extração indisponível. O usuário pode continuar pelo preenchimento manual.

### POST /v1/insights/purchase-check

JSON com `start`, `price` positivo, `installments` de 1 a 60 e `first_payment` igual ou posterior ao início. Opcionais: `opening_balance`, `reserve`, `flows`, `monthly_net_income`, `monthly_work_hours`, `goal_target`, `goal_saved`, `goal_monthly_contribution`.

Valores monetários são strings decimais de até duas casas. Fluxos têm data, valor com sinal, identificação e descrição; uma referência não pode aparecer duas vezes e fluxos anteriores ao saldo inicial são rejeitados. Meta e aporte devem ser informados juntos. Entradas inválidas retornam 422.

Resposta: preço e parcelas normalizados, `installment_amounts`, `work_hours`, `goal`, `cash_scenarios`, `gaps`, `assumptions`, `execution: "none"`. O caixa contém baseline e três alternativas. A data da meta refere-se à compra informada, sem representar promessa de realização.

## Aceite e testes

- Compra sintética de R$ 300 em três parcelas retorna três valores de R$ 100.
- Renda de R$ 3.000 e 160 horas retorna 16,0 horas.
- Meta de R$ 1.000 com aporte de R$ 500 tem adiamento calculado de 30 dias no cenário iniciado em 31/01/2026.
- Compra no dia inicial pode adiar uma meta já financiada; teste cobre essa ordem de eventos.
- Falta de saldo mantém a lacuna; valores com três casas e referências repetidas são rejeitados.
- Teste de privacidade injeta campos privados extras e verifica que não entram no cartão.
- A suíte principal incorpora `backend/tests/test_purchase_check.py` e `mobile/scripts/test-purchase-card.cjs`.

Validação real: um print sintético “FONE EXEMPLO / R$ 300,00 / 3 × R$ 100,00” foi reconhecido pela xAI, inclusive através da API local. Os testes não enviaram documento pessoal.

## Recuperação e limites

- Se a extração falhar, corrigir os campos manualmente e avaliar novamente.
- Se o arquivo compartilhado perder disponibilidade, escolher o print novamente na galeria.
- Premissas e meta ficam na sessão da tela; sair dela ou reiniciar o app exige novo preenchimento. O ledger não é alterado.
- Com contexto financeiro indisponível, a tela explicita que calcula apenas as premissas fornecidas.
- Build Android e configuração do intent são verificáveis localmente. Recebimento do print e compartilhamento do PNG ainda exigem homologação em aparelho físico.
- A análise, as alternativas e o cartão PNG gerado foram inspecionados no navegador com dados sintéticos, inclusive em 390 px. O navegador integrado iniciou o download e depois o cancelou com zero bytes recebidos; o salvamento concluído do arquivo permanece sem homologação nesse navegador. `public-card.png` conserva os bytes reais gerados pelo app e `purchase-mobile.png` registra a inspeção visual.

Evidências da entrega: `../../audit_work/purchase-check-2026-09-29/` e [estado da implementação](IMPLEMENTATION-STATUS.md).
