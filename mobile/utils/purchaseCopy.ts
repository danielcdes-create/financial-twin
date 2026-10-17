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
type ScenarioRow = {
  name: string;
  purchase_total?: string | null;
  minimum_balance?: string | null;
  outside_horizon_total?: string | null;
  first_reserve_breach?: string | null;
};

function remainderAfterPeriod(value: string | null | undefined): number {
  if (value == null || String(value).trim() === '') return 0;
  const parsed = Number(String(value).trim().replace(/\s/g, '').replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : 0;
}

function scenarioSentence(row: ScenarioRow, money: Money): string {
  const price = `O preço é ${money(row.purchase_total)}. O menor saldo no período é ${money(row.minimum_balance)}.`;
  if (remainderAfterPeriod(row.outside_horizon_total) > 0) {
    return `${price} Depois deste período ainda há ${money(row.outside_horizon_total)} em parcelas.`;
  }
  return `${price} O período já inclui todas as parcelas desta compra.`;
}

type PurchaseGoal = {baseline_date?: string | null; purchase_date?: string | null; delay_days?: number | null};
export type PurchaseResultView = {
  price?: string | null;
  installments?: number | null;
  work_hours?: string | null;
  horizon?: {start?: string | null; end?: string | null; days?: number | null};
  goal?: PurchaseGoal | null;
  cash_scenarios?: {status?: string; scenarios?: ScenarioRow[]};
  gaps?: string[];
};
export type PurchaseCopy = {
  price: string;
  installments: string;
  hours: string | null;
  goalHeadline: string | null;
  goalDetail: string | null;
  window: string;
  reserve: string | null;
  alternatives: {title: string; body: string}[];
  notes: string[];
};

export function purchaseResultCopy(result: PurchaseResultView, money: Money, goalName = ''): PurchaseCopy {
  const scenarios = result.cash_scenarios?.scenarios || [];
  const byName = new Map(scenarios.filter(row => row.name !== 'baseline').map(row => [row.name, row]));
  const current = byName.get('buy_now');
  const cashReady = result.cash_scenarios?.status !== 'insufficient_data' && Boolean(current);
  const alternatives = ALTERNATIVES.map(item => {
    const row = byName.get(item.name);
    if (!cashReady || !row) return {title: item.title, body: UNCALCULATED_ALTERNATIVE};
    return {title: item.title, body: scenarioSentence(row, money)};
  });
  const baseline = result.goal?.baseline_date ? civilDateLabel(result.goal.baseline_date) : '';
  const bought = result.goal?.purchase_date ? civilDateLabel(result.goal.purchase_date) : '';
  let goalHeadline: string | null = null;
  let goalDetail: string | null = null;
  if (result.goal) {
    if (baseline && bought && (result.goal.delay_days || 0) > 0) {
      const name = goalName.trim();
      goalHeadline = `${result.goal.delay_days} dias de adiamento ${name ? `de ${name}` : 'da meta'}`;
      goalDetail = `Sem esta compra, a meta ficaria para ${baseline}. Com a compra, fica para ${bought}.`;
    } else if (baseline && bought) {
      goalHeadline = 'Esta compra não adia a meta.';
      goalDetail = `A meta fica para ${baseline}, com ou sem esta compra.`;
    } else if (baseline) {
      goalHeadline = `Sem esta compra, a meta ficaria para ${baseline}.`;
      goalDetail = 'Com a compra, não há data dentro de dez anos.';
    } else if (bought) {
      goalHeadline = `Com a compra, a meta ficaria para ${bought}.`;
      goalDetail = 'Sem esta compra, não há data dentro de dez anos.';
    } else goalHeadline = UNDATED_GOAL;
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
  const start = civilDateLabel(result.horizon?.start);
  const end = civilDateLabel(result.horizon?.end);
  return {
    price: money(result.price),
    installments: installmentLabel(Number(result.installments)),
    hours: result.work_hours ? `${String(result.work_hours).replace('.', ',')} horas da sua renda de trabalho` : null,
    goalHeadline,
    goalDetail,
    window: `A análise olha de ${start} a ${end}, ${result.horizon?.days ?? 0} dias.`,
    reserve: !cashReady || !current ? null : current.first_reserve_breach
      ? `O primeiro dia abaixo da reserva é ${civilDateLabel(current.first_reserve_breach)}.`
      : 'Nenhum dia deste período fica abaixo da reserva.',
    alternatives,
    notes,
  };
}
