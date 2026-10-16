export const ACCOUNT_TYPES: Record<string, string> = {
  conta_corrente: 'Corrente', poupanca: 'Poupança', pagamento: 'Pagamento',
  investimento: 'Investimento', carteira: 'Carteira', criptoativos: 'Criptoativos', outra: 'Outra',
};
export type AccountProfile = {
  key: string; instituicao: string; apelido?: string; tipo: string; moeda: string;
  agencia?: string; agencia_digito?: string; numero?: string; numero_digito?: string;
  status: string; archived?: boolean; revision: number;
  data_abertura?: string | null; data_encerramento?: string | null; observacoes?: string;
  legacy_account_id?: string | null;
};
export function accountLabel(item?: Partial<AccountProfile>, fallback = '') {
  if (!item) return fallback;
  const name = item.apelido || item.instituicao || fallback;
  const bank = item.apelido && item.apelido !== item.instituicao ? ` · ${item.instituicao}` : '';
  return `${name}${bank}${item.numero ? ` · final ${item.numero.slice(-4)}` : ''}`;
}
export const selectableAccount = (item: Partial<AccountProfile>) => !item.archived && item.status !== 'encerrada';
export function profilePayload(item: Partial<AccountProfile>) {
  return {
    apelido: item.apelido || '', instituicao: item.instituicao || '', tipo: item.tipo || 'conta_corrente',
    agencia: item.agencia || '', agencia_digito: item.agencia_digito || '',
    numero: item.numero || '', numero_digito: item.numero_digito || '',
    status: item.status || 'aberta', moeda: 'BRL', archived: !!item.archived,
    data_abertura: item.data_abertura || null, data_encerramento: item.data_encerramento || null,
    observacoes: item.observacoes || '',
  };
}
