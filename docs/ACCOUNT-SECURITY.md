# Conta de acesso e privacidade

O Financial Twin mantém um histórico financeiro pessoal. Há uma única identidade proprietária na coleção `account`, com `_id=financial-twin-owner` e `kind=app_identity`. A coleção `accounts` continua reservada a bancos, cartões e financiamento. Não há cadastro público de outras pessoas nem alegação de isolamento entre vários usuários.

## Primeiro acesso

1. No servidor, execute `.\.venv\Scripts\python.exe backend\scripts\configure_account_security.py`. Ele configura `JWT_SECRET` e `ACCOUNT_SETUP_TOKEN` no `.env` privado, sem imprimir os valores. Reinicie o worker após configurar.
2. No computador, dê dois cliques em `Primeiro acesso.cmd` e escolha nome, usuário e senha de pelo menos 12 caracteres. A janela lê o código internamente e envia o cadastro somente à API local. Não registra a senha nem guarda a sessão e tenta revogar a sessão temporária após cadastrar. Se a revogação falhar por indisponibilidade da API, essa sessão expira em até 12 horas e pode ser encerrada em Minha conta.
3. Abra o aplicativo atualizado, toque em **Já configurei minha conta** e entre com o usuário e a senha escolhidos. Se preferir cadastrar pelo celular, use **ver código de primeiro acesso** na janela, ou execute `scripts\Show-AccountSetupCode.ps1`, e preencha o cadastro do app. Não compartilhe o código nem o `.env`. Não é necessário informar CPF, telefone ou e-mail.
4. Acesse **Mais → Minha conta e privacidade** para editar o perfil, trocar a senha, ativar a biometria e consultar/encerrar sessões.

O código deixa de autorizar cadastros após a criação da identidade: a chave única fixa impede substituir o proprietário, inclusive em cadastros concorrentes. Remova `ACCOUNT_SETUP_TOKEN` do ambiente após concluir o cadastro, se desejar. Um `APP_PASSWORD` previamente configurado pode comprovar o primeiro acesso e migrar para o usuário `proprietario`; depois que a identidade existe, essa senha do ambiente não oferece acesso alternativo.

Recuperação: o administrador local pode executar `backend\scripts\reset_account_password.py` com o Python do projeto. A senha é solicitada sem eco no terminal, e todas as sessões anteriores são revogadas. Não há reset público baseado em e-mail, CPF ou outros dados pessoais.

## Proteções

- Todas as rotas financeiras exigem sessão mesmo em desenvolvimento. Sem chave de assinatura válida, o acesso autenticado falha fechado.
- Senhas recebem PBKDF2-HMAC-SHA256, 600.000 iterações e sal aleatório individual. Nunca são persistidas como texto na coleção nem no aplicativo.
- Tokens assinados no navegador duram 12 horas; no aplicativo nativo, sessões de dispositivo e biometria duram 30 dias e são renovadas de forma deslizante a cada validação autenticada em `/v1/auth/account`, mantendo o mesmo identificador de sessão (`jti`). A sessão também precisa existir no banco. Revogação individual, saída global e troca/recuperação de senha invalidam sessões no servidor imediatamente. No máximo dez sessões ativas são mantidas atomicamente no documento.
- Proteção contra tentativas: dez tentativas por minuto por origem direta e um limite compartilhado da identidade pessoal, em memória por processo. Não se confia em `X-Forwarded-For`. O servidor atual usa um worker; uma implantação com múltiplos workers exige limitador compartilhado.
- Respostas `/v1` usam `Cache-Control: no-store`. Erros de validação de autenticação não devolvem os valores fornecidos, evitando eco de senhas e códigos.
- O cliente aceita HTTPS; HTTP é permitido apenas em loopback para desenvolvimento local. Tokens, senhas e códigos não são colocados em URLs. O JWT contém identificadores técnicos, sem nome, CPF ou dados financeiros.
- O navegador aberto em localhost usa a API local em `127.0.0.1:8000`, sem depender da tailnet. Para outra porta, configure `EXPO_PUBLIC_WEB_API_BASE_URL`. O app nativo mantém `EXPO_PUBLIC_API_BASE_URL` com HTTPS.
- No navegador, o token permanece só na memória. No nativo, só o opt-in de biometria permite salvar a sessão, com `SecureStore.requireAuthentication`, proteção deste aparelho e biometria forte. Alterações no cadastro biométrico invalidam a chave. Sem biometria, o token fica na memória.
- Não se coleta impressão digital. O sistema operacional valida digital/biometria. Cancelamento, indisponibilidade e expiração não desbloqueiam o app; a senha continua disponível.
- Desde 1.20.12, minimizar o app ou perder foco no navegador oculta o conteúdo e cancela requisições, mantendo a sessão aceita por 15 minutos fora do app. Retornar antes do prazo preserva a tela e os campos; ao completar o prazo, exige novo desbloqueio. O prazo é conferido antes de mostrar o conteúdo mesmo se os temporizadores tiverem sido congelados pelo sistema. Eventos repetidos de segundo plano não estendem o prazo. Bloqueio manual, saída, expiração e revogação da sessão continuam valendo imediatamente.
- No Android, uma perda de foco da janela sem sair da Activity (teclado, preenchimento de credenciais como Samsung Pass ou cortina de notificações) cobre o conteúdo privado e suspende a rede privada, preservando a sessão até o foco voltar. Na tela de login bloqueada, janelas de preenchimento automático (Samsung Pass / Autofill) não apagam os campos preenchidos nem reiniciam o ciclo biométrico. Um login aceito durante essa cobertura só é ativado ao retornar o foco, com espera limitada a 30 segundos. Sair para segundo plano com requisição de login em voo invalida o login pendente. A biometria só libera a sessão depois de consultar sua validade no servidor.
- Um diálogo de câmera, arquivos, permissões ou compartilhamento explicitamente aberto pelo app fica sob uma cobertura opaca, com rede suspensa. A sessão aceita usa a mesma tolerância de 15 minutos; terminar o seletor antes do evento de retorno ao app não descarta a tela de captura. O limite de dois minutos do marcador de diálogo não reduz a tolerância da sessão aceita.
- Desde 1.20.15 / código 61, capturas e gravações de tela são permitidas no aplicativo, conforme pedido do proprietário. Ao minimizar, o cliente continua ocultando o conteúdo; no iOS, `expo-screen-capture` mantém apenas a proteção do seletor de aplicativos. Backup automático Android continua desabilitado no plugin de configuração. A liberação no aplicativo instalado exige atualizar o APK.

## Entrada direta por digital e renovação sem segundo prompt

Quando a biometria já está ativada e disponível, abrir o aplicativo bloqueado apresenta a marca da digital e chama automaticamente o pedido nativo do Android/iOS. Não é necessário tocar primeiro em “Desbloquear com biometria”. O acesso exige leitura de `SecureStore` com `requireAuthentication: true`, sessão não expirada e validação de `/v1/auth/account` no servidor antes de liberar o conteúdo. O desenho do app é decorativo; a posição do sensor e a identificação da digital são controladas pelo sistema.

Para habilitar pela primeira vez, entre com a senha e acesse **Mais → Minha conta e privacidade → Ativar biometria neste aparelho**. Sem habilitação, hardware disponível ou durante o cadastro inicial, o app mantém a entrada por senha. A sessão biométrica vale por 30 dias e é renovada automaticamente a cada desbloqueio validado no servidor (`X-Session-Token` / `X-Session-Expires-In`). A renovação usa um envelope autenticado ancorado na chave protegida por hardware (`requireAuthentication: true`), evitando abrir um segundo `BiometricPrompt` do Android ao atualizar o token ou ao concluir login por senha/Samsung Pass.

O pedido automático é consumido antes de abrir a janela do sistema, uma vez por ciclo de bloqueio. Blur/focus da própria biometria não pode repeti-lo. Se a leitura biométrica do hardware já foi concluída e apenas a validação de rede foi interrompida por `blur` ou oscilação transitória, o app mantém a tela biométrica e reaproveita o token recém-desbloqueado naquele ciclo de bloqueio, sem exigir nova leitura física do sensor. Cancelamento do prompt, digital alterada, expiração ou revogação (`401`) mostram a alternativa por senha; naquele ciclo, novo pedido depende de um toque explícito. Um novo ciclo de bloqueio retorna à entrada automática, sem tornar a escolha anterior de senha permanente. Retorno em menos de 15 minutos mantém a sessão aceita e não solicita digital novamente. Não acrescentar uma chamada de `LocalAuthentication.authenticateAsync` antes da leitura protegida: ela já apresenta o pedido nativo e duas chamadas criariam duas autenticações.

No Android, `useScreenPrivacy` chama explicitamente `allowScreenCaptureAsync` ao montar e ao retornar ao estado ativo. As fontes do app não podem chamar prevenção de captura nem definir `FLAG_SECURE`. A cobertura opaca de segundo plano continua separada. O app permite o print nativo de suas telas; políticas externas do aparelho e a própria janela do sistema não são controladas pelo app.

Regressão: `mobile/scripts/test-biometric-entry.cjs` e `mobile/scripts/test-auth-transitions.cjs` executam o provider, a leitura protegida e o cliente HTTP reais com sistema/servidor sintéticos. Cobrem abertura automática, prompt único, cancelamento e nova tentativa explícita, retomada após interrupção de rede/blur sem segundo prompt no sensor, preservação de autofill (Samsung Pass), renovação por envelope autenticado, expiração, chave indisponível, revogação, ausência de opt-in, hardware indisponível, cadastro, background e liberação de captura ao retornar. Também impedem reintroduzir prevenção de captura/`FLAG_SECURE` nas fontes próprias. O roteiro de estabilidade permanece em [UI-STABILITY.md](UI-STABILITY.md).

## Limites e dados locais

Capturas, arquivos e rascunhos pendentes são preservados ao bloquear/sair e continuam no armazenamento privado do aplicativo. Esta entrega não migra a fila SQLite/IndexedDB nem os arquivos existentes para um cofre criptografado próprio. O bloqueio de tela do sistema, a criptografia do aparelho/disco e o controle de acesso ao servidor continuam relevantes. Não confundir login ou SecureStore dos tokens com criptografia integral do histórico.

Prints são permitidos no navegador e, desde 1.20.15, também no aplicativo nativo. Biometria, cancelamento do prompt, mudança das digitais, prévia ao minimizar, câmera/arquivos e comportamento de fabricante precisam de validação em aparelho físico. As mudanças no aplicativo instalado exigem atualizar o binário Android/iOS com os plugins de `app.json`.

Fotos, documentos e áudio enviados para análise continuam usando os provedores de IA já configurados no servidor. A autenticação não modifica essa política de processamento. Notificações previamente habilitadas podem continuar sendo capturadas pelo serviço Android, mas a sincronização do app fica bloqueada sem sessão disponível.

Requisições abortadas pelo cliente podem já ter sido recebidas pelo servidor. O bloqueio impede novos envios e publicação de respostas antigas; ele não desfaz gravações confirmadas.

## Validação

- `python -m unittest discover -s backend/tests -q`: autenticação fechada, assinatura/adulteração, expiração, sessão biométrica de 30 dias e renovação deslizante, versão/revogação, valores malformados, erros sem segredos e regressões do domínio.
- `backend/scripts/check_account_security.py`: banco descartável `financial_twin_test_account_*`, API real, cadastro protegido, sessão, revogação, senha, limite concorrente de sessões e preservação das contas bancárias de teste.
- `mobile/scripts/test-account-security.cjs`: estado bloqueado/suspenso, HTTPS, armazenamento protegido, biometria forte, envelope autenticado de renovação, cancelamento, expiração, ausência de persistência web e descarte de respostas tardias.
- `mobile/scripts/test-login-navigation.cjs`: usa o ciclo de vida real do provider SQLite nativo do SDK 57, com banco sintético, e verifica que um login aceito permanece ativo após 20 atualizações da navegação.
- `mobile/scripts/test-login-lifecycle.cjs`: executa o AuthProvider real com React e o cliente HTTP real, usando eventos nativos, relógio controlado e respostas sintéticas.
- TypeScript, ESLint, export web e revisão visual com identidade fictícia. Nenhuma credencial real é usada nos testes de interface.

Referências técnicas: [Expo LocalAuthentication SDK 57](https://docs.expo.dev/versions/v57.0.0/sdk/local-authentication/), [Expo SecureStore SDK 57](https://docs.expo.dev/versions/v57.0.0/sdk/securestore/), [Expo ScreenCapture SDK 57](https://docs.expo.dev/versions/v57.0.0/sdk/screen-capture/) e [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
