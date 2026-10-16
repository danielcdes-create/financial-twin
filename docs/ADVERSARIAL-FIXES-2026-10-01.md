# Correções da auditoria adversarial do Financial Twin

Os 15 achados da [auditoria de 01/10/2026](../../audit_work/adversarial-2026-10-01/RELATORIO.md) foram tratados no backend, cliente e controles de release. As correções preservam fontes e histórico financeiro; não foi feita reconstrução ou correção heurística de movimentações existentes.

## Correções e comprovação

| Achado | Correção | Validação |
|---|---|---|
| A01 | Leitura, validação e reserva do pagamento e compromisso na mesma transação; concorrência retorna 409. | Edição para R$ 500 aceita; vínculo obsoleto recusado; nova tentativa vincula R$ 500. |
| A02 | Tipo também é protegido em capturas, ledger e lote quando há vínculo financeiro. | Três caminhos recusados com 409, sem neutralizar pagamento. |
| A03 | Correspondência valida candidato, avança revisão e atualiza evento, estado e evidência atomicamente. | Candidato incompleto retorna 422; válido avança revisão; editor antigo retorna 409; reenvio mantém uma evidência. |
| A04 | Payload validado antes de gravar; inválidos antigos passam a rejeitados; falhas transitórias registram tentativas e próxima execução. | Cem inválidos e cem falhas transitórias não monopolizam recuperações subsequentes. |
| A05 | UUID `creation_id` obrigatório; comando, resultado, cadastro e auditoria são atômicos. Cliente retém tentativa localmente. | Reenvio e concorrência geram um cadastro; conteúdo diferente retorna 409; UUIDs distintos permitem assinaturas iguais; falha de auditoria desfaz comando e cadastro. |
| A06 | Direção aceita somente `expense` ou `income`. | `debit` retorna 422 antes de gravar evento. |
| A07 | Valor final precisa ser finito e diferente de zero; confirmados reaplicam contrato após normalização e edição. | Fração que arredonda a zero, zero histórico e tipo em branco são recusados. |
| A08 | Busca examina até 500 candidatos; overflow impede presumir unicidade. | Segunda perna na posição 21 e lista de 501 não geram vínculo automático. |
| A09 | Reconciliação considera `sha256` e `file_sha256` e atualiza todos os eventos e lotes referenciados. | Arquivo saudável permanece pronto; corrupção e restauração chegam a todas as referências. |
| A10 | Instante canonicalizado em UTC com milissegundos, precisão persistida pelo BSON. | Reenvio idêntico com microssegundos retorna 202, mantendo um evento. |
| A11 | Alertas comuns usam modal no web e alerta nativo nas demais plataformas. | Formulário inválido e confirmação recusada mostram mensagem; cancelar conserva captura; descarte confirmado executa callback. |
| A12 | Instantes são exibidos no dia civil de Brasília; vencimentos civis permanecem datas. | Compra em 01/10 às 02h30 UTC aparece em 30/09 no Extrato, Detalhe e revisão; utilitário também aplicado ao editor e exclusão. |
| A13 | Plataforma, vendedor e categoria ocupam campos verticais com quebra de texto. | Inspeção e screenshots em 320 e 390 px. |
| A14 | Abas fornecem `aria-selected` além do estado nativo. | Cinco abas retornam true/false; árvore acessível anuncia seleção. |
| A15 | Controle compara API com manifesto e APK verificados, aceita versão alvo explícita; status antigo é histórico. | Controle deixa de depender dos códigos 34 e 35. |

## Validação executada

Passaram 187 testes Python, sete scripts de regressão do cliente, dois testes de fila, TypeScript, ESLint sem avisos e exportação desktop. As cinco suítes integradas existentes passaram com 96 verificações, incluindo HTTP, MongoDB, concorrência e backup/restauração. O novo script `backend/scripts/check_adversarial_fixes.py` passou com 31 verificações.

O Atlas atingiu o limite de 500 coleções ao tentar criar bancos de teste. As suítes integradas desta entrega foram executadas em MongoDB Community 8.0.15 local, ligado somente a `127.0.0.1:27028`, como replica set. Isso valida transações e conflitos reais, mas não constitui teste de capacidade do Atlas. Bancos sintéticos e resultados foram preservados para inspeção.

A interface foi verificada em cópia da exportação desktop apontando para API e banco sintéticos. Nenhum documento ou áudio do usuário foi enviado para extração por IA.

Evidências em [adversarial-fixes-2026-10-01](../../audit_work/adversarial-fixes-2026-10-01/): `validation.log`, `local-domain/regressions.json`, `convergence.json`, `future-integration.json`, `batch-integration.json`, `deletion-integration.json` e `core-integration/integration-results.json`.

![Detalhe em 320 px com data civil correta](../../audit_work/adversarial-fixes-2026-10-01/detail-320.png)

![Mensagem visível no desktop](../../audit_work/adversarial-fixes-2026-10-01/dialog-390.png)

## Entrega verificada

A API ativa confirmou MongoDB conectado e o novo contrato de criação no OpenAPI. A exportação desktop foi atualizada. O endpoint `/v1/app-update` disponibiliza Android **1.19.8 / versionCode 44**, ARM64, com **47.313.963 bytes**. O download integral retornou o mesmo tamanho e SHA256 do manifesto: `317DF93846A024ACFA3D52206CA1754E7666748F90694F3BEF60748BFB8A3DA2`. A assinatura foi verificada e o certificado coincide com o APK anterior.

As oito coleções financeiras inspecionadas conservaram contagens e hashes antes e depois da entrega, incluindo transações, obrigações, planos, compromissos e capturas provisórias. Evidências: `runtime.json`, `production-before.json`, `production-after.json`, `signature-new.log`, `signature-old.log` e `release-gate/live-release-results.json` no diretório acima. O APK anterior foi preservado em `release-before`.

## Contratos e recuperação

O POST de compromissos requer `creation_id` UUID. A ausência retorna 428; a mesma identidade e conteúdo devolvem o resultado original; a mesma identidade com outro conteúdo retorna 409. O UUID identifica uma tentativa, não a assinatura pelo nome ou valor. Clientes antigos precisam ser atualizados para criar compromissos com esse contrato.

O formulário conserva a tentativa na tabela local `batch_drafts`, chave `commitment-create`. Uma resposta incerta permite “Retomar cadastro pendente” e reenviar os dados originais. A edição fica bloqueada até confirmar o resultado. Uma rejeição 422 comprovada permite corrigir os campos. Falha no armazenamento impede iniciar a escrita remota; não apague dados locais para recuperar um envio.

Eventos rejeitados conservam evidência e erro. Falhas transitórias permanecem pendentes, com tentativas e próxima execução. Corrigir uma evidência inválida exige nova identidade de evento; não reescreve a fonte recebida.

## Limites de homologação

O navegador comprova os fluxos web testados. Microfone, câmera, compartilhamento, suspensão do Android, instalação física e acesso pelo Tailscale a partir do celular precisam de homologação no dispositivo. Build e manifesto válidos não comprovam instalação física.

Os originais dos principais editores estão em `audit_work/adversarial-fixes-2026-10-01/source-before`. Cliente e API precisam de reversão coordenada, pois o cadastro passou a exigir identidade de reenvio. Nenhuma migração do ledger foi introduzida.
