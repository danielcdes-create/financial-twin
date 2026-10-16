import {captureDateIso} from './captureDate';
import {requireMoneyInput} from './moneyInput';

export type ManualCapture = {kind: 'purchase'|'income'|'own_transfer'; merchant: string; amount: string; date: string; origin: string; destination: string; account: string; payment: string; installments: string; transcript: string};
export const emptyManualCapture = (): ManualCapture => ({kind:'purchase',merchant:'',amount:'',date:'',origin:'',destination:'',account:'',payment:'',installments:'',transcript:''});
export const hasManualCapture = (draft: ManualCapture) => Object.entries(draft).some(([key,value]) => key === 'kind' ? value !== 'purchase' : Boolean(value));
export function manualCapturePayload(draft: ManualCapture, accountName: (key: string) => string, now = new Date()) {
  const value = Number(requireMoneyInput(draft.amount, {allowZero:false, max:100000000}));
  if (!Number.isFinite(value) || value < 0.01 || value > 100000000) throw new Error('Informe um valor de R$ 0,01 até R$ 100.000.000,00.');
  if (draft.kind !== 'own_transfer' && !draft.merchant.trim()) throw new Error(draft.kind === 'income' ? 'Informe a origem da entrada.' : 'Informe o estabelecimento.');
  const date = draft.date.trim() ? captureDateIso(draft.date) : null;
  if (draft.date.trim() && !date) throw new Error('Informe uma data válida no formato DD/MM/AAAA.');
  if (draft.kind === 'own_transfer' && (!draft.origin || !draft.destination || draft.origin === draft.destination)) throw new Error('Escolha contas de origem e destino diferentes.');
  const installments = (draft.installments || '').trim();
  let installmentCount: number | undefined;
  if (draft.kind === 'purchase' && installments && installments !== '1') {
    if (!/^\d+$/.test(installments) || Number(installments) < 2 || Number(installments) > 48) throw new Error('Informe de 2 a 48 parcelas, ou deixe em branco para compra à vista.');
    installmentCount = Number(installments);
  }
  return {amount:value,currency:'BRL',merchant_raw:draft.kind === 'own_transfer' ? `${accountName(draft.origin)} → ${accountName(draft.destination)}` : draft.merchant.trim(),
    event_timestamp:date ? `${date}T00:00:00-03:00` : now.toISOString(),event_type:draft.kind === 'own_transfer' ? 'own_transfer' : draft.kind === 'income' ? 'manual_income' : 'manual_purchase',
    direction:draft.kind === 'income' ? 'income' : 'expense',
    ...(installmentCount ? {installment_count:installmentCount} : {}),
    ...(draft.kind === 'own_transfer' ? {from_account_key:draft.origin,to_account_key:draft.destination} : {...(draft.account ? {account_profile_id:draft.account} : {}),...(draft.payment ? {payment_method:draft.payment} : {})})};
}
