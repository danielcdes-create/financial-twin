import {formatCivilDate} from './formMask';

export function civilDateLabel(value: string | null | undefined): string {
  if (!value) return '';
  return formatCivilDate(value);
}

export function installmentLabel(count: number): string {
  return Number(count) === 1 ? '1 parcela' : `${count} parcelas`;
}

const ALTERNATIVES = [
  {name: 'buy_now', title: 'Comprar agora'},
  {name: 'wait', title: 'Esperar 30 dias'},
  {name: 'reduce', title: 'Alternativa 20% mais barata'},
] as const;

const BLANK_HOURS = 'Horas de trabalho não calculadas: a renda líquida e as horas do mês ficaram em branco.';
const BLANK_GOAL = 'O adiamento da meta não foi calculado: o valor alvo e o aporte mensal ficaram em branco.';
const BLANK_BALANCE = 'O menor saldo não foi calculado: o saldo inicial ficou em branco.';
const UNDATED_GOAL = 'Dentro de dez anos, com estes aportes, a meta ainda não tem data.';
const UNCALCULATED_ALTERNATIVE = 'Não calculada: falta o saldo inicial.';

const KNOWN_GAPS: Record<string, string> = {
  'Informe saldo inicial conhecido ou uma premissa explícita.': BLANK_BALANCE,
  'Informe renda líquida e horas trabalhadas no mês para calcular horas de trabalho.': BLANK_HOURS,
  'Informe uma meta e aporte mensal para estimar eventual adiamento.': BLANK_GOAL,
  'A meta não foi alcançada no limite de dez anos destas premissas.': UNDATED_GOAL,
};

const CONTEXT_REDUNDANT_GAPS = new Set([
  'Obrigações importadas só entram após seleção e conferência de sobreposição com compromissos. Faturas entram pelo saldo da obrigação, sem somar novamente as compras.',
  'Fluxos previstos não comprovam pagamentos.',
]);

const OBLIGATION_TYPE_LABELS: Record<string, string> = {
  fatura_cartao: 'Fatura do cartão',
  fatura: 'Fatura do cartão',
  financiamento: 'Financiamento',
  parcelamento: 'Compra parcelada',
  emprestimo: 'Empréstimo',
  imposto: 'Imposto / tributo',
  boleto: 'Boleto',
  obrigacao: 'Obrigação',
};

export function purchaseGapSentence(gap: string): string {
  return KNOWN_GAPS[gap] || gap.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_match, year: string, month: string, day: string) => `${day}/${month}/${year}`);
}

export function formatPurchaseFlowLabel(label: string | null | undefined): string {
  if (!label) return 'Compromisso';
  return String(label)
    .replace(/^([a-z_]+)(?=\s*·)/i, match => OBLIGATION_TYPE_LABELS[match.toLowerCase()] || (match === match.toLowerCase() ? match.charAt(0).toUpperCase() + match.slice(1).replace(/_/g, ' ') : match))
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_m, year: string, month: string, day: string) => `${day}/${month}/${year}`)
    .replace(/\b(\d{4})-(\d{2})\b/g, (_m, year: string, month: string) => `${month}/${year}`);
}

export function purchaseHorizonSubtitle(horizon?: {
  start?: string | null;
  end?: string | null;
  days?: number | null;
  minimum_months?: number | null;
} | null): string {
  if (!horizon?.start || !horizon?.end) return '';
  const start = civilDateLabel(horizon.start);
  const end = civilDateLabel(horizon.end);
  const days = Number(horizon.days || 0);
  const effectiveMonths = Math.max(days >= 90 ? 3 : 1, Number(horizon.minimum_months || 0));
  const monthText = `pelo menos ${effectiveMonths} ${effectiveMonths === 1 ? 'mês' : 'meses'}`;
  return days > 0 ? `De ${start} a ${end} · ${days} dias (${monthText})` : `De ${start} a ${end} · ${monthText}`;
}

export type ExcludedSourceItem = {
  ref?: string | null;
  label?: string | null;
  reason?: string | null;
};

function formatNamePreview(labels: string[], limit = 3): string {
  const unique = [...new Set(labels.map(item => item.trim()).filter(Boolean))];
  if (!unique.length) return '';
  const shown = unique.slice(0, limit).join(', ');
  return unique.length > limit ? ` (${shown} e mais ${unique.length - limit})` : ` (${shown})`;
}

export function summarizePlanningContext(
  context?: {
    flows?: unknown[];
    suggested_flows?: unknown[];
    gaps?: string[];
    excluded_sources?: ExcludedSourceItem[];
  } | null,
  options?: {includeProfiles?: boolean},
): {summary: string; notices: string[]} {
  const flowsCount = (context?.flows || []).length;
  const suggestedCount = (context?.suggested_flows || []).length;
  let summary = '';
  if (flowsCount === 0 && suggestedCount === 0) {
    summary = 'Nenhum compromisso previsto ou obrigação importada neste período.';
  } else if (flowsCount > 0 && suggestedCount === 0) {
    summary = `${flowsCount === 1 ? '1 previsão incluída' : `${flowsCount} previsões incluídas`} automaticamente neste período.`;
  } else if (flowsCount === 0 && suggestedCount > 0) {
    summary = `${suggestedCount === 1 ? '1 obrigação importada disponível (desmarcada)' : `${suggestedCount} obrigações importadas disponíveis (desmarcadas)`}. Marque apenas após conferir que não repete outra previsão.`;
  } else {
    summary = `${flowsCount} ${flowsCount === 1 ? 'previsão marcada' : 'previsões marcadas'} e ${suggestedCount} ${suggestedCount === 1 ? 'obrigação importada desmarcada' : 'obrigações importadas desmarcadas'}. Marque faturas ou obrigações importadas apenas após conferir que não repetem outra previsão.`;
  }

  const notices: string[] = [];
  for (const rawGap of context?.gaps || []) {
    const gap = String(rawGap || '').trim();
    if (!gap || CONTEXT_REDUNDANT_GAPS.has(gap)) continue;
    const sentence = purchaseGapSentence(gap);
    if (!notices.includes(sentence)) notices.push(sentence);
  }

  let cardExcludedCount = 0;
  const cardExcludedLabels: string[] = [];
  let conflictExcludedCount = 0;
  const conflictExcludedLabels: string[] = [];
  let profileExcludedCount = 0;
  const profileExcludedLabels: string[] = [];
  const otherReasons: string[] = [];

  for (const item of context?.excluded_sources || []) {
    const ref = String(item?.ref || '');
    const reason = String(item?.reason || '').trim();
    const label = String(item?.label || '').trim();
    if (!reason) continue;
    if (ref.startsWith('profile:') || reason.includes('Conta encerrada ou arquivada')) {
      profileExcludedCount += 1;
      if (label) profileExcludedLabels.push(label);
      continue;
    }
    if (reason.includes('vencimento da fatura') || reason.includes('duplicar compra e fatura')) {
      cardExcludedCount += 1;
      if (label) cardExcludedLabels.push(label);
      continue;
    }
    if (reason.includes('Pagamento histórico contradiz')) {
      conflictExcludedCount += 1;
      if (label) conflictExcludedLabels.push(label);
      continue;
    }
    const formatted = purchaseGapSentence(reason);
    if (!otherReasons.includes(formatted)) otherReasons.push(formatted);
  }

  if (cardExcludedCount > 0) {
    const names = formatNamePreview(cardExcludedLabels);
    const subject = cardExcludedCount === 1
      ? `1 compromisso no cartão${names} ficou fora da lista individual`
      : `${cardExcludedCount} compromissos no cartão${names} ficaram fora da lista individual`;
    notices.push(`${subject} para não somar duas vezes com a fatura. Para prever esse gasto no caixa, marque a fatura correspondente.`);
  }
  if (conflictExcludedCount > 0) {
    const names = formatNamePreview(conflictExcludedLabels);
    const subject = conflictExcludedCount === 1
      ? `1 compromisso${names} tem divergência entre histórico e próxima cobrança`
      : `${conflictExcludedCount} compromissos${names} têm divergência entre histórico e próxima cobrança`;
    notices.push(`${subject}; confira a agenda em Compromissos antes de projetar o caixa.`);
  }
  if (options?.includeProfiles && profileExcludedCount > 0) {
    const names = formatNamePreview(profileExcludedLabels);
    notices.push(
      profileExcludedCount === 1
        ? `1 conta encerrada ou arquivada${names} foi ocultada desta seleção.`
        : `${profileExcludedCount} contas encerradas ou arquivadas${names} foram ocultadas desta seleção.`,
    );
  }
  for (const other of otherReasons) {
    if (!notices.includes(other)) notices.push(other);
  }

  return {summary, notices};
}

type Money = (value: string | null | undefined) => string;
type CurvePoint = {
  date?: string | null;
  opening?: string | null;
  closing?: string | null;
  below_reserve?: boolean;
};
type ScenarioRow = {
  name: string;
  purchase_total?: string | null;
  minimum_balance?: string | null;
  closing_balance?: string | null;
  outside_horizon_total?: string | null;
  first_reserve_breach?: string | null;
  curve?: CurvePoint[];
};

function parseAmount(value: string | number | null | undefined): number {
  if (value == null || String(value).trim() === '') return 0;
  const parsed = Number(String(value).trim().replace(/\s/g, '').replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : 0;
}

function remainderAfterPeriod(value: string | null | undefined): number {
  return parseAmount(value);
}

function lowestClosingPoint(curve?: CurvePoint[]): {date: string; amount: string} | null {
  if (!Array.isArray(curve) || !curve.length) return null;
  let best: {date: string; amount: string; num: number} | null = null;
  for (const point of curve) {
    if (!point?.date || point.closing == null) continue;
    const num = parseAmount(point.closing);
    if (!best || num < best.num) {
      best = {date: civilDateLabel(point.date), amount: String(point.closing), num};
    }
  }
  return best ? {date: best.date, amount: best.amount} : null;
}

function scenarioSentence(row: ScenarioRow, money: Money, installments: number): string {
  const totalNum = parseAmount(row.purchase_total);
  const count = Math.max(1, Number(installments || 1));
  const perInstallment = count > 1 && totalNum > 0 ? (totalNum / count).toFixed(2) : null;
  const parcelNote = perInstallment ? ` (${count}x de ~${money(perInstallment)})` : '';
  const price = `O preço é ${money(row.purchase_total)}${parcelNote}. O menor saldo no período é ${money(row.minimum_balance)}.`;
  const closingBit = row.closing_balance != null ? ` O saldo ao final do período fica em ${money(row.closing_balance)}.` : '';
  if (remainderAfterPeriod(row.outside_horizon_total) > 0) {
    return `${price}${closingBit} Depois deste período ainda há ${money(row.outside_horizon_total)} em parcelas.`;
  }
  return `${price}${closingBit} O período já inclui todas as parcelas desta compra.`;
}

function scenarioHighlight(
  name: string,
  row: ScenarioRow,
  buyNow: ScenarioRow,
  installments: number,
  workHours: string | null | undefined,
  money: Money,
): string | null {
  const count = Math.max(1, Number(installments || 1));
  const totalNow = parseAmount(buyNow.purchase_total);
  const parcelNow = (totalNow / count).toFixed(2);
  const minNow = parseAmount(buyNow.minimum_balance);
  const minRow = parseAmount(row.minimum_balance);

  if (name === 'buy_now') {
    const lowPoint = lowestClosingPoint(row.curve);
    const lowDetail = lowPoint && parseAmount(lowPoint.amount) > minNow
      ? ` · menor fechamento diário após entradas: ${money(lowPoint.amount)} em ${lowPoint.date}`
      : '';
    if (row.first_reserve_breach) {
      return `Atenção: toca na reserva mínima a partir de ${civilDateLabel(row.first_reserve_breach)}${lowDetail}.`;
    }
    return count > 1
      ? `Compromete ${money(parcelNow)} por mês durante ${count} meses${lowDetail}.`
      : `Saída imediata de ${money(buyNow.purchase_total)}${lowDetail}.`;
  }

  if (name === 'wait') {
    if (buyNow.first_reserve_breach && !row.first_reserve_breach) {
      return `Vantagem: adiar 30 dias evita furar a reserva mínima (que ocorreria em ${civilDateLabel(buyNow.first_reserve_breach)}).`;
    }
    if (minRow > minNow + 0.005) {
      return `Vantagem: eleva o menor saldo do período em +${money((minRow - minNow).toFixed(2))} ao dar 30 dias de fôlego antes da 1ª cobrança.`;
    }
    return `Fôlego imediato: mantém +${money(parcelNow)} livres nos primeiros 30 dias antes de iniciar os pagamentos.`;
  }

  if (name === 'reduce') {
    const totalReduced = parseAmount(row.purchase_total);
    const savedTotal = Math.max(0, totalNow - totalReduced);
    if (savedTotal <= 0) return null;
    const savedPerParcel = (savedTotal / count).toFixed(2);
    const parcelReduced = (totalReduced / count).toFixed(2);
    const hoursNum = workHours ? parseAmount(workHours) : 0;
    const savedHours = hoursNum > 0 ? (hoursNum * 0.2).toFixed(1).replace('.', ',') : null;
    const savedDays = hoursNum > 0 ? ((hoursNum * 0.2) / 8).toFixed(1).replace('.', ',') : null;
    const hoursBit = savedHours ? ` e poupa ${savedHours} horas de trabalho (~${savedDays} dias úteis de 8h)` : '';
    return count > 1
      ? `Economia real de ${money(savedTotal.toFixed(2))} no total (${money(savedPerParcel)} a menos por mês · parcela cai para ${money(parcelReduced)})${hoursBit}.`
      : `Economia imediata de ${money(savedTotal.toFixed(2))} no caixa${hoursBit}.`;
  }

  return null;
}

type PurchaseGoal = {baseline_date?: string | null; purchase_date?: string | null; delay_days?: number | null};
export type PurchaseResultView = {
  price?: string | null;
  installments?: number | null;
  installment_amounts?: string[];
  work_hours?: string | null;
  horizon?: {start?: string | null; end?: string | null; days?: number | null};
  goal?: PurchaseGoal | null;
  cash_scenarios?: {status?: string; scenarios?: ScenarioRow[]};
  gaps?: string[];
};
export type PurchaseVerdict = {
  badge: string;
  headline: string;
  detail: string;
  tone: 'positive' | 'caution' | 'warning' | 'neutral';
};
export type PurchaseCopy = {
  price: string;
  installments: string;
  installmentDetail: string | null;
  hours: string | null;
  hoursDetail: string | null;
  goalHeadline: string | null;
  goalDetail: string | null;
  verdict: PurchaseVerdict;
  cashSummary: string | null;
  window: string;
  reserve: string | null;
  alternatives: {title: string; body: string; highlight?: string | null}[];
  notes: string[];
};

export function purchaseResultCopy(result: PurchaseResultView, money: Money, goalName = ''): PurchaseCopy {
  const scenarios = result.cash_scenarios?.scenarios || [];
  const baselineScenario = scenarios.find(row => row.name === 'baseline') || null;
  const byName = new Map(scenarios.filter(row => row.name !== 'baseline').map(row => [row.name, row]));
  const current = byName.get('buy_now');
  const waitScenario = byName.get('wait');
  const reduceScenario = byName.get('reduce');
  const cashReady = result.cash_scenarios?.status !== 'insufficient_data' && Boolean(current);

  const count = Math.max(1, Number(result.installments || 1));
  const totalPriceNum = parseAmount(result.price);
  const firstInstallment = result.installment_amounts?.[0] || (totalPriceNum > 0 ? (totalPriceNum / count).toFixed(2) : null);
  const lastInstallment = result.installment_amounts?.[result.installment_amounts.length - 1] || firstInstallment;
  const installmentDetail = count > 1 && firstInstallment
    ? (firstInstallment === lastInstallment
        ? `${count}x de ${money(firstInstallment)} por mês`
        : `${count}x (de ${money(firstInstallment)} a ${money(lastInstallment)})`)
    : count === 1 && result.price
      ? `Pagamento à vista de ${money(result.price)}`
      : null;

  const alternatives = ALTERNATIVES.map(item => {
    const row = byName.get(item.name);
    if (!cashReady || !row || !current) return {title: item.title, body: UNCALCULATED_ALTERNATIVE, highlight: null};
    return {
      title: item.title,
      body: scenarioSentence(row, money, count),
      highlight: scenarioHighlight(item.name, row, current, count, result.work_hours, money),
    };
  });

  const hoursNum = result.work_hours ? parseAmount(result.work_hours) : 0;
  const hours = result.work_hours ? `${String(result.work_hours).replace('.', ',')} horas da sua renda de trabalho` : null;
  let hoursDetail: string | null = null;
  if (hoursNum > 0) {
    const workDays8h = (hoursNum / 8).toFixed(1).replace('.', ',');
    const perParcelHours = count > 1 ? (hoursNum / count).toFixed(1).replace('.', ',') : null;
    const hourlyRate = totalPriceNum > 0 ? (totalPriceNum / hoursNum).toFixed(2) : null;
    const parts = [`Equivale a ~${workDays8h} dias úteis de 8h dedicados a pagar esta compra`];
    if (perParcelHours) parts.push(`${perParcelHours}h de trabalho por parcela mensal`);
    if (hourlyRate) parts.push(`renda-hora considerada de ${money(hourlyRate)}/h`);
    hoursDetail = parts.join(' · ') + '.';
  }

  const baseline = result.goal?.baseline_date ? civilDateLabel(result.goal.baseline_date) : '';
  const bought = result.goal?.purchase_date ? civilDateLabel(result.goal.purchase_date) : '';
  const trimmedGoal = goalName.trim();
  let goalHeadline: string | null = null;
  let goalDetail: string | null = null;
  if (result.goal) {
    if (baseline && bought && (result.goal.delay_days || 0) > 0) {
      const delayMonths = Math.max(1, Math.round((result.goal.delay_days || 0) / 30));
      goalHeadline = `${result.goal.delay_days} dias de adiamento ${trimmedGoal ? `de ${trimmedGoal}` : 'da meta'}`;
      goalDetail = `Sem esta compra, a meta ficaria para ${baseline}. Com a compra, fica para ${bought} (~${delayMonths} ${delayMonths === 1 ? 'mês' : 'meses'} a mais de jornada).`;
    } else if (baseline && bought) {
      goalHeadline = 'Esta compra não adia a meta.';
      goalDetail = `A meta fica para ${baseline}, com ou sem esta compra.`;
    } else if (baseline) {
      const saved20 = totalPriceNum > 0 ? money((totalPriceNum * 0.2).toFixed(2)) : '';
      goalHeadline = `Sem esta compra, a meta ficaria para ${baseline}.`;
      goalDetail = `Com a compra, não há data dentro de dez anos. Como a compra e os aportes disputam o mesmo dinheiro, retirar ${money(result.price)} empurra a conquista para além do limite de 10 anos${saved20 ? ` (a opção 20% mais barata devolveria ${saved20} de fôlego para a meta)` : ''}.`;
    } else if (bought) {
      goalHeadline = `Com a compra, a meta ficaria para ${bought}.`;
      goalDetail = 'Sem esta compra, não há data dentro de dez anos.';
    } else {
      goalHeadline = UNDATED_GOAL;
    }
  }

  const start = civilDateLabel(result.horizon?.start);
  const end = civilDateLabel(result.horizon?.end);
  const days = result.horizon?.days ?? 0;

  let cashSummary: string | null = null;
  if (cashReady && current) {
    const lowPoint = lowestClosingPoint(current.curve);
    const pieces: string[] = [];
    if (baselineScenario?.closing_balance != null && current.closing_balance != null) {
      pieces.push(`Saldo projetado ao final do período (${end}): ${money(current.closing_balance)} com a compra, frente a ${money(baselineScenario.closing_balance)} sem a compra`);
    } else if (current.closing_balance != null) {
      pieces.push(`Saldo projetado ao final do período (${end}): ${money(current.closing_balance)}`);
    }
    if (lowPoint && parseAmount(lowPoint.amount) > parseAmount(current.minimum_balance)) {
      pieces.push(`menor fechamento diário após entradas: ${money(lowPoint.amount)} em ${lowPoint.date}`);
    }
    if (pieces.length) cashSummary = pieces.join(' · ') + '.';
  }

  const parcelPhrase = count > 1 && firstInstallment ? `as ${count} parcelas de ${money(firstInstallment)}` : `o valor de ${money(result.price)}`;
  let verdict: PurchaseVerdict;
  if (!cashReady || !current) {
    verdict = {
      badge: 'ANÁLISE DE ESFORÇO E META',
      headline: `Veja o peso de ${money(result.price)} no seu tempo de trabalho e nos seus objetivos.`,
      detail: 'Informe o saldo inicial na seção 2 se quiser simular também o impacto diário no seu caixa e na reserva mínima.',
      tone: 'neutral',
    };
  } else if (current.first_reserve_breach || parseAmount(current.minimum_balance) < 0) {
    const waitFixes = waitScenario && !waitScenario.first_reserve_breach && parseAmount(waitScenario.minimum_balance) >= 0;
    const reduceFixes = reduceScenario && !reduceScenario.first_reserve_breach && parseAmount(reduceScenario.minimum_balance) >= 0;
    const recoveryHint = waitFixes
      ? ' Esperar 30 dias evita que o caixa toque na reserva.'
      : reduceFixes
        ? ' A alternativa 20% mais barata mantém a reserva protegida.'
        : ' Considere renegociar o valor, aumentar o prazo ou aguardar mais fôlego no caixa.';
    verdict = {
      badge: 'ATENÇÃO · PRESSIONA O CAIXA',
      headline: `Comprar agora leva o saldo abaixo da margem segura${current.first_reserve_breach ? ` em ${civilDateLabel(current.first_reserve_breach)}` : ''}.`,
      detail: `O menor saldo chega a ${money(current.minimum_balance)} durante o período de ${days} dias.${recoveryHint}`,
      tone: 'warning',
    };
  } else if (baseline && !bought) {
    verdict = {
      badge: 'CABE NO CAIXA · TRAVA A META',
      headline: `Seu fluxo de caixa absorve ${parcelPhrase} sem furar a reserva, mas a compra compromete o prazo da sua meta.`,
      detail: `Nenhum dia de ${start} a ${end} fica abaixo da reserva${current.closing_balance != null ? ` (fechando o período com ${money(current.closing_balance)})` : ''}. O ponto de atenção é o custo de oportunidade: a meta que seria atingida em ${baseline} sai da janela de dez anos.`,
      tone: 'caution',
    };
  } else if ((result.goal?.delay_days || 0) > 0) {
    verdict = {
      badge: 'CABE NO CAIXA · ADIA A META',
      headline: `O orçamento comporta ${parcelPhrase} com reserva protegida, em troca de ${result.goal?.delay_days} dias a mais na meta.`,
      detail: `Seu caixa permanece positivo e acima da reserva até ${end}${current.closing_balance != null ? ` (saldo final de ${money(current.closing_balance)})` : ''}, deslocando a conclusão da meta de ${baseline} para ${bought}.`,
      tone: 'caution',
    };
  } else {
    verdict = {
      badge: 'CABE NA SUA VIDA · CAIXA PROTEGIDO',
      headline: `O seu fluxo no período absorve ${parcelPhrase} mantendo a reserva intacta.`,
      detail: `De ${start} a ${end}, todos os compromissos marcados e as parcelas cabem com folga no orçamento${current.closing_balance != null ? `, encerrando o período com ${money(current.closing_balance)}` : ''}.${baseline && bought ? ` Sua meta permanece em dia para ${bought}.` : ''}`,
      tone: 'positive',
    };
  }

  const notes = [
    result.work_hours ? null : BLANK_HOURS,
    result.goal ? null : BLANK_GOAL,
    cashReady ? null : BLANK_BALANCE,
  ].filter((note): note is string => Boolean(note));
  const hidden = new Set(Object.keys(KNOWN_GAPS));
  for (const gap of result.gaps || []) {
    if (hidden.has(gap)) continue;
    const sentence = purchaseGapSentence(gap);
    if (!notes.includes(sentence)) notes.push(sentence);
  }

  return {
    price: money(result.price),
    installments: installmentLabel(Number(result.installments)),
    installmentDetail,
    hours,
    hoursDetail,
    goalHeadline,
    goalDetail,
    verdict,
    cashSummary,
    window: `A análise olha de ${start} a ${end}, ${days} dias.`,
    reserve: !cashReady || !current ? null : current.first_reserve_breach
      ? `O primeiro dia abaixo da reserva é ${civilDateLabel(current.first_reserve_breach)}.`
      : 'Nenhum dia deste período fica abaixo da reserva.',
    alternatives,
    notes,
  };
}
