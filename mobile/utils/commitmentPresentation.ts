/** Presentation-only reading of commitments. Nothing here is written back to the ledger. */

export type OccurrenceState = 'Previsto' | 'Vencido · sem vínculo' | 'Pago vinculado' | 'Pago (assumido)';
export type CommitmentFilter = 'Tudo' | 'Assinaturas' | 'Contas' | 'Parcelas' | 'Dívidas';

let presentationClock: string | null = null;
/** Tests inject a civil date. Production leaves this unset and reads America/Sao_Paulo. */
export function setPresentationClock(value: string | null) {
  presentationClock = value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

export function civilToday(now = new Date()): string {
  if (presentationClock) return presentationClock;
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(now);
  const map = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

/** Floor for assumed-paid. The live boundary is this date or the first day of the civil month, whichever is later. The list response can pin it. */
export const DEFAULT_COMMITMENT_CUTOFF = '2026-10-01';
let cutoffOverride: string | null = null;
export function commitmentCutoff(): string {
  if (cutoffOverride) return cutoffOverride;
  const monthStart = `${civilToday().slice(0, 7)}-01`;
  return monthStart > DEFAULT_COMMITMENT_CUTOFF ? monthStart : DEFAULT_COMMITMENT_CUTOFF;
}
export function setCommitmentCutoff(value?: string | null) {
  const day = String(value || '').slice(0, 10);
  cutoffOverride = /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : null;
}

export function occurrenceState(due: string | null | undefined, linked: boolean, asOf = civilToday(), cutoff = commitmentCutoff()): OccurrenceState {
  if (linked) return 'Pago vinculado';
  const day = String(due || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return 'Previsto';
  if (day < cutoff) return 'Pago (assumido)';
  if (day < asOf) return 'Vencido · sem vínculo';
  return 'Previsto';
}

const FREQUENCY_MONTHS: Record<string, number> = {mensal: 1, trimestral: 3, semestral: 6, anual: 12};

/** One step backward. Stops being useful before the contract date; callers enforce that bound. */
export function previousDue(iso: string, frequency: string): string | null {
  const day = String(iso || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const [year, month, date] = day.split('-').map(Number);
  if (frequency === 'semanal') return addCivilDays(day, -7);
  const step = FREQUENCY_MONTHS[frequency];
  if (!step) return null;
  const cursor = new Date(Date.UTC(year, month - 1 - step, 1));
  const last = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0)).getUTCDate();
  const kept = Math.min(date, last);
  return `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, '0')}-${String(kept).padStart(2, '0')}`;
}

/** One step forward from a civil date. Display only; it does not write the agenda. */
export function advanceDue(iso: string, frequency: string): string | null {
  const day = String(iso || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const [year, month, date] = day.split('-').map(Number);
  if (frequency === 'semanal') return addCivilDays(day, 7);
  const step = FREQUENCY_MONTHS[frequency];
  if (!step) return null;
  const cursor = new Date(Date.UTC(year, month - 1 + step, 1));
  const last = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0)).getUTCDate();
  const kept = Math.min(date, last);
  return `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, '0')}-${String(kept).padStart(2, '0')}`;
}

function civilStamp(value: unknown): string {
  const day = String(value || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : '';
}

/** Same civil occurrence as backend schedule(). A short month must not stick to later charges. */
export function anchoredDue(item: any, offset: number): string | null {
  const start = civilStamp(item?.next_due_date);
  if (!start || !Number.isInteger(offset)) return null;
  const frequency = String(item?.frequency || '');
  if (frequency === 'semanal') return addCivilDays(start, offset * 7);
  const step = FREQUENCY_MONTHS[frequency];
  if (!step) return offset === 0 ? start : null;
  const [year, month, day] = start.split('-').map(Number);
  const index = year * 12 + (month - 1) + step * offset;
  const y = Math.floor(index / 12);
  const monthIndex = index - y * 12;
  const last = new Date(Date.UTC(y, monthIndex + 1, 0)).getUTCDate();
  const anchorRaw = Number(item?.schedule_anchor_day);
  const anchorDay = Number.isInteger(anchorRaw) && anchorRaw >= 1 && anchorRaw <= 31 ? anchorRaw : day;
  const kept = item?.schedule_anchor_end === true ? last : Math.min(anchorDay, last);
  return `${y}-${String(monthIndex + 1).padStart(2, '0')}-${String(kept).padStart(2, '0')}`;
}

/** Mirrors historical_occurrence_conflicts: that link blocks the whole forward agenda. */
export function scheduleConflict(item: any): boolean {
  if (!item || (item.status !== 'ativo' && item.status !== 'pausado')) return false;
  const next = civilStamp(item.next_due_date);
  if (!next) return false;
  return (item.linked_transactions || []).some((link: any) => {
    if (!link?.occurrence_key || link.settled_next !== false) return false;
    const due = civilStamp(link.due_date);
    return Boolean(due) && due >= next;
  });
}

/** Home month window. The civil day is America/Sao_Paulo, including the injected test clock. */
export function civilMonthWindow(now = new Date()): {yearStart: string; monthStart: string; monthEnd: string; year: number; month: number} {
  const iso = civilToday(now);
  const year = Number(iso.slice(0, 4));
  const month = Number(iso.slice(5, 7));
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const stamp = (y: number, m: number, d: number) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  return {yearStart: stamp(year, 1, 1), monthStart: stamp(year, month, 1), monthEnd: stamp(year, month, last), year, month};
}

export function frequencyLabel(value?: string | null): string {
  return ({semanal: 'Semanal', mensal: 'Mensal', trimestral: 'Trimestral', semestral: 'Semestral', anual: 'Anual'} as Record<string, string>)[value || ''] || '';
}

/** Automatic suggestion window, matching the server. Manual search is wider. */
export const SUGGESTION_BEFORE_DAYS = 7;
export const SUGGESTION_AFTER_DAYS = 10;

export function shiftCivilDate(iso: string, days: number): string {
  const day = String(iso || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return '';
  const [year, month, date] = day.split('-').map(Number);
  const stamp = new Date(Date.UTC(year, month - 1, date));
  stamp.setUTCDate(stamp.getUTCDate() + days);
  return stamp.toISOString().slice(0, 10);
}

export function suggestionWindow(due: string): {from: string; to: string} | null {
  const day = String(due || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  return {from: shiftCivilDate(day, -SUGGESTION_BEFORE_DAYS), to: shiftCivilDate(day, SUGGESTION_AFTER_DAYS)};
}

/** Difference in words. The sign says whether the statement is above or below the charge. */
export function amountWords(expected: number, valor: number, money: (value: number) => string): string {
  const actual = Math.abs(Number(valor));
  const delta = actual - Number(expected);
  if (!Number.isFinite(expected) || !Number.isFinite(actual)) return '';
  if (Math.abs(delta) < 0.005) return 'valor igual';
  return delta > 0 ? `${money(delta)} a mais` : `${money(Math.abs(delta))} a menos`;
}

export function kindLabel(source: 'managed' | 'installment' | 'obligation', payload: any): string {
  if (payload?.kind === 'obrigacao') return 'Obrigação pontual';
  const filter = decisionFilter(source, payload);
  return ({Assinaturas: 'Assinatura', Contas: 'Conta', Parcelas: 'Parcela', Dívidas: 'Dívida'} as Record<string, string>)[filter] || filter;
}

export function renewalLabel(value?: string | null): string {
  return ({automatica: 'Automática', manual: 'Manual', nao_renovar: 'Não renovar'} as Record<string, string>)[value || ''] || '';
}

/** Share of an obligation total. The stored amount is the total; leftover cents stay on the last parcel. */
export function obligationShare(total: number, count: number, number: number): number {
  const parcels = Math.trunc(Number(count) || 0);
  const cents = Math.round(Number(total) * 100);
  if (parcels < 2) return cents / 100;
  const base = Math.floor(cents / parcels);
  const index = Math.trunc(Number(number) || 0);
  return (index === parcels ? cents - base * (parcels - 1) : base) / 100;
}

/** Next charge that is still open. Assumed and linked dates stay on the stored agenda. */
/** Open charges of the current month, plus earlier ones that are already overdue and not assumed. */
export function reconcileCharge(due: string, state: OccurrenceState, asOf = civilToday(), cutoff = commitmentCutoff()): boolean {
  if (state === 'Pago vinculado' || state === 'Pago (assumido)') return false;
  const day = String(due || '').slice(0, 10);
  const month = /^\d{4}-\d{2}/.test(day) ? day.slice(0, 7) : '';
  if (!month || month < cutoff.slice(0, 7)) return false;
  if (month === asOf.slice(0, 7)) return true;
  return state === 'Vencido · sem vínculo';
}

export function nextOpenDue(item: any, asOf = civilToday()): {due: string; state: OccurrenceState; steps: number} | null {
  if (scheduleConflict(item)) return null;
  if (item?.status && item.status !== 'ativo') return null;
  const links = new Set((item?.linked_transactions || []).map((link: any) => civilStamp(link.due_date || link.occurrence_due_date)).filter(Boolean));
  const end = civilStamp(item?.end_date);
  const cut = civilStamp(item?.future_cutoff);
  for (let steps = 0; steps < 120; steps += 1) {
    const due = anchoredDue(item, steps);
    if (!due) return null;
    if (end && due > end) return null;
    if (cut && !(due < cut)) return null;
    const installment = Number(item.next_installment_number || 0) + steps;
    const counted = item.kind === 'parcelamento' || (item.kind === 'obrigacao' && item.installment_count);
    if (counted && item.installment_count && installment > Number(item.installment_count)) return null;
    if (item.kind === 'obrigacao' && !item.installment_count && steps > 0) return null;
    if (item.renewal_status === 'nao_renovar' && item.remaining_charges != null && steps >= Number(item.remaining_charges)) return null;
    if (item.renewal_status === 'manual' && steps > 0) return null;
    const state = occurrenceState(due, links.has(due), asOf);
    if (state !== 'Pago (assumido)' && state !== 'Pago vinculado') return {due, state, steps};
  }
  return null;
}

export function seriesKey(item: {series_key?: string; external_key?: string; tipo?: string; origem_id?: string; descricao?: string; id?: string}): string {
  const explicit = String(item.series_key || '').trim();
  if (explicit) return explicit;
  const key = String(item.external_key || '').trim();
  if (key) {
    const parts = key.split('|');
    const last = parts[parts.length - 1] || '';
    const month = last.length === 7 && last[4] === '-' && /^\d{4}$/.test(last.slice(0, 4)) && /^\d{2}$/.test(last.slice(5));
    if ((last.startsWith('parcela-') && /^\d+$/.test(last.slice(8))) || month) parts.pop();
    if (parts.length) return parts.join('|');
  }
  if (item.tipo || item.origem_id || item.descricao) {
    return `obligation|${item.tipo || 'obrigacao'}|${item.origem_id || ''}|${String(item.descricao || '').trim()}`;
  }
  return `obligation|${item.id || 'avulsa'}`;
}

const PAYMENT_LABELS: Record<string, string> = {
  cartao: 'Cartão de crédito',
  cartao_credito: 'Cartão de crédito',
  debito: 'Débito',
  cartao_debito: 'Cartão de débito',
  pix: 'Pix',
  boleto: 'Boleto',
  paypal: 'PayPal',
  outro: 'Outro',
  dinheiro: 'Dinheiro',
  transferencia: 'Transferência',
  conta_corrente: 'Conta corrente',
  sem_parar: 'Sem Parar',
};

/** One Portuguese label for a stored payment code. Unknown codes stay blank. */
export function paymentMethodLabel(value?: string | null): string {
  return PAYMENT_LABELS[String(value || '')] || '';
}

export function countPhrase(count: number, one: string, many: string): string {
  const amount = Math.abs(Math.trunc(Number(count) || 0));
  return amount === 1 ? `1 ${one}` : `${amount} ${many}`;
}

export function installmentCountLabel(count: number): string {
  return countPhrase(count, 'parcela', 'parcelas');
}

export function paymentLinksLabel(count: number): string {
  return count > 0 ? countPhrase(count, 'pagamento vinculado', 'pagamentos vinculados') : '';
}

/** Display-only spelling. Stored merchant and category text stay unchanged. */
export function polishCopy(value?: string | null): string {
  return String(value || '')
    .replace(/\bServicos essenciais\b/gi, 'Serviços essenciais')
    .replace(/\bItau\b/g, 'Itaú');
}

export function dedupeCardLabel(label?: string | null): string {
  return polishCopy(String(label || '').replace(/(\d{4})\s*[•*]{2,}\s*\1/g, '•••• $1'));
}

export function displayTitle(name: string): string {
  const text = polishCopy(String(name || '').trim());
  if (text === text.toLowerCase() && /[a-zà-ÿ]/.test(text)) return text.charAt(0).toUpperCase() + text.slice(1);
  return text;
}

export const LINK_WINDOW_DAYS = 7;

export function addCivilDays(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
}

/** The app only knows there is no linked payment. Due today or within the existing 7-day window counts. */
export function needsLinkAction(due: string | null | undefined, linked: boolean, asOf = civilToday(), cutoff = commitmentCutoff()): boolean {
  if (linked) return false;
  const day = String(due || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day < cutoff) return false;
  if (day < asOf) return true;
  return day <= addCivilDays(asOf, LINK_WINDOW_DAYS);
}

export function closureWarning(kind: 'assinatura' | 'financiamento' | 'parcela'): string {
  if (kind === 'assinatura') return 'Encerrar aqui é registro e planejamento; não cancela o serviço no fornecedor.';
  return 'Isso não quita nem altera o contrato; só muda o planejamento no app.';
}

/** A long series or a large predicted sum asks for a second explicit step. Display only. */
export function reinforcesClosure(count: number, total: number): boolean {
  return count > 12 || Math.abs(Number(total) || 0) >= 5000;
}

export function inactiveLine(status: string, when?: string | null): string {
  const label = status === 'cancelado' ? 'cancelado' : 'concluído';
  const day = String(when || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? `${label} em ${day.split('-').reverse().join('/')}` : label;
}

export function moneyScope(value: number): string {
  const cents = Math.round(Number(value) * 100);
  const sign = cents < 0 ? '-' : '';
  const absolute = Math.abs(cents);
  const whole = String(Math.floor(absolute / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `R$ ${sign}${whole},${String(absolute % 100).padStart(2, '0')}`;
}

export function sumAmounts(values: Array<number | string>): number {
  const cents = values.reduce((total: number, value) => total + Math.round(Number(value) * 100), 0);
  return cents / 100;
}

export type PendingKind = 'vencida' | 'a_vencer';
export type PendingOccurrence = {key: string; due: string; amount: number; kind: PendingKind; estimated: boolean; state: OccurrenceState};

export function pendingKindOf(due: string, asOf: string): PendingKind {
  const day = String(due || '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return day < asOf.slice(0, 10) ? 'vencida' : 'a_vencer';
  if (/^\d{4}-\d{2}$/.test(day)) return day < asOf.slice(0, 7) ? 'vencida' : 'a_vencer';
  return 'a_vencer';
}

/** Automatic renewal without an end date. The restante is a dash, never a projected sum. */
export function isOpenEnded(item: any): boolean {
  if (!item || (item.status && item.status !== 'ativo') || item.end_date) return false;
  if (item.kind === 'parcelamento' || item.kind === 'obrigacao') return false;
  return item.renewal_status === 'automatica';
}

export function chargeSuffix(item: any, source: 'managed' | 'installment' | 'obligation' = 'managed'): string {
  if (source !== 'managed') return '/mês';
  const parcels = Number(item?.installment_count || 0);
  if (item?.kind === 'obrigacao' && item?.obligation_plan !== 'parcelado' && parcels < 2) return 'à vista';
  return ({semanal: '/semana', mensal: '/mês', trimestral: '/trim.', semestral: '/semestre', anual: '/ano'} as Record<string, string>)[item?.frequency || ''] || '/mês';
}

export function cardTypeLabel(source: 'managed' | 'installment' | 'obligation', payload: any): string {
  const filter = decisionFilter(source, payload);
  return ({Assinaturas: 'Assinatura', Contas: 'Conta', Parcelas: 'Parcela', Dívidas: 'Dívida'} as Record<string, string>)[filter] || filter;
}

export function fullDateLabel(due: string): string {
  const day = String(due || '').slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return `${day.slice(8, 10)}/${day.slice(5, 7)}/${day.slice(0, 4)}`;
  if (/^\d{4}-\d{2}$/.test(String(due || ''))) return `${String(due).slice(5, 7)}/${String(due).slice(0, 4)}`;
  return '';
}

export function monthStamp(due: string): string {
  const day = String(due || '');
  if (/^\d{4}-\d{2}/.test(day)) return `${day.slice(5, 7)}/${day.slice(0, 4)}`;
  return '';
}

export function shortDueLabel(due: string): string {
  const day = String(due || '').slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
  if (/^\d{4}-\d{2}$/.test(day)) return `${day.slice(5, 7)}/${day.slice(0, 4)}`;
  return '';
}

function linkOccurrenceDue(link: any): string {
  return civilStamp(link?.due_date || link?.occurrence_due_date || link?.base_due_date);
}

function linkedDueSet(item: any): Set<string> {
  const found = new Set<string>();
  for (const link of item?.linked_transactions || []) {
    if (link?.settled_next === false && link?.occurrence_key) continue;
    for (const key of ['due_date', 'occurrence_due_date', 'base_due_date']) {
      const stamp = civilStamp(link?.[key]);
      if (stamp) found.add(stamp);
    }
  }
  return found;
}

function managedInstallmentOverride(item: any, baseDue: string): {amount?: number; due?: string; cancelled: boolean} | null {
  const patch = item?.installment_overrides?.[baseDue];
  if (!patch || typeof patch !== 'object') return null;
  const amount = patch.amount == null || patch.amount === '' ? undefined : Number(patch.amount);
  const due = patch.due_date ? String(patch.due_date).slice(0, 10) : undefined;
  return {amount: amount != null && Number.isFinite(amount) ? amount : undefined, due: /^\d{4}-\d{2}-\d{2}$/.test(due || '') ? due : undefined, cancelled: Boolean(patch.cancelled)};
}

function managedCharge(item: any, steps: number): number {
  const total = Number(item.amount || 0);
  const count = Number(item.installment_count || 0);
  if (item.kind === 'obrigacao' && count >= 2) return obligationShare(total, count, Number(item.next_installment_number || 1) + steps);
  return total;
}

export function installmentState(part: any, asOf = civilToday(), cutoff = commitmentCutoff()): OccurrenceState {
  if (part?.payment_link || part?.status === 'paga' || part?.transaction_id || part?.bill_id || (part?.status && part.status !== 'projetada')) return 'Pago vinculado';
  const due = part?.data_vencimento ? String(part.data_vencimento).slice(0, 10) : '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(due)) return occurrenceState(due, false, asOf, cutoff);
  const month = String(part?.competencia || '').slice(0, 7);
  if (/^\d{4}-\d{2}$/.test(month) && month < cutoff.slice(0, 7)) return 'Pago (assumido)';
  return 'Previsto';
}

/** Every pendente of one commitment. Open-ended series stop after a display window; their restante is not this list's sum. */
export function pendingOccurrences(source: 'managed' | 'installment' | 'obligation', payload: any, asOf = civilToday(), cutoff = commitmentCutoff()): PendingOccurrence[] {
  if (source === 'installment') {
    return (payload?.installments || []).map((part: any, index: number) => {
      const due = part.data_vencimento ? String(part.data_vencimento).slice(0, 10) : String(part.competencia || '').slice(0, 7);
      const state = installmentState(part, asOf, cutoff);
      if (state !== 'Previsto' && state !== 'Vencido · sem vínculo') return null;
      const amount = Number(part.valor || 0);
      if (!(amount > 0)) return null;
      return {key: `${index}:${due}`, due, amount, kind: pendingKindOf(due, asOf), estimated: false, state};
    }).filter(Boolean).sort((left: PendingOccurrence, right: PendingOccurrence) => left.due.localeCompare(right.due) || left.key.localeCompare(right.key));
  }
  if (source === 'obligation') {
    return (payload?.items || []).map((part: any) => {
      const due = String(part.due_date || '').slice(0, 10);
      const state = occurrenceState(due, Boolean(part.payment_link), asOf, cutoff);
      if (state !== 'Previsto' && state !== 'Vencido · sem vínculo') return null;
      const amount = Number(part.amount || 0);
      if (!(amount > 0)) return null;
      return {key: String(part.id || due), due, amount, kind: pendingKindOf(due, asOf), estimated: false, state};
    }).filter(Boolean).sort((left: PendingOccurrence, right: PendingOccurrence) => left.due.localeCompare(right.due) || left.key.localeCompare(right.key));
  }
  if (scheduleConflict(payload)) return [];
  if (payload?.status && payload.status !== 'ativo') return [];
  if (!anchoredDue(payload, 0)) return [];
  const links = linkedDueSet(payload);
  const end = civilStamp(payload.end_date);
  const cut = civilStamp(payload.future_cutoff);
  const endless = isOpenEnded(payload);
  const estimated = Boolean(payload.amount_estimate || payload.amount_mode === 'last_three_bills');
  const cap = endless ? 12 : 600;
  const rows: PendingOccurrence[] = [];
  for (let steps = 0; steps < cap; steps += 1) {
    const due = anchoredDue(payload, steps);
    if (!due) break;
    const installment = Number(payload.next_installment_number || 0) + steps;
    const counted = payload.kind === 'parcelamento' || (payload.kind === 'obrigacao' && payload.installment_count);
    if (counted && payload.installment_count && installment > Number(payload.installment_count)) break;
    if (payload.kind === 'obrigacao' && !(Number(payload.installment_count) >= 2) && steps > 0) break;
    if (payload.renewal_status === 'nao_renovar' && payload.remaining_charges != null && steps >= Number(payload.remaining_charges)) break;
    if (payload.renewal_status === 'manual' && steps > 0) break;
    if (end && due > end) break;
    if (cut && !(due < cut)) break;
    const patch = managedInstallmentOverride(payload, due);
    if (patch?.cancelled) continue;
    const shownDue = patch?.due || due;
    const state = occurrenceState(shownDue, links.has(due) || links.has(shownDue), asOf, cutoff);
    if (state === 'Previsto' || state === 'Vencido · sem vínculo') {
      const amount = patch?.amount != null ? patch.amount : managedCharge(payload, steps);
      rows.push({key: due, due: shownDue, amount, kind: pendingKindOf(shownDue, asOf), estimated, state});
    }
  }
  return rows;
}

export type CommitmentFigures = {
  pending: PendingOccurrence[];
  endless: boolean;
  estimated: boolean;
  suffix: string;
  chargeAmount: number;
  chargeMuted: boolean;
  sum: number;
  count: number;
  lastDue: string;
};

export function commitmentFigures(source: 'managed' | 'installment' | 'obligation', payload: any, asOf = civilToday()): CommitmentFigures {
  const pending = pendingOccurrences(source, payload, asOf);
  const endless = source === 'managed' && isOpenEnded(payload);
  const estimated = pending.some(row => row.estimated) || (source === 'managed' && Boolean(payload?.amount_estimate || payload?.amount_mode === 'last_three_bills'));
  const oldest = pending[0];
  const settled = source === 'installment' ? (payload?.installments || []).filter((part: any) => part.status !== 'projetada') : [];
  const fallback = source === 'managed' ? Number(payload?.amount || 0) : source === 'obligation' ? Number(payload?.items?.[payload.items.length - 1]?.amount || 0) : (settled.length ? Number(settled[settled.length - 1].valor || 0) : 0);
  return {
    pending,
    endless,
    estimated: Boolean(oldest ? oldest.estimated : estimated),
    suffix: chargeSuffix(payload, source),
    chargeAmount: oldest ? oldest.amount : fallback,
    chargeMuted: !oldest,
    sum: endless ? 0 : sumAmounts(pending.map(row => row.amount)),
    count: endless ? 0 : pending.length,
    lastDue: pending.length ? pending[pending.length - 1].due : '',
  };
}

export function formatCharge(amount: number, suffix: string, estimated: boolean, money: (value: number) => string): string {
  return `${estimated ? '~ ' : ''}${money(amount)} ${suffix}`.replace(/\s+/g, ' ').trim();
}

export function restanteCopy(figures: CommitmentFigures, money: (value: number) => string): {amountPiece: string; restPiece: string; detail: string; termina: string; line: string} {
  if (figures.endless) {
    return {amountPiece: 'Restante —', restPiece: ' · sem data final', detail: '—', termina: '— (sem data final)', line: 'Restante — · sem data final'};
  }
  const marked = figures.estimated && figures.count > 0;
  const amount = `${marked ? '~ ' : ''}${money(figures.sum)}`;
  const count = installmentCountLabel(figures.count);
  const until = monthStamp(figures.lastDue);
  const restPiece = until ? ` · ${count} · até ${until}` : ` · ${count}`;
  return {amountPiece: `Restante ${amount}`, restPiece, detail: `${amount} · ${count}`, termina: until || '—', line: `Restante ${amount}${restPiece}`};
}

export function cardLine2(source: 'managed' | 'installment' | 'obligation', payload: any, figures: CommitmentFigures, payment = ''): string {
  const type = cardTypeLabel(source, payload);
  const date = figures.pending[0] ? fullDateLabel(figures.pending[0].due) : '';
  return [type, date, payment].filter(Boolean).join(' · ');
}

export function reconcileMeta(amount: number, estimated: boolean, due: string, state: OccurrenceState, money: (value: number) => string): string {
  const value = estimated ? `~ ${money(amount)}` : money(amount);
  const verb = state === 'Vencido · sem vínculo' ? 'venceu' : 'vence';
  return `${value} · ${verb} ${shortDueLabel(due)}`;
}

export function futureRemovalLabel(count: number, name: string, total: number): string {
  const noun = count === 1 ? '1 parcela futura' : `${count} parcelas futuras`;
  return `Remover ${noun} de ${name} — ${moneyScope(total)}`;
}

export function amountText(money: (value: number) => string, value: number, estimated: boolean): string {
  const formatted = money(Number(value) || 0);
  return estimated ? `~ ${formatted} · Estimado` : formatted;
}

function seriesAmount(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2);
}

/** Variable bills use the same Conta bucket as the list. The month total stays put. */
export function alignProjectionSeries<T extends {subscriptions?: string; accounts?: string; items?: Array<{category?: string; estimated?: boolean; amount?: string}>}>(months: T[]): T[] {
  return months.map(row => {
    let moved = 0;
    const items = (row.items || []).map(entry => {
      if (entry.estimated && entry.category === 'subscriptions') {
        moved += Number(entry.amount || 0);
        return {...entry, category: 'accounts'};
      }
      return entry;
    });
    if (!moved && row.accounts != null) return {...row, items};
    return {...row, items, subscriptions: seriesAmount(Number(row.subscriptions || 0) - moved), accounts: seriesAmount(Number(row.accounts || 0) + moved)};
  });
}

export function projectionFigures(data: {items: Array<{total?: string; installments?: string; subscriptions?: string; other?: string; bill_balance?: string}>; summary?: {this_month?: string | number | null; overdue?: string | number | null; upcoming?: string | number | null; next_12?: string | number | null; installments?: string | number | null; subscriptions?: string | number | null; other?: string | number | null; bill_balance?: string | number | null; window_months?: string | number | null} | null}) {
  const summary = data.summary;
  if (summary && summary.this_month != null) {
    return {
      this_month: String(summary.this_month),
      this_month_overdue: summary.overdue == null ? null : String(summary.overdue),
      this_month_upcoming: summary.upcoming == null ? null : String(summary.upcoming),
      next_12: summary.next_12 == null ? null : String(summary.next_12),
      installments: String(summary.installments ?? '0'),
      subscriptions: String(summary.subscriptions ?? '0'),
      other: String(summary.other ?? '0'),
      bill_balance: String(summary.bill_balance ?? '0'),
      window_months: Number(summary.window_months ?? Math.min(12, data.items.length)),
    };
  }
  const window = data.items.slice(0, 12);
  const sum = (key: 'total' | 'installments' | 'subscriptions' | 'other' | 'bill_balance') => window.reduce((total, row) => total + Number(row[key] || 0), 0);
  return {
    this_month: data.items[0]?.total ?? '0',
    this_month_overdue: null,
    this_month_upcoming: null,
    next_12: data.items.length >= 12 ? String(sum('total')) : null,
    installments: String(sum('installments')),
    subscriptions: String(sum('subscriptions')),
    other: String(sum('other')),
    bill_balance: String(sum('bill_balance')),
    window_months: window.length,
  };
}

/** Small categories stay visible beside a dominant series. Shares are display-only. */
export function readableShares(parts: Array<{key: string; amount: number}>): Array<{key: string; share: number}> {
  const positive = parts.filter(part => part.amount > 0);
  if (!positive.length) return parts.map(part => ({key: part.key, share: 0}));
  const minimum = 0.08;
  const total = positive.reduce((sum, part) => sum + part.amount, 0);
  let shares = positive.map(part => ({key: part.key, share: part.amount / total}));
  const small = shares.filter(part => part.share < minimum);
  if (small.length && small.length < shares.length) {
    const reserved = small.length * minimum;
    const bigTotal = shares.filter(part => part.share >= minimum).reduce((sum, part) => sum + part.share, 0);
    shares = shares.map(part => part.share < minimum ? {key: part.key, share: minimum} : {key: part.key, share: (part.share / bigTotal) * (1 - reserved)});
  }
  const byKey = new Map(shares.map(part => [part.key, part.share]));
  return parts.map(part => ({key: part.key, share: byKey.get(part.key) || 0}));
}

export type ObligationSeries = {
  series_key: string;
  items: any[];
  title: string;
  subtitle: string;
  nextDue: string;
  lastDue: string;
  total: number;
  tipo: string;
  linked_purchase?: any;
  categoria?: string;
  subcategoria?: string;
  natureza?: string;
  plataforma?: string;
  metodo_pagamento?: string;
  payment_method?: string;
  account_id?: string;
  card_id?: string;
  card_label?: string;
};

function seriesTitle(items: any[]): string {
  const tipo = String(items[0]?.tipo || '');
  const described = items.some(item => String(item.raw_name || item.descricao || '').trim());
  if (tipo && !described) return displayTitle(tipo);
  const raw = String(items[0]?.raw_name || items[0]?.descricao || '').trim();
  if (raw) return displayTitle(raw);
  const name = String(items[0]?.name || '').trim();
  return displayTitle(name.replace(/\s*[·•]\s*\d{4}-\d{2}$/, '') || name || 'Obrigação');
}

export function groupObligationSeries(items: any[]): ObligationSeries[] {
  const groups = new Map<string, any[]>();
  for (const item of items) {
    const key = seriesKey(item);
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  return [...groups.entries()].map(([key, rows]) => {
    const sorted = [...rows].sort((left, right) => String(left.due_date).localeCompare(String(right.due_date)) || String(left.id).localeCompare(String(right.id)));
    const raw = String(sorted[0]?.raw_name || sorted[0]?.descricao || '').trim();
    const vendor = sorted.map(row => String(row.display_name || row.linked_purchase?.vendedor || '').trim()).find(name => name && name.localeCompare(raw, 'pt-BR', {sensitivity: 'accent'}) !== 0) || '';
    const titled = seriesTitle(sorted);
    const title = vendor ? displayTitle(vendor) : titled;
    const subtitle = vendor ? (raw || (titled !== title ? titled : '')) : (raw && raw !== titled && raw.toLowerCase() !== titled.toLowerCase() ? raw : '');
    const sample = sorted.find(row => row.linked_purchase || row.categoria || row.category || row.natureza || row.plataforma || row.metodo_pagamento || row.payment_method || row.account_id || row.card_id || row.card_label) || sorted[0];
    return {
      series_key: key,
      items: sorted,
      title,
      subtitle,
      nextDue: String(sorted[0]?.due_date || ''),
      lastDue: String(sorted[sorted.length - 1]?.due_date || ''),
      total: sumAmounts(sorted.map(row => row.amount || 0)),
      tipo: String(sorted[0]?.tipo || ''),
      linked_purchase: sample?.linked_purchase || null,
      categoria: sample?.categoria || sample?.category || sample?.linked_purchase?.categoria || '',
      subcategoria: sample?.subcategoria || sample?.linked_purchase?.subcategoria || '',
      ...(sample?.natureza || sample?.linked_purchase?.natureza ? {natureza: sample?.natureza || sample?.linked_purchase?.natureza} : {}),
      ...(sample?.plataforma || sample?.linked_purchase?.plataforma ? {plataforma: sample?.plataforma || sample?.linked_purchase?.plataforma} : {}),
      ...(sample?.metodo_pagamento || sample?.linked_purchase?.metodo_pagamento ? {metodo_pagamento: sample?.metodo_pagamento || sample?.linked_purchase?.metodo_pagamento} : {}),
      ...(sample?.payment_method ? {payment_method: sample.payment_method} : {}),
      ...(sample?.account_id || sample?.linked_purchase?.account_id ? {account_id: sample?.account_id || sample?.linked_purchase?.account_id} : {}),
      ...(sample?.card_id || sample?.linked_purchase?.card_id ? {card_id: sample?.card_id || sample?.linked_purchase?.card_id} : {}),
      ...(sample?.card_label || sample?.linked_purchase?.card_label || sample?.linked_purchase?.cartao_label ? {card_label: sample?.card_label || sample?.linked_purchase?.card_label || sample?.linked_purchase?.cartao_label} : {}),
    };
  });
}

export function decisionFilter(source: 'managed' | 'installment' | 'obligation', payload: any): Exclude<CommitmentFilter, 'Tudo'> {
  if (source === 'installment' || payload?.kind === 'parcelamento') return 'Parcelas';
  const tipo = String(payload?.tipo || payload?.items?.[0]?.tipo || '').toLowerCase();
  if (tipo === 'parcelamento') return 'Parcelas';
  if (payload?.kind === 'obrigacao' || source === 'obligation' || tipo === 'financiamento') return 'Dívidas';
  if (payload?.amount_mode === 'last_three_bills' || payload?.amount_estimate) return 'Contas';
  return 'Assinaturas';
}

export type SettledRow = {
  key: string;
  due: string;
  amount: number;
  state: 'Pago (assumido)' | 'Pago vinculado' | 'Pago';
  label: string;
  paidDate: string;
  identity?: string;
  source?: 'installment' | 'obligation';
  revision?: number;
  paymentLink?: any;
};

function finiteAmount(value: unknown): number | null {
  if (value == null || value === '') return null;
  const amount = Number(value);
  return Number.isFinite(amount) ? amount : null;
}

function obligationChargeAmount(item: any, number: number): number {
  const parcels = Number(item?.installment_count || 0) >= 2 ? Number(item.installment_count) : 1;
  const total = Number(item?.amount || 0);
  return parcels > 1 ? obligationShare(total, parcels, number) : total;
}

/** Paid rows for Detalhe and the schedule. The same charge keeps the same cent and state. */
export function settledRows(source: 'managed' | 'installment' | 'obligation', payload: any, asOf = civilToday(), cutoff = commitmentCutoff()): SettledRow[] {
  if (source === 'installment') {
    return (payload?.installments || []).flatMap((part: any, index: number) => {
      const due = part?.data_vencimento ? String(part.data_vencimento).slice(0, 10) : '';
      if (part?.payment_link) {
        const paid = String(part.payment_link.paid_date || due).slice(0, 10);
        return [{
          key: `installment:${payload?.id || index}:link:${index}`,
          due: paid,
          amount: finiteAmount(part.payment_link.actual_amount) ?? Number(part.valor || 0),
          state: 'Pago vinculado' as const,
          label: 'extrato',
          paidDate: paid,
          identity: String(payload?.id || ''),
          source: 'installment' as const,
          revision: Number(payload?.link_revision || 0),
          paymentLink: part.payment_link,
        }];
      }
      if (part?.status && part.status !== 'projetada') {
        return [{key: `installment:${payload?.id || index}:paid:${index}`, due, amount: Number(part.valor || 0), state: 'Pago' as const, label: 'Pago', paidDate: due}];
      }
      return [];
    });
  }
  if (source === 'obligation') {
    return (payload?.items || []).flatMap((part: any) => {
      const due = String(part?.due_date || '').slice(0, 10);
      if (part?.payment_link) {
        const paid = String(part.payment_link.paid_date || due).slice(0, 10);
        return [{
          key: String(part.id || due),
          due: paid,
          amount: finiteAmount(part.payment_link.actual_amount) ?? Number(part.amount || 0),
          state: 'Pago vinculado' as const,
          label: 'extrato',
          paidDate: paid,
          identity: String(part.id || ''),
          source: 'obligation' as const,
          revision: Number(part.link_revision || payload?.link_revision || 0),
          paymentLink: part.payment_link,
        }];
      }
      if (occurrenceState(due, false, asOf, cutoff) === 'Pago (assumido)') {
        return [{key: String(part.id || due), due, amount: Number(part.amount || 0), state: 'Pago (assumido)' as const, label: 'Pago (assumido)', paidDate: due}];
      }
      return [];
    });
  }
  const item = payload || {};
  const rows: SettledRow[] = [];
  const seen = new Set<string>();
  const links = item.linked_transactions || [];
  const pushLink = (link: any, fallback: number) => {
    const due = linkOccurrenceDue(link);
    if (due) seen.add(due);
    const paid = civilStamp(link?.paid_date) || due;
    rows.push({
      key: String(link?.transaction_id || link?.provisional_id || due || paid),
      due: paid,
      amount: finiteAmount(link?.actual_amount) ?? fallback,
      state: 'Pago vinculado',
      label: 'extrato',
      paidDate: paid,
    });
  };
  if (item.kind === 'obrigacao') {
    const parcels = Number(item.installment_count || 0) >= 2 ? Number(item.installment_count) : 1;
    const origin = 1 - Number(item.next_installment_number || 1);
    const dated = {...item, frequency: item.frequency || 'mensal'};
    const shareFor = (due: string) => {
      for (let number = 1; number <= parcels; number += 1) {
        if (anchoredDue(dated, origin + number - 1) === due) return obligationChargeAmount(item, number);
      }
      return obligationChargeAmount(item, 1);
    };
    for (const link of links) pushLink(link, shareFor(linkOccurrenceDue(link)));
    for (let number = 1; number <= parcels; number += 1) {
      const cursor = anchoredDue(dated, origin + number - 1);
      if (!cursor || seen.has(cursor)) continue;
      if (occurrenceState(cursor, false, asOf, cutoff) !== 'Pago (assumido)') continue;
      rows.push({key: cursor, due: cursor, amount: obligationChargeAmount(item, number), state: 'Pago (assumido)', label: 'Pago (assumido)', paidDate: cursor});
    }
    return rows;
  }
  for (const link of links) pushLink(link, Number(item.amount || 0));
  const start = civilStamp(item.purchase_date);
  for (let step = 0; step <= 120; step += 1) {
    const cursor = anchoredDue(item, -step);
    if (!cursor || (start && cursor < start)) break;
    if (seen.has(cursor)) continue;
    if (occurrenceState(cursor, false, asOf, cutoff) !== 'Pago (assumido)') continue;
    rows.push({key: cursor, due: cursor, amount: Number(item.amount || 0), state: 'Pago (assumido)', label: 'Pago (assumido)', paidDate: cursor});
  }
  return rows;
}

export function managedOccurrenceState(item: any, asOf = civilToday()): OccurrenceState | null {
  if (item?.status !== 'ativo' || !item?.next_due_date) return null;
  const due = String(item.next_due_date).slice(0, 10);
  const linked = (item.linked_transactions || []).some((link: any) => String(link.due_date || link.occurrence_due_date || '').slice(0, 10) === due);
  return occurrenceState(due, linked, asOf);
}
