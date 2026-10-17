export type PurchaseCardFacts = {
  price: string;
  workHours: string | null;
  delayDays: number | null;
  installments?: number | null;
  installmentAmount?: string | null;
  goalUnreachedWithPurchase?: boolean;
};

function formatCardAmount(value: string): string {
  const clean = String(value || '').trim().replace(',', '.');
  const num = Number(clean);
  if (!Number.isFinite(num)) return String(value || '').replace('.', ',');
  const [whole, cents] = Math.abs(num).toFixed(2).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${num < 0 ? '-' : ''}${grouped},${cents}`;
}

export function purchaseCardLines(facts: PurchaseCardFacts): string[] {
  const count = Number(facts.installments || 0);
  const parcelBit = count > 1 && facts.installmentAmount
    ? ` (${count}x de R$ ${formatCardAmount(facts.installmentAmount)})`
    : count > 1
      ? ` (${count}x)`
      : '';
  const lines = [`Uma compra de R$ ${formatCardAmount(facts.price)}${parcelBit}`];
  if (facts.workHours !== null) {
    const hoursNum = Number(String(facts.workHours).replace(',', '.'));
    const workDays = Number.isFinite(hoursNum) && hoursNum > 0
      ? ` (~${(hoursNum / 8).toFixed(1).replace('.', ',')} dias úteis de 8h)`
      : '';
    lines.push(`${facts.workHours.replace('.', ',')} horas da minha renda de trabalho${workDays}`);
  }
  if (facts.delayDays !== null && facts.delayDays > 0) {
    lines.push(`${facts.delayDays} dias de adiamento de uma meta`);
  } else if (facts.goalUnreachedWithPurchase) {
    lines.push('Adia uma meta financeira para além de 10 anos');
  }
  lines.push('Simulação com premissas informadas · Financial Twin');
  return lines;
}
