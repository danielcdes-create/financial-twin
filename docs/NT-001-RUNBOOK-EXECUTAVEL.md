# NT-001 — Runbook executável, contratos e evidências

Data: 29/09/2026. Complementa `NT-001-CONFIABILIDADE-E-EVOLUCAO.md`. O estado observado fica em `IMPLEMENTATION-STATUS.md`. Este documento especifica o procedimento; uma instrução escrita não comprova sua execução.

## 1. Contexto que o executor não deve tentar adivinhar

Diretório: `C:\Users\danie\FIN\financial-app`. Python: `.venv\Scripts\python.exe`. Código Python importável em `backend`; testes em `backend/tests`. Mobile: `mobile`, Expo 57, TypeScript, React Native. Entrada existente: Expo Router `mobile/app/index.tsx`, que carrega `App.tsx`. Não substituir o roteamento inteiro durante correção de uma regra financeira.

O mobile já tinha alterações anteriores. Não executar `git reset`, `git clean`, checkout em massa, limpeza de dados do aplicativo ou exclusão da fila para obter um teste verde. A raiz financeira não possui repositório Git próprio confirmado; o workflow de CI entregue precisa ser incluído num repositório que disponibilize `backend` e `mobile` nos caminhos esperados. Criar o YAML não significa que uma execução remota ocorreu.

A conexão de produção vem de `.env`; nunca exibir seu conteúdo. Os testes reais geram apenas bancos `financial_twin_test_<8 caracteres>`. A variante de restauração acrescenta `_restore`, mantendo o nome abaixo do limite observado no Atlas. Não derivar o banco de testes do nome de produção. Não usar a inicialização normal da aplicação nos testes, pois ela instala índices e inicia recuperação automática de eventos.

## 2. Mapa de propriedade das regras

| Regra | Arquivo proprietário | Consumidores |
|---|---|---|
| Papel econômico e sinal | `backend/app/economic_policy.py` | analytics, fechamento, compromissos, experimentos |
| UTC versus calendário civil | `backend/app/financial_time.py` | filtros, agregação mensal, matching |
| Cronograma e âncora | `backend/app/commitments.py` | compromissos e projeções |
| Janela de leitura do painel | `backend/app/dashboard_window.py` | dashboard e overview |
| Limites das filas e cursor | `backend/app/pagination.py` | revisões, capturas, comandos, casos, experimentos |
| Sessão transacional | `backend/app/atomic.py` | mutações e snapshots |
| Regras de decisão e liquidez | `backend/app/planning.py` | novas APIs, sem escrita financeira |
| Workflow e evidências | `backend/app/insights.py` | fechamento, guardião e experimentos |
| Arquivo durável | `mobile/utils/durableFiles.ts` e `.native.ts` | captura/importação e upload |
| Metadados locais | `mobile/utils/queueDb.web.tsx` e `queueDb.tsx` | fila, quarentena, rascunhos |
| Interface das cinco funções | `mobile/components/FinancialLab.tsx` | Mais → Decisões e evidências |
| Publicação Android | `mobile/scripts/publish-release.ps1` e `backend/app/release_manifest.py` | download e atualização |

Uma alteração na política econômica deve atualizar os consumidores por importação. Não copiar condicionais diferentes para cada tela. Os DTOs antigos podem continuar com valores numéricos para compatibilidade; os novos motores calculam com Decimal e respondem com strings monetárias.

## 3. Ordem exata dos lotes

1. **Q00 — preservar e registrar:** copiar fonte sem `node_modules`, `.git`, builds e credenciais; gerar hashes; registrar se a cópia é realmente anterior à edição. O checkpoint desta execução é posterior às primeiras correções; não tratá-lo como backup inicial integral.
2. **Q01 — semântica:** centralizar papel econômico e calendário. Aceitar 23h de Brasília mesmo quando o instante já está no dia UTC seguinte. Estorno positivo reduz consumo; transferência própria e pagamento de fatura não geram novo consumo.
3. **Q02 — matching:** normalizar instante; comparar direção/valor/instrumento; impedir match automático ambíguo ou com universo truncado; captura manual não se funde automaticamente.
4. **Q03 — persistência:** inserir transação e auditoria na mesma sessão; testar falha no audit após a escrita; exigir revisão em capturas já versionadas; invalidar vínculos financeiros obsoletos; devolver 409/428 com ação de recuperação.
5. **Q04 — recorrência:** conservar âncora e último dia do mês; distinguir vencimento civil de instante de pagamento; impedir quitação por transferência/fatura.
6. **Q05 — revisão:** cursor estável por data e `_id`, `total/has_more/next_cursor`; vendedor desativado por edição; aceitar só o conjunto da prévia; persistência de rascunho confirmada antes de dizer salvo.
7. **Q06 — offline:** copiar bytes antes de inserir referência na fila; subir os mesmos bytes no reenvio; ACK antes da remoção; falha de quota/arquivo/corrupção é erro recuperável explícito.
8. **Q07 — operação:** validar runtime/locks, alertas, backup e restauração isolada; produzir manifesto a partir do APK real.
9. **Q08 — fechamento:** usar a política única, explicar lacunas, carregar grafo, desfazer com revisão/idempotência. Snapshot consistente não comprova extratos completos.
10. **Q09 — guardião:** abrir caso explícito, reservar crédito único, gerar dossiê; preservar caso resolvido.
11. **Q10 — cenários:** testar centavos, âncora, saldo ausente, fluxo duplicado, reserva e parcelas fora do horizonte.
12. **Q11 — liquidez:** não financiar propostas com saldo desconhecido; preservar débitos futuros da origem; conferir conservação monetária.
13. **Q12 — experimentos:** um ativo, linha de base não sobreposta, denominador explícito, encerramento congela a data observada; não afirmar causalidade.
14. **Q13 — entrega:** testes completos, storage web, typecheck, lint, build, navegação real, APK com versão correta; registrar instalação física separadamente.

Não abrir cinco frentes de edição antes de concluir Q01–Q06. A fila representa dependências de integridade, não apenas preferência visual de organização.

## 4. Contratos HTTP implementados

Todas as rotas `/v1/insights/*` usam a dependência de sessão da aplicação. O modo de desenvolvimento local existente pode estar sem senha; isso não autoriza publicar o serviço na internet. Simulação e liquidez são cálculo, não execução bancária.

| Método e rota | Entrada | Saída / condição |
|---|---|---|
| GET `/v1/insights/closure` | `start,end` civis, até 367 dias | totais, papel econômico, contas, lacunas, consistência e prévia explícita |
| GET `/v1/insights/trail` | `reference=ledger:<ObjectId>` ou `capture:<provisional_id>` | nós, arestas e referências ausentes |
| POST `/v1/insights/evidence/{event_id}/undo` | UUID `request_id`, `expected_revision`, motivo de 5–500 caracteres | reabertura auditada; 409 para dependência/revisão divergente |
| GET `/v1/insights/planning-context` | `start,end` | compromissos previstos, sugestões de obrigações e contas; lacunas |
| POST `/v1/insights/simulate` | saldo, compra, parcelas, datas, reserva, fluxos | baseline, comprar, esperar, reduzir; curva diária |
| POST `/v1/insights/liquidity` | contas, reservas, limites, fluxos com conta | propostas, déficits descobertos e premissas |
| POST `/v1/insights/cases` | UUID, título, motivo, valor esperado, refs, notas | caso idempotente; mesmo UUID/outro corpo → 409 |
| GET `/v1/insights/cases` | limite e cursor | lista continuável |
| PATCH `/v1/insights/cases/{id}` | revisão, estado, referência de reembolso | crédito único, auditoria, revisão incrementada |
| GET `/v1/insights/cases/{id}/dossier` | ID | JSON e Markdown, fontes, histórico, ausências |
| GET `/v1/insights/guardian-detections` | `start,end` | suspeitas com vínculo explícito; nenhuma escrita |
| POST `/v1/insights/experiments` | UUID, categoria, protocolo, datas, alvo | experimento ativo; trava por proprietário |
| GET `/v1/insights/experiments` | limite e cursor | lista continuável |
| PATCH `/v1/insights/experiments/{id}` | revisão, concluir/cancelar, satisfação, fatores | libera trava e registra `actual_end` |
| GET `/v1/insights/experiments/{id}/report` | ID | taxa diária da base, observado, diferença e limites |

`start/end` são datas, não timestamps. Timestamps de eventos precisam de offset ou da convenção UTC documentada do PyMongo. Não converter `data_vencimento` legado à hora de Brasília: esse campo foi armazenado como data civil à meia-noite UTC.

## 5. Regras de conflito e reenvio

- Uma captura nova tem revisão 1. PATCH envia `expected_revision`; edição incrementa. Confirmar após editar deve enviar a revisão devolvida pelo PATCH, não a anterior.
- Captura versionada sem revisão solicitada devolve 428; revisão antiga devolve 409. Compatibilidade transitória existe para documentos legados ainda sem campo `revision`.
- Repetir uma decisão terminal idêntica não cria outro fato econômico. Decisão terminal conflitante pede recarga.
- Confirmar evidência após undo substitui o vínculo ativo preservando seu antes/depois no audit; revisão do vínculo cresce. Não inserir uma segunda evidência com o mesmo `event_id` sob índice único.
- Uma falha ao escrever auditoria aborta a transação. Nenhuma resposta de sucesso deve ser emitida antes do commit.
- Falhas transientes de transação devolvem conflito. O cliente deve recarregar e repetir a ação com o mesmo identificador quando aplicável; não gerar IDs novos para contornar timeout.
- Caso resolvido não troca de crédito. Captura usada como reembolso não muda de valor/data/natureza silenciosamente. Créditos de transferência própria não resolvem casos de reembolso.
- O bloqueio de experimento usa o documento único `experiment_locks._id=owner`, dentro da mesma transação da criação/conclusão. Não depende de uma consulta “não existe ativo” seguida de inserção sem trava.

## 6. Regras detalhadas das filas

O cursor é opaco. A ordenação usa data descendente e `_id` descendente como desempate. A consulta busca `limit+1`; o item adicional indica `has_more`, mas não é consumido. O cursor usa o último item efetivamente entregue. O hash dos filtros impede reaproveitar um cursor de outra consulta nas filas genéricas.

O usuário deve ver quantos itens foram carregados e poder continuar. Limite de 100/500 é tamanho de página, não tamanho total da fila. A prévia de fechamento de 100 movimentos é identificada como prévia e oferece orientação para consultar Movimentações; não representa corte nos totais.

Filas que sofrem alterações enquanto são percorridas não são um snapshot histórico imutável: itens novos acima do cursor aparecem ao atualizar do início. A continuação não deve duplicar itens já apresentados. Após uma decisão, atualizar contagem e lista; em conflito, manter rascunho e recarregar.

Na web, a consulta da fila inclui `SELECT e.*, s.attempt_count, s.next_attempt_at, s.last_error`. O adaptador precisa aceitar essa projeção expandida. Não trocar “consulta desconhecida” por `[]`; isso esconderia uma fila existente. Ler JSON malformado gera erro e preserva o conteúdo original.

Propagação de vendedor começa desativada. Alterar o texto do vendedor remove o opt-in anterior. A prévia carrega um token calculado sobre contexto e registros. O servidor recalcula antes de gravar, na transação. Mesmo total com identidades/conteúdo diferentes também invalida a aceitação.

## 7. Precisão dos motores

Compra de R$ 10,00 em três parcelas: R$ 3,34 + R$ 3,33 + R$ 3,33. Se o horizonte termina após a primeira, devolver R$ 6,66 como compromisso após o horizonte. Não informar economia só porque parcelas ficaram fora da curva.

Simulação de R$ 300 em três parcelas, saldo inicial R$ 1.000: comprar agora encerra em R$ 700 no horizonte que inclui todas as parcelas; reduzir para 80% encerra em R$ 760. Ao adiar, o saldo pode ficar maior dentro do horizonte porque parte da dívida fica depois; o valor posterior deve continuar visível.

Origem com R$ 100, reserva de R$ 20 e débito futuro de R$ 70 só pode doar R$ 10, mesmo que o destino precise de R$ 30. Receita futura apenas assumida não pode aumentar a capacidade atual de doação. Depois da data da receita, continua sendo cenário condicionado à premissa, não recomendação garantida.

Conta sem referência ou com capturas sem atribuição não entra como saldo verificado na tela de liquidez. Uma API de simulação pode receber saldo manual explícito; a resposta o chama de premissa. Binance não é doadora automática de caixa bancário.

Obrigações importadas aparecem desmarcadas. O usuário confirma ausência de sobreposição com o cronograma manual e escolhe conta. Uma obrigação vencida selecionada assume pagamento hoje e exibe seu vencimento original. Compras de cartão não entram de novo quando o saldo de sua fatura é incluído.

## 8. Comandos e critérios de aceite

Executar na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\scripts\validate.ps1
.\scripts\validate.ps1 -Integration -WebExport
```

`validate.ps1` verifica cada exit code; não confiar no exit code da última instrução de uma sequência que já falhou antes. Integração exige acesso a MongoDB replica set e cria dados sintéticos. O script de integração é explícito e não roda automaticamente durante o startup de produção.

Os scripts `check_*` que criam bancos sintéticos exigem MongoDB local por padrão. Configure `MONGODB_URI=mongodb://127.0.0.1:27028/?replicaSet=auditfixes` na sessão do teste; não altere o `.env` financeiro. Usar Atlas conscientemente exige `FIN_ALLOW_REMOTE_TEST_DATABASE=1`. Cada banco de teste consome o limite compartilhado de coleções, mesmo sem tocar no ledger. A consulta `check_convergence.py --readonly` continua permitida no banco configurado.

O startup reserva `deleted_movements` e seu índice antes de atender solicitações. Se o Atlas atingir o limite, preserve o banco sintético identificado pelas evidências com `backup_restore.py`, valide sua restauração e os hashes em outro servidor e só então retire aquela cópia remota. Não apague bancos pelo prefixo em lote nem retire coleções financeiras para liberar capacidade.

Comandos individuais úteis:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -q
.\.venv\Scripts\python.exe backend/scripts/check_integration.py data/test-evidence/run-unico
.\.venv\Scripts\python.exe -m pip_audit -r backend/requirements.lock.txt --no-deps --disable-pip
```

No diretório mobile:

```powershell
node scripts/test-web-storage.cjs
pnpm exec tsc --noEmit
pnpm exec eslint App.tsx components utils app
pnpm audit --audit-level=moderate
pnpm exec expo export --platform web --output-dir desktop-dist
```

Testes obrigatórios ao alterar persistência: falha após primeira escrita; mesma solicitação repetida; mesma chave com outro conteúdo; revisão obsoleta; duas solicitações que disputam o mesmo reembolso/experimento. Não testar apenas serialização de DTO.

Testes obrigatórios ao alterar calendário: 02:59:59 UTC e 03:00:00 UTC nos limites do dia de Brasília; janeiro 31 → fevereiro → março 31; ano bissexto; filtros de um único dia; pagamento noturno com mês UTC diferente do mês local.

## 9. Dependências e compatibilidade

Python possui `requirements.lock.txt`; desenvolvimento acrescenta ferramentas em `requirements-dev.txt`. O lock de runtime passou no pip-audit nesta execução. Isso representa os avisos conhecidos no momento, não ausência universal de vulnerabilidades.

Mobile fixa `xcode>uuid` em 11.1.1, que ainda fornece CommonJS. `query-string>decode-uri-component` usa 0.5.0, corrigida e ESM; `patches/query-string@7.1.3.patch` adapta o import para export default. Não atualizar só a versão removendo o patch: Node e Metro precisam consumir a função correta. Teste manual reproduzível: parser de query com entrada percentual inválida e geração de UUID via xcode; depois export Expo.

Referências de manutenção: [uuid 11.1.1](https://github.com/uuidjs/uuid/releases/tag/v11.1.1), [advisory decode-uri-component](https://github.com/SamVerschueren/decode-uri-component/security/advisories/GHSA-vcc3-ghjq-m6fr). Não usar uma faixa `>=0.4.3` como prova de que essa versão foi publicada; a versão disponível adotada foi 0.5.0.

## 10. Backup, release e homologação

Backup em BSON Extended JSON comprimido conserva ObjectId, Decimal128 e datas; inclui índices, opções e arquivos referenciados com SHA256. Restauração confere hashes antes de gravar, só aceita banco de teste vazio e não apaga coleções existentes. Backup sequencial não é snapshot global: parar escritas antes de usá-lo como ponto de recuperação de produção.

O APK servido precisa de `.manifest.json` com versão lida pelo `aapt`, tamanho e hash do próprio arquivo. Alterar `app.json` sem gerar APK não muda a versão disponível para download. Manifesto ausente/divergente deixa atualização indisponível; não mostrar “aplicativo atualizado” a partir dessa ausência.

Compilação Windows deve usar caminho curto quando Ninja excede limite de caminho. Esta execução usa cópia isolada `C:\f17`. Não executar `/MIR` nem exclusão recursiva em raiz calculada para sincronizar. Depois de qualquer edição tardia, copiar os arquivos alterados para a cópia de build e recompilar antes de promover o APK.

Homologação física ainda precisa confirmar: instalar sobre versão anterior sem limpar dados; retomar rascunho após fechar processo; fila de arquivo em modo avião/reabertura; notificação de compra aprovada versus negada; duas notificações enquanto um ACK remove outra; download e instalação da atualização. `adb devices` vazio não é teste físico.

Não executar reparos históricos por inferência. Capturas antigas sem conta permanecem lacunas até atribuição humana. Não declarar extratos completos sem evidência de cobertura. Não habilitar envio a bancos, cancelamento de contratos ou mensagens externas a partir desses cálculos.

## 11. Próximo executor: rotina de continuidade

Leia o status, rode apenas o teste que corresponde ao defeito observado, reproduza sem dados de produção, faça a menor correção de domínio, acrescente regressão e atualize evidência. Se todos os checks passaram e só falta dispositivo, não reescreva o sistema para simular essa prova. Registre `blocked_external` para homologação física e a ação concreta necessária.

Uma etapa pode ter código e integração validados, mas dispositivo pendente. Não reduzir esses três estados a “pronto para tudo”. A nota é normativa e revisável por evidência; não promete perfeição de execução por qualquer LLM.
