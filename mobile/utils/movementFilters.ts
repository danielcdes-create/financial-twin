import {paymentCodeLabel, settlementLabel} from './commitmentProfile';

type MovementFilters = {
  search?: string;
  category?: string;
  account?: string;
  range?: {from: string; to: string} | null;
  spendingOnly?: boolean;
  cursor?: string | null;
};

export function movementPath(filters: MovementFilters) {
  const query = new URLSearchParams({limit: '50', cursor_mode: 'true'});
  if (filters.search?.trim()) query.set('search', filters.search.trim());
  if (filters.category) query.set('category', filters.category);
  if (filters.account) query.set('account_id', filters.account);
  if (filters.range) {
    query.set('date_from', filters.range.from);
    query.set('date_to', filters.range.to);
  }
  if (filters.spendingOnly) {
    query.set('period_basis', 'economic');
    query.set('spending_only', 'true');
  }
  if (filters.cursor) query.set('cursor', filters.cursor);
  return `/v1/movements?${query}`;
}

export const recordCategory = (item: any): string =>
  (item?.provisional_id ? item.categoria_sugerida : item?.categoria) || 'Sem categoria';

/** Own-account transfer layout applies only after both accounts were stored. */
export const ownAccountTransfer = (item: any): boolean => {
  const origin = item?.transfer?.from_account_key;
  const destination = item?.transfer?.to_account_key;
  return item?.natureza === 'TRANSFERENCIA' && !!origin && !!destination && origin !== destination;
};

const NATURE_LABELS: Record<string, string> = {
  COMPRA: 'Compras e Consumo',
  IMPOSTO_TRIBUTO: 'Impostos e Tributos',
  TARIFA_ENCARGO: 'Tarifas e Encargos',
  ESTORNO: 'Estornos e Reembolsos',
  RENDIMENTO: 'Rendimentos e Receitas',
  TRANSFERENCIA: 'Transferência Neutra',
};

export const natureLabel = (value?: string) => value ? (NATURE_LABELS[value] || value.replaceAll('_', ' ')) : '';

export const paymentLabel = (value?: string) => paymentCodeLabel(value);

/** Same settlement line Compromissos shows for this purchase. */
export function statementSettlementLabel(item: any, accountLabel = ''): string {
  return settlementLabel({
    paymentMethod: item?.metodo_pagamento || item?.payment_method,
    accountLabel: item?.conta_label || accountLabel,
    cardLabel: item?.cartao_label || item?.card_label || item?.commitment?.card_label || item?.installment_plan?.card_label,
  });
}

/** Card when the purchase is on a card; otherwise the stored account label. */
export function statementInstrument(item: any, accountLabel = ''): string {
  const full = statementSettlementLabel(item, accountLabel);
  const method = paymentLabel(item?.metodo_pagamento);
  if (method && full.startsWith(`${method} · `)) return full.slice(method.length + 3);
  return full || accountLabel;
}

/** Stored classification, in the same order the statement list shows it. */
export function classificationLine(item: any): string {
  const payment = paymentLabel(item?.metodo_pagamento);
  if (ownAccountTransfer(item)) return [natureLabel('TRANSFERENCIA'), payment].filter(Boolean).join(' · ');
  const category = recordCategory(item);
  const classified = item?.subcategoria ? `${category} › ${item.subcategoria}` : category;
  return [natureLabel(item?.natureza), classified, payment].filter(Boolean).join(' · ');
}

/** Detail of a parcelamento row: the statement line is parcel 1; this is the plan. */
export function parcelamentoPurchaseSummary(item: any): {count: number; total: number} | null {
  const marked = Boolean(item?.parcelamento_purchase || item?.commitment?.purchase || item?.parcelamento);
  const count = Number(item?.parcelamento?.count ?? item?.installment_count ?? 0);
  if (!marked || !Number.isInteger(count) || count < 2) return null;
  const stated = Number(item?.parcelamento?.total);
  if (Number.isFinite(stated) && stated > 0) return {count, total: stated};
  const raw = item?.valor;
  const value = typeof raw === 'number' ? raw : Number(raw?.$numberDecimal ?? raw);
  if (!Number.isFinite(value) || value === 0) return null;
  return {count, total: Math.abs(value) * count};
}

/** List mark uses the same plan count as the detail, not a stale installment_count on the row. */
export function parcelamentoListMark(item: any): string {
  const summary = parcelamentoPurchaseSummary(item);
  return summary ? `parcela 1/${summary.count}` : '';
}

export function categoryOptions(items: any[]): string[] {
  return [...new Set(items.map(item => typeof item === 'string' ? item : item?.name || item?.categoria)
    .filter((value): value is string => typeof value === 'string' && !!value.trim()))];
}
