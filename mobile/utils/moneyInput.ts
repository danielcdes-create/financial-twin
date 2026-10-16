export type MoneyInputRules = {signed?: boolean; allowZero?: boolean; max?: number};

// Validate the original grouping before normalizing. Keep cents as text until
// a caller whose API requires a number performs the final conversion.
export function parseMoneyInput(input: string, {signed = false, allowZero = true, max}: MoneyInputRules = {}): string | null {
  const raw = input.trim().replace(/^([+-]?)R\$\s*/, '$1');
  const sign = signed ? '[+-]?' : '';
  const brazilian = new RegExp(`^${sign}(?:\\d+|\\d{1,3}(?:\\.\\d{3})+)(?:,\\d{1,2})?$`);
  const decimal = new RegExp(`^${sign}\\d+\\.\\d{1,2}$`);
  if (!brazilian.test(raw) && !decimal.test(raw)) return null;
  const grouped = /^[+-]?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$/.test(raw);
  const normalized = raw.includes(',') || grouped ? raw.replace(/\./g, '').replace(',', '.') : raw;
  const number = Number(normalized);
  if (!Number.isFinite(number) || (!allowZero && number === 0) || (max != null && Math.abs(number) > max)) return null;
  const negative = normalized.startsWith('-') && number !== 0;
  const [whole, fraction = ''] = normalized.replace(/^[+-]/, '').split('.');
  return `${negative ? '-' : ''}${whole.replace(/^0+(?=\d)/, '')}.${fraction.padEnd(2, '0')}`;
}

export function requireMoneyInput(input: string, rules: MoneyInputRules = {}): string {
  const result = parseMoneyInput(input, rules);
  if (result === null) throw new Error('Informe um valor válido, como 1.234,56, com até duas casas decimais.');
  return result;
}
