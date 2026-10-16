# Financial Twin na Oracle — preparação

Estado: kit local preparado; nenhuma VM criada e nenhuma implantação remota validada.

Mantém Python/FastAPI/Uvicorn, PyMongo/MongoDB remoto e o APK Expo/React Native.
Não executa migração nem recria a base financeira. O pacote contém apenas fontes
do backend, dependências e estes modelos; não inclui .env, mídia ou APK.

## 1. Criar a VM

Na região principal da conta, escolher uma imagem Ubuntu 24.04 ARM64 elegível
para Always Free e VM.Standard.A1.Flex. Começar com 2 OCPU, 4 GB de RAM e
50 GB de boot volume. Conferir no console a elegibilidade e os limites totais
da conta antes de criar. Gerar a chave SSH localmente e cadastrar só a chave
pública na Oracle. Habilitar acesso SSH somente do IP administrativo necessário;
a API continuará em 127.0.0.1 e será acessada por Tailscale Serve.

## 2. Gerar e transferir o pacote

No Windows, a partir de financial-app:

```powershell
.\.venv\Scripts\python.exe scripts\Build-OraclePackage.py --output deploy\packages\financial-twin-oracle.tar.gz
```

Transferir por SCP para a VM, conferir o SHA-256 informado pelo script e extrair
em /opt/financial-twin. Não incluir o arquivo de credenciais em pacotes de código.

## 3. Instalar na VM

Com o pacote já extraído, estes comandos preparam o runtime; ainda não iniciam
a API nem conectam ao banco:

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv ca-certificates curl
sudo useradd --system --home-dir /var/lib/financial-twin --shell /usr/sbin/nologin financial-twin
sudo install -d -o financial-twin -g financial-twin -m 700 /var/lib/financial-twin/media /var/lib/financial-twin/artifacts
sudo install -d -o root -g root -m 700 /etc/financial-twin
cd /opt/financial-twin
sudo python3.12 -m venv .venv
sudo .venv/bin/python -m pip install -r backend/requirements.txt -c backend/requirements.lock.txt
sudo .venv/bin/python -m pip check
sudo .venv/bin/python -c 'import fastapi, uvicorn, pymongo, pymupdf, PIL, openpyxl'
sudo install -o root -g root -m 600 deploy/oracle/fin.env.example /etc/financial-twin/fin.env
sudo install -o root -g root -m 644 deploy/oracle/financial-twin.service /etc/systemd/system/financial-twin.service
sudo systemctl daemon-reload
```

O arquivo lock fixa as versões existentes; requirements.txt também inclui Pillow,
necessário ao processamento de imagens. A instalação e os imports em ARM ainda
precisam ser verificados na VM. Os comandos de criação do usuário e cópia do
modelo são para a primeira instalação: não repetir sobre uma instalação existente.

## 4. Transferir configuração, mídia e acesso privado

Preencher /etc/financial-twin/fin.env de forma privada, preservando a URI, o nome
do banco, JWT_SECRET e as configurações atuais. Não enviar segredos pelo chat.
Conferir as regras de rede do MongoDB para o IP da VM sem liberar acesso geral.
Não criar outra conta de proprietário se a conta atual já existir no banco.

Copiar os comprovantes atuais para /var/lib/financial-twin/media, preservando
os nomes e subdiretórios, e comparar hashes e contagens. Manter o original local
e um backup independente. O backend atual grava caminhos absolutos Windows em
media_assets.path e em referências de mídia; somente copiar os arquivos não
resolve esses caminhos no Linux. Antes de ligar a API no banco financeiro,
inventariar as referências em modo somente leitura e preparar uma relocação
auditada e reversível, validada em banco sintético, preservando o caminho original.
Este kit ainda não executa essa relocação. Não iniciar a recuperação automática
sobre a base real até resolver e verificar todos esses caminhos.

Transferir o APK aprovado e o respectivo arquivo .manifest.json separadamente
para o caminho ANDROID_APK_PATH quando o atualizador for habilitado na nova API.
Conferir que digest, tamanho e versão do manifesto correspondem ao APK.

Instalar Tailscale pelo procedimento oficial, autenticar a VM na rede atual e
configurar Serve na porta 8443 para http://127.0.0.1:8000. O novo endereço precisa
ser confirmado pelo tailscale serve status; não reutilizar o hostname do PC.

## 5. Validar e mudar o app

Antes do primeiro start, definir uma janela de transição: o processo de recuperação
do backend escreve no MongoDB ao iniciar. Fazer primeiro uma validação com banco
descartável de teste; nunca apontar testes de escrita para o banco financeiro.
Para a troca definitiva, pausar o backend local após confirmar que não há envios
em andamento, completar a cópia da mídia e iniciar uma única instância na VM:

```bash
sudo systemctl enable --now financial-twin
curl --fail http://127.0.0.1:8000/live
curl --fail http://127.0.0.1:8000/health
sudo systemctl status financial-twin --no-pager
```

Verificar o estado do MongoDB no health, login, consultas, comprovantes antigos,
uma captura sintética em banco de teste e HTTPS pelo Tailscale. Então atualizar
EXPO_PUBLIC_API_BASE_URL no build Android, gerar um APK com a mesma assinatura
e confirmar no celular: login, sincronização, PDFs, biometria e atualização.
Não desinstalar o app nem limpar seus dados: a fila e os rascunhos devem permanecer.
Reiniciar a VM e confirmar a recuperação automática. O health local sozinho
não comprova acesso pelo Android.

Se houver necessidade de voltar ao PC, pausar primeiro a API da VM, copiar de
volta toda mídia recebida desde a troca e verificar os hashes antes de religar
o backend local. Reverter também a relocação das referências de mídia, incluindo
as novas referências criadas na VM. Manter o mesmo banco remoto e nunca manter dois gravadores com
pastas de mídia independentes durante a transição.

## Referências

- [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Criar instância](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm)
- [Tailscale em servidores](https://tailscale.com/docs/how-to/set-up-servers)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)

Disponibilidade regional e eventual retomada de instâncias ociosas seguem as
condições da Oracle. Sempre conferir o console; gratuidade da VM não inclui
eventuais cobranças atuais de MongoDB ou xAI.
