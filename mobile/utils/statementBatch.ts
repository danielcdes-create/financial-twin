import {captureDateIso} from './captureDate';
import {parseMoneyInput} from './moneyInput';
export type BatchRow = {row_id: number; date: string; amount: string; description: string; kind: string; bank_ref: string | null; currency?: string | null; selected: boolean; confirmed_distinct: boolean};
export function signedBatchAmount(input: string): string | null {
  return parseMoneyInput(input, {signed:true, allowZero:false, max:100_000_000});
}
export const batchDate = captureDateIso;
export function batchBody(account_ref: string, rows: BatchRow[], acknowledge_partial: boolean, acknowledge_brl = false) {
  return {account_ref, acknowledge_partial, acknowledge_brl, rows: rows.map(row => ({...row, date: batchDate(row.date), amount: signedBatchAmount(row.amount)}))};
}
export function selectNewBatchRows(rows: BatchRow[], outcomes: {row_id: number; status: string}[]): BatchRow[] {
  const fresh = new Set(outcomes.filter(row => row.status === 'new').map(row => row.row_id));
  return rows.map(row => ({...row, selected: fresh.has(row.row_id), confirmed_distinct: false}));
}
