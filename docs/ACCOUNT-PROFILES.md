# Cadastro das contas

Contrato atualizado em 1.20.33: o inventário de uso inclui **todos** os compromissos e regras de valor, inclusive pausados, cancelados, concluídos e com cobranças futuras removidas. Prévia e exclusão usam `account_usage`; exclusão e criação/alteração de vínculos disputam a mesma trava de movimentações dentro da transação MongoDB. Uma corrida não pode deixar compromisso ou regra órfã.

Conta encerrada ou arquivada continua válida para manter o vínculo histórico durante edição, observações ou cancelamento. Trocar para outra conta ou criar vínculo exige conta selecionável. O editor conserva a conta histórica na lista e a distingue de novas escolhas. [Auditoria atual](AUDITORIA-ADVERSARIAL.md).

Em **Mais > Contas**, adicionar e editar apelido, instituição, tipo, agência e dígito, número e dígito, status, datas opcionais e observações. Campos bancários são textos para preservar zeros iniciais. Carteiras e contas de criptoativos dispensam agência e número. A moeda permanece BRL, conforme o modelo financeiro atual; criptoativos usam equivalente em reais informado pelo usuário.

O cartão mostra apenas o final do número; os dados completos ficam no formulário de detalhes. Os filtros Abertas, Encerradas, Arquivadas e Todas permitem consultar as contas. Todas inclui arquivadas. Encerrar representa a situação no banco; arquivar oculta da lista principal sem apagar histórico. Restaurar conserva o status de encerramento. Editar o cadastro não modifica o saldo de referência nem os lançamentos. O saldo continua desconhecido até que o usuário informe uma referência.

Excluir exige uma prévia e confirmação na interface. O servidor verifica novamente revisão e vínculos dentro de uma transação. Saldos registrados, capturas inclusive canceladas, transferências, eventos, lotes confirmados, movimentos removidos, experimentos e cadastros importados bloqueiam exclusão e permitem arquivamento. A exclusão de um cadastro vazio mantém uma marca interna de exclusão e a auditoria, impedindo reaparecimento de contas predefinidas ou reutilização acidental de uma chave.

## Persistência e compatibilidade

`account_profiles` é uma extensão separada do ledger v1.2. O esquema e os documentos originais de `accounts` permanecem intactos. As seis chaves anteriores são preservadas; contas novas têm chaves derivadas do identificador do pedido. Criação é idempotente; edição e exclusão exigem `expected_revision` e retornam 409 quando uma versão antiga tenta sobrescrever a nova. As gravações e auditorias são atômicas e usam a mesma trava de movimentações da ingestão e dos lotes.

`POST /v1/account-profiles`, `PATCH /v1/account-profiles/{key}`, `GET /v1/account-profiles/{key}/deletion` e `DELETE /v1/account-profiles/{key}` exigem sessão. `GET /v1/account-positions` inclui metadados e saldos; a referência de saldo continua no endpoint existente. O armazenamento é reservado na inicialização; falha de capacidade do MongoDB não derruba as leituras existentes, mas gravações sem capacidade retornam falha sem assumir confirmação.

Novas identidades funcionam em saldos, transferências, revisão e extratos em lote. O Extrato aceita `account_id=profile:{key}` em paginação normal e por cursor. Apelido, instituição e final distinguem contas do mesmo banco nos seletores. Contas encerradas e arquivadas permanecem consultáveis no histórico; novas transferências e escolhas iniciais de planejamento usam contas abertas. Revisões e importações de histórico podem conservar contas encerradas.

A ponte para uma conta importada só é automática quando existe exatamente uma candidata conhecida naquela instituição. Havendo múltiplas candidatas, o saldo mostra a ambiguidade e não soma lançamentos de uma candidata arbitrária. Novos cadastros não assumem vínculos com contas importadas. Nomes de bancos em descrições de extrato não bastam para conciliar transferências entre duas contas no mesmo banco.

Desde 1.20.4, o filtro pelo ObjectId importado inclui também capturas atribuídas ao perfil associado, nas duas paginações. Referências informadas com `as_of_date` representam o fim daquele dia civil: movimentos desde o primeiro instante do dia seguinte são incluídos. Referências de instante exato permanecem exclusivas; referências antigas sem marcador mantêm a regra anterior. A exclusão também é bloqueada por saldos, marcações, pendências e retratos de conclusão de fechamentos mensais; arquive a conta para preservar esses vínculos.

## Validação

Evidências em `../../audit_work/account-profiles-2026-10-01`: suíte Python, regressões de cliente, lint, TypeScript, exportação web, 21 verificações com MongoDB local em replica set e jornadas de interface com APIs simuladas em 390/320 px. Os probes de MongoDB incluem rollback real por falha de auditoria e concorrência entre exclusão e referência de saldo. A coleção histórica foi validada usando o mesmo esquema rígido do ambiente financeiro. Nenhum dado de teste é gravado na base financeira.

O APK precisa ser recompilado para levar a tela ao Android. Build e assinatura não comprovam instalação em aparelho físico.
