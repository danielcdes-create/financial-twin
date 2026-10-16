export type PurchaseCardFacts = { price: string; workHours: string | null; delayDays: number | null };

export function purchaseCardLines(facts: PurchaseCardFacts): string[] {
  const lines = [`Uma compra de R$ ${facts.price.replace('.', ',')}`];
  if (facts.workHours !== null) lines.push(`${facts.workHours.replace('.', ',')} horas da minha renda de trabalho`);
  if (facts.delayDays !== null && facts.delayDays > 0) lines.push(`${facts.delayDays} dias de adiamento de uma meta`);
  lines.push('Simulação com premissas informadas · Financial Twin');
  return lines;
}
