import {occurrenceState, paymentMethodLabel} from './commitmentPresentation';

export type ChargeEntry = {
  source_ref: string;
  name: string;
  category: string;
  amount: string;
  estimated: boolean;
  due_date?: string | null;
  source_type?: string;
  occurrence_number?: number | null;
  occurrence_total?: number | null;
  payment_method?: string | null;
  billing_platform?: string | null;
  card_label?: string | null;
  category_name?: string | null;
};

/** Single palette for the legend, the month list bar, and the month screen bar. */
export const CHART_TRACK = '#263D40';
export const chartSeries = [
  {key: 'installments', label: 'Parcelas', color: '#49C5AA'},
  {key: 'subscriptions', label: 'Assinaturas', color: '#6EA8FF'},
  {key: 'accounts', label: 'Contas', color: '#C9B0FF'},
  {key: 'other', label: 'Dívidas', color: '#F0B3C8'},
] as const;

const MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

export function monthLabel(month: string): string {
  const index = Number(month.slice(5)) - 1;
  return `${MONTHS[index] || month}/${month.slice(0, 4)}`;
}

export function monthHref(month: string, periodo: string): string {
  return `/compromissos/mes/${month}?periodo=${periodo}`;
}

export function periodoFromMonths(months: number | null): string {
  return months == null ? 'all' : String(months);
}

export function chargeTypeLabel(entry: {category?: string; occurrence_number?: number | null; occurrence_total?: number | null}): string {
  const number = Number(entry.occurrence_number);
  const total = Number(entry.occurrence_total);
  if (entry.category === 'installments' && Number.isFinite(number) && number > 0 && Number.isFinite(total) && total > 0) {
    return `Parcela ${number}/${total}`;
  }
  if (entry.category === 'installments') return 'Parcela';
  if (entry.category === 'subscriptions') return 'Assinatura';
  if (entry.category === 'accounts') return 'Conta';
  if (entry.category === 'other') return 'Dívida';
  return '';
}

export function chargeDueLabel(due?: string | null): string {
  const day = String(due || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return '';
  return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

/** One payment form. A stored card or platform wins over the method code. */
export function chargePaymentLabel(entry: {payment_method?: string | null; billing_platform?: string | null; card_label?: string | null}): string {
  return entry.card_label || entry.billing_platform || paymentMethodLabel(entry.payment_method) || '';
}

export function chargeMetaLine(entry: ChargeEntry): string {
  return [chargeDueLabel(entry.due_date), chargeTypeLabel(entry), chargePaymentLabel(entry)].filter(Boolean).join(' · ');
}

export function sortCharges<T extends {due_date?: string | null; name?: string}>(items: T[]): T[] {
  return [...items].sort((left, right) => {
    const leftDue = left.due_date || '9999-99-99';
    const rightDue = right.due_date || '9999-99-99';
    return leftDue.localeCompare(rightDue) || String(left.name || '').localeCompare(String(right.name || ''));
  });
}

/** Linked dues never enter the month items. No as-of or no due date means no badge. */
export function chargeOverdue(entry: {due_date?: string | null}, asOf: string | null | undefined, current: boolean): boolean {
  if (!current || !asOf || !entry.due_date) return false;
  return occurrenceState(entry.due_date, false, asOf) === 'Vencido · sem vínculo';
}
