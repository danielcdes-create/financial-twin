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

export function purchaseGapSentence(gap: string): string {
  return KNOWN_GAPS[gap] || gap.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_match, year: string, month: string, day: string) => `${day}/${month}/${year}`);
}

type Money = (value: string | null | undefined) => string;
type ScenarioRow = {
  name: string;
  purchase_total?: string | null;
  minimum_balance?: string | null;
  outside_horizon_total?: string | null;
  first_reserve_breach?: string | null;
};
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
    return {title: item.title, body: `O preço é ${money(row.purchase_total)}. O menor saldo no período é ${money(row.minimum_balance)}. Depois deste período ainda há ${money(row.outside_horizon_total)} em parcelas.`};
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
