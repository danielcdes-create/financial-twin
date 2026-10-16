// The input accepts Brazilian civil dates; voice extraction also supplies ISO dates.
export function captureDateIso(value: string): string | null {
  const input = value.trim();
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(input) ? input : /^\d{2}\/\d{2}\/\d{4}$/.test(input) ? input.split('/').reverse().join('-') : '';
  if (!iso) return null;
  const parsed = new Date(`${iso}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0,10) === iso ? iso : null;
}
