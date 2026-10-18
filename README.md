# Financial Digital Twin — MVP

Mobile client and FastAPI backend for a private, offline-first personal finance MVP. This application is separate from the workbook migration tool and does not rebuild or migrate the existing finance data.

## Antes de alterar a interface

Leia as [instruções para agentes](mobile/AGENTS.md) e o [contrato de estabilidade da interface](docs/UI-STABILITY.md). Eles registram a regressão recorrente dos menus oscilando, os padrões que devem ser preservados em todas as telas e como verificar foco, teclado, rolagem e respostas assíncronas. O [histórico de foco dos seletores](docs/SELECTOR-FOCUS.md) distingue o problema anterior de Dialog Android da correção posterior de geometria do painel.

## What is implemented

- Views over MongoDB v1.2 `transactions`, `accounts`, `credit_card_bills`, and `source_records`. Explicit statement deletion archives the complete selected transaction and its auxiliary descriptor atomically before removing the active record.
- Dashboard totals use the v1.2 `classification.migration_reason` flags; invoice payments, credit-card movements, and own-account transfers are excluded from economic spending/income totals.
- Dashboard periods include confirmed captures newer than the imported ledger. The response exposes `legacy_data_updated_until`, `confirmed_capture_updated_until`, and separate freshness flags so an old statement cannot hide recent confirmed expenses.
- “Sem categoria” usa o mesmo agrupamento no Início, Extrato e Revisão: campo ausente, nulo, vazio ou rótulo histórico explícito. Correção de consulta no servidor em 05/10/2026: as nove capturas de outubro somam R$ 281,03 no agregado e aparecem no filtro; os registros financeiros foram preservados. [Conferência e testes](audit_work/uncategorized-verification.json).
- New captures write only to additive collections: `raw_events`, `transaction_evidence`, `provisional_transactions`, `review_queue`, and `audit_log`. Indexes are created idempotently when the backend connects.
- Matching compares amount, event time, merchant, payment method hint, and transaction type. High-confidence evidence attaches automatically; medium matches go to review; unlinked captures remain provisional.
- Idempotent retry handling for notification and manual event IDs. CSV/XLSX imports derive stable row IDs from the source file hash and row number.
- Receipt extraction for JPEG/PNG, text PDFs, and scanned PDFs. xAI returns structured output; Pydantic and deterministic BRL/item-sum checks gate processing.
- CSV and XLSX bank statement import with explicit Brazilian amount/date parsing. Ambiguous rows are reported instead of guessed.
- React Native Android screens for dashboard, transaction list/detail, capture, review inbox, accounts, cards, categories, and settings.
- **Mais → Fechamento do mês** oferece um checklist persistente por mês, com conferências por conta/cartão, saldos de abertura e encerramento, diferenças, observações, justificativas e pendências próprias. Novos dados pedem revisão dos itens afetados; conclusão e reabertura preservam o histórico. Veja [Checklist mensal](docs/MONTH-CLOSE.md).
- **Mais → Cartões** permite escolher o cartão antes de ver suas faturas, editar cartão e fatura e excluir/restaurar pela própria tela. As correções sincronizam a obrigação vinculada e preservam compras e pagamentos; exclusões não eliminam dívidas. Veja [Cartões e faturas](docs/CARDS.md).
- Listas e a revisão reutilizam os formatadores de moeda e data. Capturas, movimentações e compromissos carregam o contexto histórico e os vínculos uma vez por resposta. Veja [Conexão e recuperação local](docs/CONNECTION-STABILITY.md).
- **Mais → Categorias** permite abrir as subcategorias de cada categoria para criar, renomear e excluir. Renomeações atualizam os registros vinculados; exclusões em uso exigem uma substituta da mesma categoria. O catálogo servido é a árvore de dois níveis da NT-ARCH-MONGO-002 §07; um grupo de nível 1 não aceita lançamento direto. Opções personalizadas e nomes sem destino único permanecem, exclusões não ressuscitam e os nomes editados valem para as sugestões futuras. Valores, contas e descrições originais são preservados, com auditoria e gravações atômicas. Veja [Subcategorias](docs/SUBCATEGORIES.md).
- **Transferências próprias** têm origem e destino entre Itaú, Santander, Inter, BTG Pactual, Mercado Pago e Binance. Uma captura representa o movimento entre as duas contas, fica fora de despesas e receitas e pode ser conciliada com as duas pernas do extrato quando cada correspondência for única e identificar a outra instituição.
- **Mais → Contas** mostra as seis contas e permite informar um saldo de referência atual ou datado. O saldo calculado soma lançamentos posteriores por conta e mostra capturas sem conta e transferências pendentes. Binance usa um equivalente em BRL informado pelo usuário; não há cotação automática.
- **Mais → Compromissos** manages subscriptions and installment purchases in a separate `commitments` collection. It stores purchase and next-charge dates, amount, frequency, payment method, linked account/card, renewal state, remaining charges and optional end date. The dashboard shows charges expected in the next 30 days; imported installment plans support future-only corrections and exclusions through additive overrides, preserving original sources and paid history.
- The monthly commitment projection expands each selected month into individual charges with due date, installment number, payment method, platform and category when those details exist. The shared display name for an imported installment can be edited from Compromissos or follows a seller name manually reviewed on a transaction explicitly linked to that plan; the imported descriptor remains preserved.
- Edits to `fluxo` are checked against the signed amount before saving, including exact-descriptor batch updates. Confirmed captures require a type and a coherent flow.
- A user can explicitly link a real outgoing transaction to the next scheduled charge. The link records expected and actual amounts, advances the schedule, and is shown on the transaction detail. A forecast itself never creates a `transaction` or changes cash-flow totals.
- Native Android notification listener for selected Brazilian bank app package names. Capture runs only after the user enables notification access in Android Settings.
- Manual events and selected files use SQLite on Android/iOS and durable browser storage with Web Locks on web. Acknowledgements remove only the matching immutable event ID. Permanent failures remain available for review; transient failures retry per item with jitter.
- The owner account requires authenticated sessions. On first setup, configure `ACCOUNT_SETUP_TOKEN` and `JWT_SECRET` (at least 32 characters); `APP_PASSWORD` is only a migration credential for an existing installation. An empty migration password never disables authentication.

## Existing MongoDB compatibility

### Compras por plataformas de entrega

O dicionário v1.2 separa **Vendedor** (`merchant_id`), quem vendeu o bem ou serviço, de **Plataforma** (`platform_id`), o aplicativo ou intermediador, como iFood, 99Food ou Rappi. A categoria e a subcategoria descrevem o que foi comprado, não o aplicativo. Se o vendedor não estiver identificado, o campo pode ficar vazio. O descritor original (`merchant_raw` ou `estabelecimento_original`) permanece preservado, e a cidade da sede da plataforma não é usada como localidade da compra. Os nomes antigos `fornecedor` da API continuam aceitos para compatibilidade com versões anteriores do aplicativo.

The v1.2 financial collections are not rebuilt or migrated at startup. Account balance references live in the additive `account_balance_snapshots` collection; transfer details live with captured provisionals.

Uploaded source files are stored by SHA-256 under `MEDIA_STORAGE_PATH` (default `financial-app/data/media`). The server writes a temporary file, checks it, and atomically promotes it; `media_assets` records pending, ready, missing, orphaned, and purged states. A periodic scan flags broken references. An orphan file with no reference is removed from disk after 7 days and the record becomes `purged`. This path must be an absolute, durable shared volume before running multiple API instances. Protect and back it up; MongoDB transactions cannot make a filesystem write atomic.

## Backend setup

Use Python 3.12 or later.

```powershell
cd financial-app
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
Copy-Item .env.example .env
```

Set `MONGODB_URI` and `MONGODB_DATABASE` for the existing MongoDB instance. Decision writes and balance/dashboard reads require a transaction-capable MongoDB replica set. The app creates indexes on workflow collections and additive search/page indexes on `transactions`. It does not replace collections or source descriptions.

Exact-descriptor batch suggestions use `transaction_descriptor_index`, a separate equality index that leaves the validated ledger untouched. Build it after importing or adding ledger rows:

```powershell
.\.venv\Scripts\python.exe backend\migrations\backfill_descriptor_keys.py
.\.venv\Scripts\python.exe backend\migrations\backfill_descriptor_keys.py --apply
```

The script defaults to a dry run. It is idempotent; the API refuses batch suggestions if the indexed row count differs from the ledger count. Free-text transaction and capture searches use MongoDB text indexes; they match terms rather than arbitrary substrings. `/v1/movements?cursor_mode=true` and `/v1/transactions?cursor=...` use keyset pages. Old offset pages remain for compatibility and stop after offset 10,000.

`raw_events` are first recorded as pending, then their projections and final status commit together. A periodic worker retries pending events. Review decisions, commitment edits and links, and classification batches use MongoDB transactions. Classification batches also create durable commands in `batch_commands`; unresolved commands appear at `GET /v1/batch-commands`.

For a private development server, start from `financial-app`:

```powershell
.\.venv\Scripts\python.exe backend\run_dev.py
```

The development configuration binds the API to `127.0.0.1:8000` and publishes it through private Tailscale Serve HTTPS at `https://gbp360.tail1093e7.ts.net:8443`. The existing HTTPS site on port 443 is preserved. Personal-data routes require the owner's authenticated session, including when `APP_PASSWORD` is empty. Production requires `ENVIRONMENT=production`, a durable owner account, and a random `JWT_SECRET`.

No computador atual, a tarefa do Windows **Financial Twin Backend** inicia `scripts/Start-FinancialBackend.ps1` ao entrar na conta. O script reutiliza o servidor quando a porta 8000 já está ocupada e grava os logs de inicialização em `data/backend-startup`. Se o processo terminar, a tarefa tenta reiniciá-lo até três vezes, com intervalo de um minuto. O computador precisa estar ligado e conectado ao Tailscale para o celular acessar a API.

## Acesso no aplicativo

No navegador, a sessão autenticada dura 12 horas e o token fica só na memória. No aplicativo, a sessão de dispositivo e a biometria duram 30 dias e se renovam a cada desbloqueio conferido em `/v1/auth/account`, sem um segundo pedido de digital. Cancelar o sensor, trocar a digital ou receber revogação ainda pede senha. O preenchimento automático não apaga os campos nem reinicia a biometria. Um compartilhamento recebido com o app bloqueado fica guardado e abre depois da digital ou da senha. A fila nativa de notificações guarda no máximo 500 eventos. O contrato está em [ACCOUNT-SECURITY.md](docs/ACCOUNT-SECURITY.md).

## Cabe na minha vida?

**Mais → Cabe na minha vida?** avalia uma compra a partir de um print ou preço confirmado: horas de trabalho, caixa por pelo menos número de parcelas + 1 mês (mínimo de 90 dias) e eventual adiamento de uma meta. Lista de compromissos e cálculo usam o mesmo período, atualizado ao alterar parcelas ou primeira cobrança. Cada compromisso ativo do período entra com nome, data e valor, inclusive o pago no cartão e as compras parceladas; a fatura consolidada fica de fora. No topo há **Selecionar Tudo** e **Limpar seleção**; em cada compromisso há **Selecionar todo o Compromisso** e **Limpar seleção**. A renda líquida mensal, quando preenchida, entra uma vez por mês civil no menor saldo. Se o período já cobre as parcelas, o texto não diz que ainda há R$ 0,00. A análise abre com um selo que cruza caixa, reserva e meta, mostra a parcela mensal, os dias úteis de 8 horas e a renda-hora, e compara o saldo final com e sem a compra. Comprar agora, esperar 30 dias e a alternativa 20% mais barata trazem um destaque prático. A prévia gera um cartão PNG com preço, parcelas, horas, dias úteis e adiamento; salário, saldo, dívida, produto/loja e nome da meta ficam fora do cartão.

O Android recebe prints pela opção de compartilhar imagem de outros apps. A leitura pela xAI depende de um toque explícito e todos os campos permanecem editáveis. Nenhuma análise cria lançamento financeiro. Contratos, premissas, testes e recuperação: [PURCHASE-CHECK.md](docs/PURCHASE-CHECK.md).

Release privado atual: **1.20.70 / versionCode 116**, [APK ARM64](mobile/artifacts/financial-twin-1.20.70.apk). SHA256: `94D3BB0F984BA651D3C689EF7571D3CF18CF9361E2B042FFCBFC2439B0B42841`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.69 / versionCode 115**, [APK ARM64](mobile/artifacts/financial-twin-1.20.69.apk). SHA256: `4AC9D24CD14C8AC3189290226EDD55CBE8AB99C8D32A906C28EB8F0225B2D790`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.68 / versionCode 114**, [APK ARM64](mobile/artifacts/financial-twin-1.20.68.apk). SHA256: `7CF20E65021FC28CE5B4E2A055C7F2AC4C38B7FF51A338F894510C4EC8EDE38F`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.67 / versionCode 113**, [APK ARM64](mobile/artifacts/financial-twin-1.20.67.apk). SHA256: `9C0F69848E950EF1D96DF13A0CABCAB62839F6A507BAF760846564628E040DA5`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.66 / versionCode 112**, [APK ARM64](mobile/artifacts/financial-twin-1.20.66.apk). SHA256: `B1254751610714B7F3B801D38A820803E17E09BC65C58C79C67B6CD0CB6F1D6F`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.65 / versionCode 111**, [APK ARM64](mobile/artifacts/financial-twin-1.20.65.apk). SHA256: `6EBA45E048CEA22084FF28EC54A5F807418CCBE7318997A2D7B39D769FC738C7`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.64 / versionCode 110**, [APK ARM64](mobile/artifacts/financial-twin-1.20.64.apk). SHA256: `BC8B3B7A3DBD74E9F7D511D2AC2FC59F76246C371FF4EA0133B9E00B9E67F5C0`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.63 / versionCode 109**, [APK ARM64](mobile/artifacts/financial-twin-1.20.63.apk). SHA256: `604AE6BE0A631C0038A754AB697D4B49BB4B6F0DF2872D7FB18246E2FEAE3581`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.62 / versionCode 108**, [APK ARM64](mobile/artifacts/financial-twin-1.20.62.apk). SHA256: `46F2B34D4D422786D1D112DE4440BDAC54AB1AB2D4F1EE3EBF44DCBAB2D190AC`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.61 / versionCode 107**, [APK ARM64](mobile/artifacts/financial-twin-1.20.61.apk). SHA256: `F321AA3191326A15BE85DCD5B357E86B84FD3AC03143111D9746171E6B05632B`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.60 / versionCode 106**, [APK ARM64](mobile/artifacts/financial-twin-1.20.60.apk). SHA256: `3847A53BAD6F81B438F47542C7090B46D9E61ED767D10FF65D845E563E2E13F2`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.59 / versionCode 105**, [APK ARM64](mobile/artifacts/financial-twin-1.20.59.apk). SHA256: `ADF9166EA8C69B6967ECAC775CBE4F02F056019A447C96AB0DEEB9C8CE61F9AD`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.58 / versionCode 104**, [APK ARM64](mobile/artifacts/financial-twin-1.20.58.apk). SHA256: `D73ECF2192E76EF33C1CF6975C2A163E7DFBA9B6D02B5C0C5ADD8B4C82846FA6`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.57 / versionCode 103**, [APK ARM64](mobile/artifacts/financial-twin-1.20.57.apk). SHA256: `B7BB5FFC1A4FB2EA41C7F49D98F2E61BC8998F5D792931350EB7FE1D01BDC1FF`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.56 / versionCode 102**, [APK ARM64](mobile/artifacts/financial-twin-1.20.56.apk). SHA256: `83C025588B12ECE62ECB2585E3ED67DC2CC9F667C3F8D6454BA77B77FCA447AB`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.55 / versionCode 101**, [APK ARM64](mobile/artifacts/financial-twin-1.20.55.apk). SHA256: `48D1C59AA091953B5AD8DFB18773ABE09E682600C78017442CFDAF53D14E0E7C`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.54 / versionCode 100**, [APK ARM64](mobile/artifacts/financial-twin-1.20.54.apk). SHA256: `8D7C3AC7ED63AC199B2F0C115C88D4E4091AD5E7B630350D5C737F2DCD64C825`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.53 / versionCode 99**, [APK ARM64](mobile/artifacts/financial-twin-1.20.53.apk). SHA256: `CB124A86FC097EB505957D5D852B90DE1CF32DE2F490268A5DB335FADC0CA93B`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.52 / versionCode 98**, [APK ARM64](mobile/artifacts/financial-twin-1.20.52.apk). SHA256: `5E651721A9722B76E51BBE9E0C9589C0B35C71BF388E5FE8C291FB4F9D9806C4`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.51 / versionCode 97**, [APK ARM64](mobile/artifacts/financial-twin-1.20.51.apk). SHA256: `56B635170BB94B1C964CFABEF6A4B0AE0AE4394555063CFE24E05175363140CB`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.50 / versionCode 96**, [APK ARM64](mobile/artifacts/financial-twin-1.20.50.apk). SHA256: `50859950C215C3A9AF41D3F3B3DBBEDF28B47A38EBE1E37675305EF7FBCB339E`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.49 / versionCode 95**, [APK ARM64](mobile/artifacts/financial-twin-1.20.49.apk). SHA256: `5530FB722A5BBE2EE4832AEC2EFB6882CE520E8CB6DF74F8D53849C4B0ED17CA`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.48 / versionCode 94**, [APK ARM64](mobile/artifacts/financial-twin-1.20.48.apk). SHA256: `9FA8A0F4D21CD23974D02BB5EEED8425C70D36101B7AC9B8CCDF9473D7FC81F8`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.47 / versionCode 93**, [APK ARM64](mobile/artifacts/financial-twin-1.20.47.apk). SHA256: `0C577D3F65EA3F0978928BA309DFAE2ECBEF929D9E552DA948F677E649C02D87`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.46 / versionCode 92**, [APK ARM64](mobile/artifacts/financial-twin-1.20.46.apk). SHA256: `0643BF7884643F52A8BCA3849A5E0DC49602E7E1370E92A59E1313ACCB59FA7D`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.45 / versionCode 91**, [APK ARM64](mobile/artifacts/financial-twin-1.20.45.apk). SHA256: `C1F8D85081D571956B3944FFA5C8CC5B7242108DC4B5B83DBAA3C7894E842B4C`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.44 / versionCode 90**, [APK ARM64](mobile/artifacts/financial-twin-1.20.44.apk). SHA256: `4525D7FBCBEA1872387C588A7F0E9BA822B4F1990DE22B67C5061227D75EC70F`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.43 / versionCode 89**, [APK ARM64](mobile/artifacts/financial-twin-1.20.43.apk). SHA256: `83BCB7007A7F9A5E81217369E395B9A4CE8371FD5B354D325E5877E747E4C9B0`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.42 / versionCode 88**, [APK ARM64](mobile/artifacts/financial-twin-1.20.42.apk). SHA256: `0E02A0D1FE047A9BA998E372D62D2B7EC81A89D8B52A697F640C06519BC5C419`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.41 / versionCode 87**, [APK ARM64](mobile/artifacts/financial-twin-1.20.41.apk). SHA256: `CEA748339F4E3C5BCE243C55880A23F6E47942012D239C2BA1DBD83DC96DD516`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.40 / versionCode 86**, [APK ARM64](mobile/artifacts/financial-twin-1.20.40.apk). SHA256: `13FBC8A2A2DD70708B88EEC9BFF2623DB8238182DB8ABEB0E6E85008702A764E`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.39 / versionCode 85**, [APK ARM64](mobile/artifacts/financial-twin-1.20.39.apk). SHA256: `8A98BC3B63F8E794330A87BDBF4E81B1CFA2450E7DC1C8DC3F24DC2DF59BC5C4`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.38 / versionCode 84**, [APK ARM64](mobile/artifacts/financial-twin-1.20.38.apk). SHA256: `8781335DC16A5BFEF83D0AE3390C955356BC809B2684A7D0C145D4342DB81920`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.37 / versionCode 83**, [APK ARM64](mobile/artifacts/financial-twin-1.20.37.apk). SHA256: `4014C24459FAE2E188F1FA7FC1E21D1C81BEEEEB9F132E49B716660416C931D0`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.36 / versionCode 82**, [APK ARM64](mobile/artifacts/financial-twin-1.20.36.apk). SHA256: `527AF0D0B8092261978263F47C5152D3A50C1837C296C856A52BBFFEB906D93D`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.35 / versionCode 81**, [APK ARM64](mobile/artifacts/financial-twin-1.20.35.apk). SHA256: `F65C96B7F8147452AE7050ACA39B0DF2E58A7713AC5B0E3CA3B87F6F726A05C7`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.34 / versionCode 80**, [APK ARM64](mobile/artifacts/financial-twin-1.20.34.apk). SHA256: `7A88AE506EE396DAB8F91A0BBC12BEE3C3EA2C09A88DC2F5C82D59AA747FB3D3`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.33 / versionCode 79**, [APK ARM64](mobile/artifacts/financial-twin-1.20.33.apk). SHA256: `490892E5A87207A306D42E4F70660CFB7B340C13BC629E6B22F697966F4D78E8`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.

Histórico preservado (registro anterior): **1.20.26 / versionCode 72**, [APK ARM64](mobile/artifacts/financial-twin-1.20.26.apk). Corrige categorias e filtros semelhantes em Início, Extrato, Revisão, detalhes, categorias/subcategorias, experimentos, fechamento, contas e cartões: competência e gastos do detalhamento concordam com o agregado, todas as categorias são exibidas, inferência não substitui classificação persistida, busca inclui vendedor/plataforma e resultados antigos ficam ocultos ao trocar filtros. O catálogo inclui capturas ativas e preserva descartadas sem ressuscitar opções excluídas. Passaram 424 testes do backend, 43 suítes do cliente, TypeScript e lint; 64 conferências da matriz real e o catálogo foram verificados sem gravações financeiras. Servidor reiniciado e saudável; assinatura compatível, fontes embarcadas e download integral pelo atualizador local conferidos. Sem aparelho ADB, instalação e comportamento físico Android não foram verificados. [Verificação e limites](audit_work/filter-consistency-2026-10-05/verification.json).

Histórico preservado: **1.20.24 / versionCode 70**, [APK ARM64](mobile/artifacts/financial-twin-1.20.24.apk). Reduz a fonte dos valores dos cartões do Início de 24 para 21; o financiamento exibido no print cabe inteiro em uma linha na prévia com componentes reais em 320, 390 e 398 px. TypeScript, lint e o teste de valores responsivos passaram. Assinatura compatível, fontes embarcadas e download integral pelo atualizador local foram conferidos. Sem aparelho ADB, instalação e comportamento físico Android não foram verificados. [Verificação da entrega](../audit_work/dashboard-font-2026-10-04/verification.json).

Histórico preservado: **1.20.23 / versionCode 69**, [APK ARM64](mobile/artifacts/financial-twin-1.20.23.apk). A auditoria transversal reproduziu e corrigiu nove falhas de estado/foco: diálogos compartilhados dentro da Activity, suspensão da fila sem backoff, recuperação de conflitos de cartões/faturas com rascunho preservado, busca de vendedor interrompida, callbacks depois de sair e separação entre ACK e recargas secundárias. Contas, compromissos, lotes, revisão provisória, decisões, compra e ajustes compartilham essas proteções. Passaram 34 suítes do cliente, 72 testes de backend, TypeScript e lint. As 77 fontes embarcadas conferem com o checkout, incluindo 27 críticas; assinatura v2 e download integral do atualizador local foram verificados. Inspeção web em 320/390 px usa dados sintéticos. Sem aparelho ADB, instalação e comportamento físico Android não foram comprovados; acesso remoto por Tailscale não foi revalidado nesta auditoria. Nenhuma gravação financeira real foi executada. [Relatório e cobertura](../audit_work/state-transitions-2026-10-04/review.html) · [Verificação](../audit_work/state-transitions-2026-10-04/verification.json).

Histórico preservado: **1.20.22 / versionCode 68**, [APK ARM64](mobile/artifacts/financial-twin-1.20.22.apk). Corrigiu confirmações de categorias/subcategorias dentro da Activity, evitando blur tardio do Alert nativo durante renomeação/exclusão. Foram aprovadas 32 suítes do cliente e 12 testes de subcategorias, além de TypeScript/lint, assinatura e download local. [Evidência daquela entrega](../audit_work/category-transitions-2026-10-04/verification.json).

Entrega anterior registrada: **1.20.16 / versionCode 62**, [APK ARM64](mobile/artifacts/financial-twin-1.20.16.apk). A entrada solicita automaticamente a digital/biometria quando habilitada, mostra a marca da digital e mantém senha alternativa sem repetir o prompt após cancelamento. Prints Android são liberados explicitamente na abertura e no retorno; a cobertura de segundo plano e a tolerância de 15 minutos permanecem. As correções transversais de estabilidade também estão neste pacote. Foram aprovadas 21 suítes do cliente, TypeScript e lint; seis fontes e o bundle extraído conferem com o compilado e a assinatura é compatível com 1.20.15. [Verificação da entrega](../audit_work/biometric-entry-2026-10-03/verification.json) e [contrato de acesso](docs/ACCOUNT-SECURITY.md). Não havia aparelho ADB conectado para confirmar sensor e print físico.

Entrega anterior: **1.20.15 / versionCode 61**, [APK ARM64](mobile/artifacts/financial-twin-1.20.15.apk), compilado e assinado com o mesmo certificado da versão 1.20.14. Prints e gravação da tela estão liberados por pedido do proprietário. A cobertura ao minimizar, a tolerância de 15 minutos e a biometria permanecem; no iOS, o módulo mantém apenas a proteção da prévia nos aplicativos recentes. A [verificação desta entrega](../audit_work/screenshots-2026-10-02/verification.json) registra cinco regressões do cliente, TypeScript, lint, fontes do bundle, assinatura e download integral local de `/v1/app-update`. Não houve alteração do backend ou gravação financeira. A [correção dos seletores da versão 1.20.14](docs/SELECTOR-FOCUS.md), com sua [verificação](../audit_work/selector-focus-2026-10-02/verification.json), permanece. Remoção individual de lotes e comparação automática de duplicidades da [versão 1.20.13](../audit_work/batch-review-2026-10-02/verification.json), sessão e envio nativo de PDF da [versão 1.20.12](../audit_work/resume-pdf-2026-10-02/verification.json) permanecem. A [correção do cadastro/exclusão após confirmação e da data do saldo](../audit_work/sem-parar-account-2026-10-02/RELATORIO.md), as [correções C01–C23](../audit_work/fixes-C01-C23-2026-10-02/RELATORIO.md) e as [verificações B01–B12 da versão 1.20.4](../audit_work/fixes-B01-B12-2026-10-01/RELATORIO.md) permanecem documentadas. Não havia aparelho ADB conectado; o print no aparelho do proprietário e a transferência pela rede do celular não foram verificados nesta tarefa.

## xAI

Set `XAI_API_KEY` only in the server `.env`. Text and image understanding use separate adapters under `backend/app/services/ai/xai`; image input uses the configured vision model (configured as `grok-4.3` for the available account models) and structured outputs. Requests disable response storage. The mobile package contains no xAI key. Image generation is a separate unimplemented capability and is not used by this MVP.

The adapter follows the official [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs) and [image understanding](https://docs.x.ai/developers/model-capabilities/images/understanding) interfaces. On 2026-09-23, the authenticated model list did not include `grok-4-fast`, so text and vision use the available `grok-4.3` model; a synthetic receipt extraction returned valid structured BRL output. The account model list included `grok-imagine-image`; generation remains an unused, unimplemented capability. See the [xAI model retirement guide](https://docs.x.ai/developers/migration/may-15-retirement) and [Grok 4.3 capabilities](https://docs.x.ai/developers/models/grok-4.3).

## Android setup

Use Java from Android Studio and Android SDK platform/build-tools 36. The build script discovers their default Windows locations, or accepts `JAVA_HOME` and `ANDROID_HOME`.

From `financial-app/mobile`:

```powershell
pnpm install --frozen-lockfile
# Configure EXPO_PUBLIC_API_BASE_URL in the local .env if it is not already set.
node node_modules/expo/bin/cli prebuild --platform android --no-install
.\scripts\build-android.ps1
```

The script creates `mobile/artifacts/financial-twin-arm64.apk` with bundled JavaScript for the Galaxy S23. No phone or Metro server is required to generate the APK. This private development APK uses the existing development signing certificate; it is not a Play Store release. The build log is saved alongside the APK. An existing prepared build copy can be selected with `-ProjectDirectory C:\ft`.

`pnpm-workspace.yaml` sets `nodeLinker: hoisted` so native libraries use short paths on Windows. Keep this setting when reinstalling dependencies: nested `.pnpm` paths can cause CMake/Ninja regeneration failures. pnpm 11 reads these settings from the workspace YAML; `.npmrc` is not the place for this configuration. See the [pnpm 11 release notes](https://github.com/pnpm/pnpm.io/blob/main/blog/releases/11.0.md).

## Desktop launcher

Double-click **Financeiro - Desktop** on the Windows Desktop, or run `C:\Users\danie\FIN\Abrir-Financeiro-Desktop.ps1`. It starts the local API and serves the exported web app at `http://127.0.0.1:19006/`, then opens the default browser. The exported files are in `mobile/desktop-dist`; neither Node nor Metro needs to run when using the shortcut. The browser keeps the capture queue in local storage and holds the login token only for the current page session. Startup logs are in `data/desktop-launch`.

After changing mobile UI code, rebuild the desktop files from `financial-app/mobile` with `EXPO_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` and `expo export --platform web --output-dir desktop-dist`; the launcher serves the last export.

Version 1.16.2 names the review fields **Plataforma** and **Vendedor**. Confirmed provisional captures remain visible in Movimentações and can be edited there, including category and subcategory; the backend still accepts the old `fornecedor` API key for installed older clients.

The local mobile `.env` supplies the Tailscale HTTPS URL. No server API key belongs in the mobile environment. Once installed, open **Capturar → Configurar captura de notificações** and enable Financial Twin in Android's notification access settings. The native listener cannot run inside Expo Go.

## Import behavior

- `/v1/documents/extract`: JPEG/PNG, PDF, or plain text up to 20 MiB. PDFs support up to ten pages without silent truncation; scanned/mixed PDFs render every page and text PDFs use extracted text. Protected PDFs accept the user CPF through an optional transient multipart field after a 428 password challenge.
- `/v1/import`: CSV/XLSX up to 10 MiB. Required columns are recognizable date, amount, and merchant/description headers. Direction must be explicit from a signed amount, `fluxo`/`direction`/`natureza`/`tipo`, or card-payment method. Unclear rows are returned in `invalid_row_numbers`.
- Item totals differing from a receipt total by more than R$ 0,02 create a review item. AI output never writes directly to MongoDB.

## API

- `GET /health`
- `POST /v1/auth/session`
- `POST /v1/events`
- `POST /v1/documents/extract`
- `POST /v1/import`
- `GET /v1/dashboard`, `/v1/transactions`, `/v1/transactions/{id}`, `/v1/provisional`
- `GET /v1/reviews`, `POST /v1/reviews/{id}/decision`
- `GET /v1/accounts`, `/v1/cards`, `/v1/categories`, `/v1/obligations`, `/v1/installments`, `/v1/investments`
- `GET /v1/account-positions`, `PUT /v1/account-positions/{key}/balance-reference`, `POST /v1/transfers/reconcile`
- `GET/POST /v1/commitments`, `PUT /v1/commitments/{id}`, `POST /v1/commitments/{id}/payment`

Use `/docs` on the running backend for request schemas. Personal-data routes always require an authenticated Bearer session. `/health` contains only service status. See [the current adversarial audit](docs/AUDITORIA-ADVERSARIAL.md) for version tokens, review acknowledgement and pagination. The editing rule is unchanged: classification and review require the read token, a repeated command with the same id returns the prior result, and a changed payload with that id returns 409.

## Tests and checks

From `financial-app`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

From `financial-app/mobile`:

```powershell
.\node_modules\.bin\tsc.cmd --noEmit
.\node_modules\.bin\expo.cmd prebuild --platform android --no-install
```

## Current limitations

- Account balances require a user-confirmed reference amount and date; no opening balance is inferred from incomplete history. Captures without an assigned account and transfers awaiting both bank legs are flagged on the account screen. BTG Pactual, Mercado Pago wallet, and Binance currently have no linked legacy checking-account record.
- xAI extraction requires the server-side key; no secret key is included in the Android client.
- On 2026-09-28, the ARM64 Android 1.16.15 (versionCode 32) build completed from the short `C:\ft` build copy to avoid the Windows Ninja path limit. Only the Início dashboard rounds displayed BRL values to whole reais; other screens display cents, while stored amounts and calculations always retain cents. That APK is preserved as `mobile/artifacts/financial-twin-1.16.15.apk`.
- On 2026-09-29, the ARM64 Android 1.16.16 (versionCode 33) build completed with the queue quarantine, cursor pages, and consistency display. Its APK is `mobile/artifacts/financial-twin-arm64.apk` (also preserved as `financial-twin-1.16.16.apk`). APK v2 signature verification passed; installation and device-level validation await an ADB connection.
- Open Finance providers, automatic bank balances, crypto market valuation, remote sync background scheduling, category editing, AI chat/goals, and production HTTPS deployment are future work. Deterministic decision simulation is implemented in the NT-001 module.
- The review API confirms/keeps evidence separate or corrects provisional categories. Promotion remains in the new provisional ledger until a compatible, explicit account is selected; it never fabricates an `account_id` in v1.2 `transactions`.

## NT-001 reliability and decision features

Technical specification: [NT-001](docs/NT-001-CONFIABILIDADE-E-EVOLUCAO.md). Execution contracts and commands: [Runbook](docs/NT-001-RUNBOOK-EXECUTAVEL.md). Actual evidence and outstanding device checks: [Status](docs/IMPLEMENTATION-STATUS.md).

The app includes evidence-backed closure, purchase decision scenarios, charge/refund cases, account liquidity proposals, and savings experiments under **Mais → Decisões e evidências**. These workflows do not execute bank payments or send messages. Missing balances and incomplete statement coverage remain explicit.

Run `scripts/validate.ps1` for all backend/client tests, Expo compatibility, the dependency-security gate, typecheck and lint. Add `-Integration -TestMongoUri 'mongodb://127.0.0.1:27017/?replicaSet=financialTwinTest'` for all nine integration suites, including backup/restore and C regressions. The target guard rejects remote MongoDB before test writes. CI invokes the same client runner and integration dispatcher. Read the runbook before production recovery or Android builds.







