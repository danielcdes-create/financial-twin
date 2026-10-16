import {dedupeCardLabel, displayTitle, paymentMethodLabel} from './commitmentPresentation';

/** Title is the seller or display name. The imported descriptor stays the subtitle. */
export function commitmentHeading(input: {displayName?: string | null; originalDescription?: string | null; linkedVendor?: string | null}): {title: string; subtitle: string} {
  const vendor = String(input.displayName || input.linkedVendor || '').trim();
  const raw = String(input.originalDescription || '').trim();
  const title = displayTitle(vendor || raw || 'Compromisso');
  const same = vendor && raw && vendor.localeCompare(raw, 'pt-BR', {sensitivity: 'accent'}) === 0;
  const subtitle = vendor && raw && !same ? raw : '';
  return {title, subtitle};
}

export function categoryTrail(category?: string | null, subcategory?: string | null): string {
  const group = String(category || '').trim();
  const child = String(subcategory || '').trim();
  if (group && child) return `${group} › ${child}`;
  return group || child;
}

/** Portuguese label for a stored payment code. Unknown codes stay blank so the screen never shows the raw value. */
export function paymentCodeLabel(value?: string | null): string {
  return paymentMethodLabel(value);
}

/** One payment line for Compromissos and Extrato. A card purchase names the card, not the bank account. */
export function settlementLabel(input: {paymentMethod?: string | null; accountLabel?: string | null; cardLabel?: string | null}): string {
  const method = paymentMethodLabel(input.paymentMethod);
  const card = dedupeCardLabel(input.cardLabel || '');
  const account = String(input.accountLabel || '').trim();
  const usesCard = method === 'Cartão de crédito' || method === 'Cartão de débito';
  const instrument = usesCard && card ? card : (account || card);
  return [method, instrument].filter(Boolean).join(' · ');
}

export function headingForManaged(item: any): {title: string; subtitle: string} {
  const linked = item?.linked_purchase || {};
  const raw = String(item?.original_description || linked.descricao_original || '').trim();
  const vendor = String(item?.display_name || item?.vendedor || linked.vendedor || '').trim();
  if (vendor) return commitmentHeading({displayName: vendor, originalDescription: raw || (vendor !== String(item?.name || '') ? item?.name : ''), linkedVendor: linked.vendedor});
  return commitmentHeading({displayName: item?.name, originalDescription: raw && raw !== String(item?.name || '') ? raw : ''});
}

export function headingForInstallment(item: any): {title: string; subtitle: string} {
  const linked = item?.linked_purchase || {};
  const described = String(item?.descricao_original || item?.descricao || linked.descricao_original || '').trim();
  const named = String(item?.display_name || '').trim();
  const vendor = (named && described && named.localeCompare(described, 'pt-BR', {sensitivity: 'accent'}) !== 0 ? named : '') || String(linked.vendedor || '').trim();
  return commitmentHeading({displayName: vendor, originalDescription: described, linkedVendor: linked.vendedor});
}

export function profileTrail(item: any): string {
  const linked = item?.linked_purchase || item?.profile || {};
  return categoryTrail(item?.categoria || item?.category || linked.categoria, item?.subcategoria || linked.subcategoria);
}
