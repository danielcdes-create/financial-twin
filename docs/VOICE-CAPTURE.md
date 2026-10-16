# Captura por voz — 1.19.0

## Protocolo vigente de evidência — 1.20.33

Salvar uma declaração sela primeiro um comando durável: UUID da ocorrência, instante UTC com offset, texto revisado imutável, identidade do arquivo e estágio. A fila reutiliza esse comando e verifica os bytes antes de reutilizar o TXT. Duas declarações novas podem ter texto e hash iguais; seus UUIDs continuam diferentes. O hash identifica conteúdo e não substitui a identidade da captura.

O multipart `POST /v1/documents/extract` admite `captured_at` opcional. Para `mobile_voice` com esse campo, `event_id` é obrigatório e o ACK deve confirmar esse mesmo UUID em `processing.persisted_event_id`. O protocolo usa `voice_command_version: 1` na fila. Replays legados concluídos, sem esse marcador/instante, conservam contexto, identidade antiga e recibo original.

Persistência do comando precede arquivo/fila; enfileiramento precede limpeza condicionada do rascunho. Falha de limpeza mostra finalização pendente e retoma a mesma tentativa. ACK durável é consultado antes de recriar arquivo ou fila; falha após confirmação não repete transporte. Para outra declaração, **Editar nova captura** inicia explicitamente outro preenchimento e outro UUID. Autosave antigo não pode apagar um comando selado.

Nenhuma evidência confirma uma movimentação automaticamente. Áudio original, outros rascunhos, arquivos compartilhados, comandos e recibos permanecem preservados. Regressões executam SQLite real, arquivos com bytes diferentes, desmontagem, falhas entre persistências, ACK e protocolo multipart; a homologação física segue pendente sem aparelho. [Auditoria atual](AUDITORIA-ADVERSARIAL.md). As seções anteriores da 1.19.0 abaixo são histórico, não o protocolo vigente.

Atualização em **1.20.20 / código 66**: áudio e transcrição do editor atual ficam salvos no aparelho para retomar após trocar de tela ou reiniciar. O áudio escolhido/gravado recebe cópia durável; gravações nativas também usam o diretório de documentos. O rascunho anterior fica preservado ao substituir o áudio ou salvar/aplicar a evidência. Esses arquivos locais não são anexados automaticamente ao histórico financeiro. A evidência continua sendo o texto revisado, enfileirado com identidade estável.

Ao retomar, toque novamente em **Preencher campos com a voz**: o formulário destino pode ter sido descartado e o app não presume que os campos ainda estejam preenchidos. A transcrição fica editável e nenhuma movimentação é gravada automaticamente. Falha de persistência local conserva o editor e informa o erro; uma captura já salva continua confirmada se a limpeza posterior não terminar. Os trechos abaixo registram os contratos e limites históricos da 1.19.0; a ausência de retomada descrita naquela versão foi substituída nesta atualização.

Na **1.19.1**, Capturar passou a usar um único editor de voz, selecionado por método e finalidade. Veja [a revisão da tela de captura](CAPTURE-UX.md). O contrato de voz e a validação histórica abaixo pertencem à implementação 1.19.0.

Disponível em **Cabe na minha vida?**, **Capturar → Nova movimentação**, **Capturar → Adicionar evidência** e no formulário de **Compromissos**. Compras, entradas e transferências usam o mesmo formulário de movimentação.

1. Grave até dois minutos ou escolha um áudio de até 20 MiB.
2. Toque em **Transcrever com xAI**. O servidor envia o áudio a `POST https://api.x.ai/v1/stt`, com `model=grok-voice-transcribe-2.0`, `language=pt` e `format=true`. A chave fica somente no servidor. O campo `file` é o último no multipart.
3. Confira e edite o texto. **Preencher campos com a voz** extrai apenas um rascunho; os valores e datas permanecem editáveis. Nenhuma compra, compromisso ou movimentação é confirmada nesse passo.
4. Salve pelo formulário existente após conferir os dados. Transferências exigem contas de origem e destino escolhidas explicitamente, mesmo quando a fala menciona os bancos.

No modo de evidência, **Salvar texto como evidência** coloca a transcrição revisada em um arquivo TXT na fila durável existente. A fonte é `mobile_voice`. O áudio original não é anexado ao histórico; a evidência salva é o texto informado pelo usuário. Em movimentações, o texto usado para preencher o formulário permanece no `raw_payload.transcript` do evento como proveniência.

A gravação para ao sair da tela, colocar o app em segundo plano ou atingir dois minutos. Apenas um gravador do app pode ocupar o microfone. Gravar/escolher áudio funciona sem exigir uma sessão online; transcrever e preencher dependem de conexão. Em uma falha de rede/provedor, áudio e texto permanecem disponíveis **nesta tela** para tentar novamente. Ainda não há retomada de rascunho de voz após sair da tela ou reiniciar o app. No navegador o microfone exige HTTPS ou localhost; escolher um áudio oferece outra forma de captura.

## Backend

- `POST /v1/capture/transcribe`: multipart; detecta WAV, M4A/MP4, MP3, WebM, OGG ou FLAC pela assinatura e limita a 20 MiB. Retorna texto editável, modelo, `needs_review=true`, `persisted=false`.
- `POST /v1/capture/interpret`: JSON `{context: "purchase" | "movement" | "commitment", text}`. Retorna campos validados e os mesmos indicadores de revisão. Usa a data civil de Brasília para referências explícitas como “ontem”.
- Os dois endpoints respeitam a autenticação existente e não consultam nem gravam dados financeiros. Não armazenam o áudio ou transcrição. O logging de falhas contém somente status HTTP e o nome do contrato que falhou.
- `XAI_TRANSCRIPTION_MODEL` tem o padrão `grok-voice-transcribe-2.0`; `XAI_API_KEY`, URL e modelo de extração são os mesmos do backend existente.

## Leitura do print

O contrato de extração agora pede valores decimais como strings, transforma preços brasileiros como `R$ 3.256,90` em `3256.90` e aplica o schema estrito também a objetos aninhados. Valores ambíguos permanecem nulos. Uma leitura parcial não apaga preço/produto/parcelas já preenchidos. Erros de credencial, limite de uso, timeout e saída inválida mostram mensagens específicas; a tela permite repetir a leitura. Os uploads nativos incluem tanto bytes para o fetch do Expo SDK 57 quanto URI para o transporte alternativo do React Native.

A imagem recebida nesta tarefa é uma captura da tela de erro, com uma miniatura do anúncio. A chamada direta atual à xAI retornou normalmente, mas considerou o preço dessa miniatura ambíguo. A causa histórica da falha no aparelho não foi reproduzida e não deve ser atribuída com certeza à formatação monetária ou ao transporte.

## Validação

- 114 testes Python, incluindo contrato multipart, modelo 2.0, idioma, autenticação, limites de tamanho, assinatura de arquivo, saídas inválidas, schema e leitura de BRL.
- TypeScript, ESLint, regressões da fila web e do cartão compartilhável.
- `mobile/scripts/test-capture-upload.cjs` usa o serializador multipart da versão instalada do Expo e verifica os bytes de print, voz e evidência, além da URI para o transporte alternativo. Isso não substitui executar o app em um telefone.
- Chamadas reais à xAI com fala sintética em português: preço total de R$ 3.256,00 em dez parcelas; interpretação de transferência entre Itaú e Inter; compromisso mensal de R$ 49,90.
- Fluxo web real de escolha do áudio, transcrição e preenchimento na tela de compra, com largura de 390 px.
- APK ARM64 1.19.0 / versionCode 36 compilado, com `RECORD_AUDIO`, assinatura igual à versão 1.18.0 e URL privada do backend no bundle. O download HTTP local e o manifesto oferecido por `/v1/app-update` coincidem em tamanho e SHA-256. O acesso HTTPS pela tailnet não pôde ser confirmado neste host.

Evidências da execução: `../../audit_work/voice-capture-2026-09-30/`. Não havia dispositivo conectado ao ADB: gravação, permissões e instalação no telefone continuam sem validação física.
