# Plano de recuperação priorizado — Financial Twin Android

Data: 09/10/2026. Plano de recuperação implementado: 19 achados, SEC01, Git/backup local, backend e APK privado; **SEC02 foi separado para outra entrega**, mantendo a assinatura compatível. Resultados e limites de homologação: [recuperação](RECUPERACAO-ANDROID-2026-10-09.md). Evidência e inventário originais: [auditoria](AUDITORIA-ADVERSARIAL-ANDROID-2026-10-09.md).

O alvo é preservar a intenção de cada operação, fazer UI/backend/armazenamento concordarem e manter o histórico recuperável. As fases abaixo são ordenadas por impacto e dependência; não são estimativas de prazo nem autorização para corrigir dados ambíguos.

## R0 — Preservar e fixar a versão de referência

**Prioridade:** Pré-requisito. **Itens:** OP01.

Registrar as fontes atuais, configuração sem segredos, lockfiles, artefato e checksums em baseline recuperável. Antes de qualquer reparo de dados, obter backup consistente de banco e arquivos, identificar dispositivos/filas/rascunhos e registrar fingerprints. Usar bancos sintéticos para desenvolver as correções.

**Dependência:** Nenhuma alteração financeira antes desta etapa.

**Saída verificável:** Backup restaurado em destino vazio e isolado; referência de código/artefato verificável; nenhuma limpeza de fila ou reconstrução de movimentos.

## R1 — Impedir omissões e sobrescritas silenciosas

**Prioridade:** P1 + dependência P2. **Itens:** PS01, BE04, BE06, PS05, UI01.

Conservar evidência de notificações ambíguas; exigir identidade e tempo suficientes no matching automático; separar captura nova de replay. Persistir o comando de voz antes da fila e reutilizá-lo se limpeza falhar. Corrigir a identidade no backend junto com esse protocolo. Implementar comparação origem/local/remoto no cronograma e decisão explícita sobre campos sobrepostos.

**Dependência:** R0. PS05 precede ou integra BE06: remover deduplicação por texto isoladamente pode transformar o rascunho ressuscitado em uma segunda captura.

**Saída verificável:** Duas compras legítimas de mesmo valor/comerciante em dias distintos permanecem duas; duas evidências de voz novas permanecem duas; replay do mesmo comando produz uma. Falha após ACK não gera UUID novo. 409 nunca substitui valor/data/cancelamento remoto sem escolha explícita.

## R2 — Alinhar cadastro, obrigações e planejamento

**Prioridade:** P1/P2. **Itens:** BE01, BE02, BE03, BE05.

Unificar inventário de referências de conta com compromissos e regras de valor; serializar exclusão e criação de vínculo. Distinguir conta histórica mantida de nova escolha. Padronizar estados abertos de fatura nos leitores e escritores. Permitir edição compatível de compromisso concluído sem retroceder número da parcela ou projeção.

**Dependência:** R0; revisar mensagens e opções dos consumidores Android na mesma entrega.

**Saída verificável:** Prévia e exclusão concordam; zero órfãos em testes de corrida; cancelar compromisso mantendo a conta histórica funciona; fatura aberta continua elegível para seleção do planejamento; nota em parcela concluída salva sem reabrir dívida.

## R3 — Recuperar interações e tornar o estado visível

**Prioridade:** P2. **Itens:** UI02, UI03, UI04, UI06, UI07, UI08, PS03, PS04.

Vincular análises às premissas e à geração; parser monetário estrito por domínio; invalidar confirmações antes do transporte. Criar erro persistente e retentativa da mesma consulta sem remontagem. Distinguir catálogo vazio de falha. Integrar Voltar à hierarquia local. Importar inbox nativo offline e sinalizar erro/corrupção sem apresentar zero; preservar bytes e oferecer recuperação controlada.

**Dependência:** R1 para protocolo de identidade; preservar contratos compartilhados UI-STABILITY em todos os consumidores.

**Saída verificável:** Cada fluxo mantém rascunho, foco e dono em erro/retomada. Segundo toque, edição e Back durante mutação são descartados sem replay. ACK seguido de GET falho continua sendo salvo. Pendência local aparece mesmo com servidor indisponível.

## R4 — Tratar segurança e fechar o processo de publicação

**Prioridade:** SEC01 em paralelo a R1; demais antes de distribuir. **Itens:** SEC01, SEC02, OP01, OP02.

Atualizar PyMongo e revalidar o ambiente implantado. Concluir a raiz versionada e executar CI de fato. Corrigir ponteiro de release no README. Planejar chave privada de distribuição e migração compatível, sem desinstalar ou apagar armazenamento como atalho.

**Dependência:** R0 para preservar baseline. Migração de assinatura depende de prova no aparelho e estratégia para versões Android suportadas.

**Saída verificável:** Scanner sem os avisos identificados; fontes e build rastreáveis; versão/hash convergentes. Atualização/migração mantém fila, mídia, SecureStore e rascunhos.

## R5 — Homologar o Android e reconciliar eventual impacto histórico

**Prioridade:** Gate de conclusão. **Itens:** Todos, UI-R01, UI-R02, PS-R01, PS-R02.

Executar a matriz física abaixo em APK candidato identificado. Revisar somente em leitura a incidência dos defeitos na base real e nos dispositivos. Quando houver dano demonstrado, preparar reparo individual com identidade, origem, antes/depois, auditoria e reversão; ambiguidades ficam em revisão.

**Dependência:** R1–R4; aparelho disponível. Testar share bloqueado e capacidade antes de converter hipóteses em correções.

**Saída verificável:** Sem P1 aberto; scanner resolvido; testes atuais + novos probes de aceitação verdes; instalação sobre versão anterior comprovada; impacto histórico explicitamente medido ou declarado pendente.

## Protocolo comum de aceitação

1. Reproduzir o caso atual e manter a prova anterior; converter o probe para exigir comportamento correto, sem enfraquecer a asserção.
2. Corrigir o dono da regra e todos os consumidores semelhantes; atualizar o contrato canônico correspondente.
3. Executar regressões focalizadas e os gates integrados proporcionais, conservando dados e fingerprints.
4. Gerar candidato Android versionado; conferir assinatura, hash, download e proveniência das fontes.
5. Executar a matriz física abaixo e registrar cada lacuna. Publicação e homologação são etapas distintas.
6. Se dados históricos precisarem de reparo, preparar plano por identidade estável, fonte e antes/depois. Não deduplicar por valor/data/comerciante, não apagar filas e não recriar lançamentos como workaround.

Não migrar a assinatura apagando a instalação, nem consertar BE06 isoladamente de PS05. Não remover awaits essenciais a tokens para reduzir o indicador de processamento. Não tratar notificações com parsing ambíguo como movimentações certas.

## Homologação Android obrigatória para encerrar a recuperação

Registrar modelo/Android, versão/código/hash do APK, escala de fonte e teclado. Executar numa cópia de teste com dados sintéticos; preservar o app e histórico pessoais. A atualização final sobre a instalação existente precisa preservar armazenamento e identidade de assinatura.

| Eixo | Sequência adversarial | Resultado exigido |
|---|---|---|
| Navegação | Início→Extrato→Detalhe→Editar; Mais→cada subtela; Voltar físico/gesto/visual | Mesmo destino por hierarquia; overlay fecha primeiro; rascunho protegido |
| Seletores/teclado | Teclado aberto, abrir campo, buscar muitos/zero resultados, selecionar/limpar, repetir | Sem oscilação/remontagem; foco e texto estáveis; opções antigas não atuam na nova abertura |
| Mutações | Duplo toque, toque em outra aba, editar, Back; ACK normal, timeout e resposta perdida | Uma operação; ações posteriores descartadas sem replay; comando e recibo preservados |
| Diálogos | Cancelar, confirmar, blur tardio, background, troca de dono/desmontagem | Callback inválido não inicia transporte; confirmação vigente opera uma vez |
| Leituras | Resposta fora de ordem, erro inicial, GET falha após ACK, repetir mesma busca | Estado ligado à consulta vigente; erro e retry visíveis; nunca repetir escrita para corrigir leitura |
| Conflitos | 409 em categoria/cartão/conta/compromisso/cronograma e campo sobreposto/disjunto | Rascunho preservado; origem/local/remoto visíveis; sobreposição exige decisão |
| Persistência | Fechar processo durante digitação, gravação/arquivo, fila, ACK e limpeza local | Recuperação com identidade estável; nenhuma perda ou duplicação silenciosa |
| Offline | Notificação, digitação, voz e arquivo com modo avião; reabrir e reconectar | Pendências locais visíveis antes da rede; transporte só após permitido; ACK correto |
| Captura nativa | Notificação aprovada/recusada/rodapé, duas compras iguais; câmera/galeria/PDF | Origem e ocorrência preservadas, revisão para ambiguidade; arquivos sobrevivem |
| Share | App frio, já aberto, bloqueado, sessão expirada, biometria cancelada; duas imagens | Intenção não some nem se aplica à captura errada; comprovar PS-R02 |
| Segurança | Blur sem background, active antes de focus, expiração e invalidação externa | Cobertura e rede privada respeitam sessão; sensor não entra em loop |
| Acessibilidade | TalkBack, fonte 200%, tela pequena, textos longos e erro/loader | Nome/papel/estado anunciados; fundo inacessível sob diálogo; ações legíveis e alcançáveis |
| Capacidade | Muitos itens/arquivos, armazenamento quase cheio e JSON corrompido | Erro visível sem apagar fonte; falha não equivale a fila vazia; medir latência/ANR |
| Atualização | Instalar sobre versão anterior com filas e rascunhos preenchidos | Dados e comandos intactos; assinatura/versão corretas; retomada e replay idempotentes |

Lacunas atuais: aparelho/IME/sensores/TalkBack; incidência no histórico real; carga prolongada/ANR/disco; extração externa e formatos bancários reais; recuperação de produção a partir de backup atual. A integração local de backup/restauração não preenche a última lacuna.
