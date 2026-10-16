export function formatCivilDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value;
}

export function parseCivilDate(value: string): string {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value.trim());
  if (!match) return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '';
  const day = Number(match[1]);
  const month = Number(match[2]);
  const year = Number(match[3]);
  const stamp = new Date(Date.UTC(year, month - 1, day));
  if (stamp.getUTCFullYear() !== year || stamp.getUTCMonth() !== month - 1 || stamp.getUTCDate() !== day) return '';
  return `${match[3]}-${match[2]}-${match[1]}`;
}

export function maskCivilDate(value: string): string {
  const digits = value.replace(/\D/g, '').slice(0, 8);
  return [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 8)].filter(Boolean).join('/');
}

export function displayMoney(value: string | number): string {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  const normalized = raw.includes(',') ? raw.replace(/\./g, '').replace(',', '.') : raw;
  const number = Number(normalized);
  if (!Number.isFinite(number)) return raw;
  const negative = number < 0;
  const [whole, fraction = '00'] = Math.abs(number).toFixed(2).split('.');
  return `${negative ? '-' : ''}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.')},${fraction}`;
}

export function maskMoneyInput(value: string): string {
  const digits = value.replace(/\D/g, '').slice(0, 12);
  if (!digits) return '';
  const cents = digits.padStart(3, '0');
  const whole = cents.slice(0, -2).replace(/^0+(?=\d)/, '') || '0';
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.')},${cents.slice(-2)}`;
}
