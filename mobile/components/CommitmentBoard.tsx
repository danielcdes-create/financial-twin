import React, {useLayoutEffect, useState} from 'react';
import {ScrollView, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {financialDateLabel} from '../utils/classification';
import {creationId} from '../utils/commitmentCreate';
import {Alert} from '../utils/appAlert';
import FutureCommitmentEditor from './FutureCommitmentEditor';
import PaymentLinkScreen from './PaymentLinkScreen';
import InstallmentLabelEditor from './InstallmentLabelEditor';
import {
  amountText, cardLine2, closureWarning, commitmentFigures, decisionFilter, dedupeCardLabel, formatCharge, frequencyLabel, futureRemovalLabel, groupObligationSeries, kindLabel,
  inactiveLine, needsLinkAction, paymentMethodLabel, pendingOccurrences, reconcileCharge, reconcileMeta, reinforcesClosure, renewalLabel, restanteCopy, settledRows, sumAmounts, type CommitmentFilter, type OccurrenceState,
} from '../utils/commitmentPresentation';
import {headingForInstallment, headingForManaged, settlementLabel} from '../utils/commitmentProfile';
import {useLocalBack} from '../utils/localBack';
import {useAsyncScope} from '../utils/useAsyncScope';
import {useScheduleOverlay} from './ScheduleOverlay';
import CommitmentProfileEditor from './CommitmentProfileEditor';

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Card = {key: string; source: 'managed' | 'installment' | 'obligation'; title: string; subtitle: string; payload: any; completed: boolean};

const filters: CommitmentFilter[] = ['Tudo', 'Assinaturas', 'Contas', 'Parcelas', 'Dívidas'];
function removalPreviewPath(source: string, identity: string) {
  return `/v1/future-commitments/${source}/${encodeURIComponent(identity)}?refresh=${Date.now()}`;
}
const dateLabel = financialDateLabel;

function maskMonth(value: string): string {
  const digits = value.replace(/\D/g, '').slice(0, 6);
  if (digits.length <= 2) return digits;
  return `${digits.slice(0, 2)}/${digits.slice(2)}`;
}

function paymentOf(card: Card, cardLabelFor: (id: string) => string | undefined, accountLabelFor: (id: string) => string): string {
  const item = card.payload;
  const linked = item.linked_purchase || {};
  const cardName = linked.cartao_label || linked.card_label || item.card_label || (item.card_id ? (cardLabelFor(item.card_id) || '') : '');
  const accountName = linked.conta_label || (item.account_id ? accountLabelFor(String(item.account_id)) : '');
  const method = linked.metodo_pagamento || item.payment_method || item.metodo_pagamento || '';
  const label = settlementLabel({paymentMethod: method, accountLabel: accountName, cardLabel: cardName});
  if (label) return label;
  if (card.source === 'installment') return 'não vinculado';
  return paymentMethodLabel(item.payment_method);
}

function LinkedPaymentSheet({item, link, api, money, onChanged, onSwap}: {item: any; link: any; api: Api; money: (value: number) => string; onChanged: () => Promise<void>; onSwap: () => void}) {
  const [busy, setBusy] = useState(false);
  const captureScope = useAsyncScope(item.commitment_id);
  const source = link.transaction_id ? {transaction_id: link.transaction_id} : {provisional_id: link.provisional_id};
  const remove = () => {
    if (busy) return;
    const dialogValid = captureScope();
    Alert.alert('Desvincular pagamento', 'O lançamento continua no extrato. A agenda só volta se este for o vínculo mais recente que a moveu.', [
      {text: 'Cancelar', style: 'cancel'},
      {text: 'Desvincular', onPress: () => {void (async () => {
        if (!dialogValid() || busy) return;
        const valid = captureScope();
        setBusy(true);
        try {
          const result = await api(`/v1/commitments/${item.commitment_id}/payment-unlink`, {method: 'POST', body: JSON.stringify({request_id: creationId(), expected_revision: item.revision || 1, ...source})});
          if (!valid()) return;
          Alert.alert(result?.warning ? 'Agenda mantida' : 'Vínculo removido', result?.warning || 'A agenda voltou ao estado anterior a este vínculo.');
          await onChanged();
        } catch (error: any) {
          if (valid()) Alert.alert('Não foi possível desvincular', error.message);
        } finally {if (valid()) setBusy(false);}
      })();}},
    ]);
  };
  return <View>
    <Text style={s.meta}>{dateLabel(link.paid_date || link.due_date)}</Text>
    <Text style={s.name}>{link.original_description || 'Lançamento'}</Text>
    <Text style={s.value}>{money(Number(link.actual_amount || 0))}</Text>
    <Pressable accessibilityRole="button" accessibilityLabel="Trocar lançamento" disabled={busy} onPress={onSwap} style={s.compactSecondary}><Text style={s.secondaryText}>Trocar lançamento</Text></Pressable>
    <Pressable accessibilityRole="button" accessibilityLabel="Desvincular pagamento" disabled={busy} onPress={remove} style={s.compactSecondary}><Text style={s.secondaryText}>Desvincular</Text></Pressable>
  </View>;
}

type LinkRequest = {cardKey: string; due: string; amount: number; state: string; origin: 'list' | 'detail' | 'schedule'; preferredId?: string; swap?: {item: any; link: any}};
function cardForCharge(cards: Card[], entry: {source_ref?: string}): Card | null {
  const ref = String(entry.source_ref || '');
  const split = ref.indexOf(':');
  const kind = split < 0 ? '' : ref.slice(0, split);
  const id = split < 0 ? '' : ref.slice(split + 1);
  if (kind === 'commitment') return cards.find(card => card.key === `managed:${id}`) || null;
  if (kind === 'installment') return cards.find(card => card.key === `installment:${id}`) || null;
  if (kind === 'obligation') return cards.find(card => card.source === 'obligation' && (card.payload?.items || []).some((item: {id?: string}) => String(item.id) === id)) || null;
  return null;
}
export default function CommitmentBoard({width, managedLoaded, items, legacy, obligations, money, cardLabelFor, accountLabelFor = () => '', cardChoices = [], accountChoices = [], api, onEdit, onCommitLink, onUpdated, summary, chart, editing = false, paymentStamp = '', linkUndo = null, onUndoLink, monthOpen = false, bindCharge}: {
  width: number; managedLoaded: boolean; items: any[]; legacy: any[]; obligations: any[]; money: (value: number) => string;
  cardLabelFor: (id: string) => string | undefined; accountLabelFor?: (id: string) => string;
  cardChoices?: Array<{value: string; label: string}>; accountChoices?: Array<{value: string; label: string}>;
  api: Api; onEdit: (item: any) => void;
  onCommitLink: (item: any, transaction: any) => Promise<boolean | void> | boolean | void;
  onUpdated: () => Promise<void>; editing?: boolean;
  summary?: React.ReactNode; chart?: React.ReactNode; paymentStamp?: string;
  linkUndo?: {commitmentId: string; name: string} | null; onUndoLink?: () => void;
  monthOpen?: boolean;
  bindCharge?: (opener: (entry: {source_ref?: string}) => void) => void;
}) {
  const [paidTarget, setPaidTarget] = useState<string | null>(null);
  const [paidOpen, setPaidOpen] = useState(false);
  const [futureEdit, setFutureEdit] = useState<string | null>(null);
  const [profileEdit, setProfileEdit] = useState<string | null>(null);
  const [endEdit, setEndEdit] = useState<string | null>(null);
  const [endMonth, setEndMonth] = useState('');
  const [linkRequest, setLinkRequest] = useState<LinkRequest | null>(null);
  const [scheduleSegment, setScheduleSegment] = useState<'open' | 'paid'>('open');
  const [filter, setFilter] = useState<CommitmentFilter>('Tudo');
  const [showCompleted, setShowCompleted] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [actionsKey, setActionsKey] = useState<string | null>(null);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [armedKey, setArmedKey] = useState<string | null>(null);
  const [closeKey, setCloseKey] = useState<string | null>(null);
  const [scheduleKey, setScheduleKey] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [collapsedYears, setCollapsedYears] = useState<Record<string, boolean>>({});
  const [undoOffers, setUndoOffers] = useState<Array<{key: string; source: 'obligation' | 'managed' | 'installment'; title: string; rows: Array<{identity: string; delete_request_id: string}>}>>([]);
  const [busy, setBusy] = useState(false);
  const captureScope = useAsyncScope();
  const [reconcileAll, setReconcileAll] = useState(false);
  const [importedUndo, setImportedUndo] = useState<{source: 'installment' | 'obligation'; identity: string; revision: number; title: string; transactionId?: string; provisionalId?: string} | null>(null);

  const wide = width >= 960;
  const managed: Card[] = items.map(item => {
    const heading = headingForManaged(item);
    return {
      key: `managed:${item.commitment_id}`, source: 'managed', title: heading.title, subtitle: heading.subtitle, payload: item,
      completed: item.status === 'concluido' || item.status === 'cancelado',
    };
  });
  const plans: Card[] = legacy.map(item => {
    const heading = headingForInstallment(item);
    const projected = (item.installments || []).filter((part: any) => part.status === 'projetada' && !part.payment_link);
    return {key: `installment:${item.id}`, source: 'installment', title: heading.title, subtitle: heading.subtitle, payload: item, completed: projected.length === 0 || sumAmounts(projected.map((part: any) => part.valor || 0)) === 0};
  });
  const seriesCards: Card[] = groupObligationSeries(obligations).map(series => ({
    key: `series:${series.series_key}`, source: 'obligation', title: series.title, subtitle: series.subtitle, payload: series, completed: false,
  }));
  const cards = [...managed, ...plans, ...seriesCards];
  const visible = (card: Card) => filter === 'Tudo' || decisionFilter(card.source, card.payload) === filter;
  const active = cards.filter(card => visible(card) && !card.completed);
  const completed = cards.filter(card => visible(card) && card.completed);
  const reconcile = cards.flatMap(card => {
    const open = pendingOccurrences(card.source, card.payload).find(row => reconcileCharge(row.due, row.state));
    if (!open || card.completed) return [];
    return [{key: card.key, cardKey: card.key, title: card.title, due: open.due, amount: open.amount, estimated: open.estimated, state: open.state, managed: card.source === 'managed' ? card.payload : null}];
  }).sort((left, right) => (left.state === 'Vencido · sem vínculo' ? 0 : 1) - (right.state === 'Vencido · sem vínculo' ? 0 : 1) || left.due.localeCompare(right.due) || left.title.localeCompare(right.title));
  const shownReconcile = reconcileAll ? reconcile : reconcile.slice(0, 3);
  useLocalBack(() => {
    if (linkRequest) {setLinkRequest(null); return;}
    if (scheduleKey) {setScheduleKey(null); return;}
    if (confirmKey) {setConfirmKey(null); setArmedKey(null); return;}
    if (closeKey) {setCloseKey(null); return;}
    if (openKey) setOpenKey(null);
  }, Boolean(linkRequest || scheduleKey || confirmKey || closeKey || openKey), 20);
  const opened = cards.find(card => card.key === openKey) || null;
  useLayoutEffect(() => {
    bindCharge?.(entry => {
      const card = cardForCharge(cards, entry);
      if (!card) return;
      setScheduleKey(null);
      setActionsKey(null);
      setOpenKey(card.key);
    });
  }, [bindCharge, cards]);

  const removeSeries = async (card: Card) => {
    if (busy || card.source !== 'obligation' || !captureScope()()) return;
    const valid = captureScope();
    setBusy(true);
    const done: Array<{identity: string; delete_request_id: string}> = [];
    try {
      for (const item of card.payload.items) {
        const path = `/v1/future-commitments/obligation/${encodeURIComponent(item.id)}`;
        const preview = await api(path);
        const requestId = creationId();
        const saved = await api(path, {method: 'DELETE', body: JSON.stringify({request_id: requestId, preview_token: preview.preview_token})});
        if (!saved.persisted || saved.identity !== String(item.id)) throw new Error('O servidor não confirmou esta alteração. Tente novamente.');
        done.push({identity: String(item.id), delete_request_id: String(saved.delete_request_id || requestId)});
      }
      if (!valid()) return;
      rememberUndo(card.key, 'obligation', card.title, done);
      setConfirmKey(null);
      Alert.alert('Compromissos futuros excluídos', 'A alteração foi confirmada. O histórico foi preservado.');
      await onUpdated();
    } catch (failure: any) {
      if (!valid()) return;
      if (done.length) rememberUndo(card.key, 'obligation', card.title, done);
      Alert.alert('Não foi possível concluir a remoção', failure?.message || 'A remoção parou. O que já foi retirado pode ser desfeito.');
    } finally {if (valid()) setBusy(false);}
  };
  const rememberUndo = (key: string, source: 'obligation' | 'managed' | 'installment', title: string, rows: Array<{identity: string; delete_request_id: string}>) => {
    setUndoOffers(current => {
      const previous = current.find(item => item.key === key);
      const merged = [...(previous?.rows || [])];
      rows.forEach(row => {if (!merged.some(item => item.identity === row.identity)) merged.push(row);});
      return [...current.filter(item => item.key !== key), {key, source, title, rows: merged}];
    });
  };
  const undoOffer = async (offer: {key: string; source: 'obligation' | 'managed' | 'installment'; title: string; rows: Array<{identity: string; delete_request_id: string}>}) => {
    if (busy || !offer.rows.length || !captureScope()()) return;
    const valid = captureScope();
    setBusy(true);
    try {
      for (const row of offer.rows) {
        const saved = await api(`/v1/future-commitments/${offer.source}/${encodeURIComponent(row.identity)}/undo`, {method: 'POST', body: JSON.stringify({request_id: creationId(), delete_request_id: row.delete_request_id})});
        if (!saved.restored) throw new Error('O servidor não confirmou o desfazer.');
      }
      if (!valid()) return;
      setUndoOffers(current => current.filter(item => item.key !== offer.key));
      Alert.alert('Exclusão futura desfeita', 'As parcelas futuras voltaram ao planejamento. Pagamentos já vinculados não foram alterados.');
      await onUpdated();
    } catch (failure: any) {
      if (valid()) Alert.alert('Não foi possível desfazer', failure?.message || 'Tente novamente.');
    } finally {if (valid()) setBusy(false);}
  };

  const closeManaged = async (card: Card) => {
    if (busy || card.source !== 'managed' || !captureScope()()) return;
    const valid = captureScope();
    const item = card.payload;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        kind: item.kind, name: item.name, purchase_date: String(item.purchase_date).slice(0, 10), next_due_date: String(item.next_due_date).slice(0, 10),
        amount: Number(item.amount).toFixed(2), frequency: item.frequency, payment_method: item.payment_method, renewal_status: item.renewal_status,
        status: 'cancelado', revision: item.revision,
      };
      for (const field of ['amount_mode', 'account_id', 'card_id', 'billing_platform', 'category', 'notes', 'obligation_plan'] as const) if (item[field]) body[field] = item[field];
      if (item.cash_due_date) body.cash_due_date = String(item.cash_due_date).slice(0, 10);
      if (item.end_date) body.end_date = String(item.end_date).slice(0, 10);
      if (item.kind === 'parcelamento' || (item.kind === 'obrigacao' && item.installment_count)) {body.installment_count = item.installment_count; body.next_installment_number = item.next_installment_number;}
      const saved = await api(`/v1/commitments/${item.commitment_id}`, {method: 'PUT', body: JSON.stringify(body)});
      if (!saved || (saved.status && saved.status !== 'cancelado')) throw new Error('O servidor não confirmou o encerramento.');
      if (!valid()) return;
      setCloseKey(null);
      Alert.alert('Compromisso encerrado', item.kind === 'assinatura'
        ? 'O histórico e os pagamentos já vinculados permanecem. Isso não cancela o serviço no fornecedor.'
        : 'O histórico e os pagamentos já vinculados permanecem. Isso não quita nem altera o contrato.');
      await onUpdated();
    } catch (failure: any) {
      if (valid()) Alert.alert('Não foi possível encerrar', failure?.message || 'Tente novamente.');
    } finally {if (valid()) setBusy(false);}
  };
  const saveEnd = async (card: Card, end: string) => {
    if (busy || !captureScope()()) return;
    const valid = captureScope();
    const item = card.payload;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        kind: item.kind, name: item.name, purchase_date: String(item.purchase_date).slice(0, 10), next_due_date: String(item.next_due_date).slice(0, 10),
        amount: Number(item.amount).toFixed(2), frequency: item.frequency, payment_method: item.payment_method, renewal_status: item.renewal_status,
        status: item.status, revision: item.revision, end_date: end,
      };
      for (const field of ['amount_mode', 'account_id', 'card_id', 'billing_platform', 'category', 'notes', 'obligation_plan'] as const) if (item[field]) body[field] = item[field];
      if (item.cash_due_date) body.cash_due_date = String(item.cash_due_date).slice(0, 10);
      if (item.kind === 'assinatura' && item.renewal_status === 'nao_renovar' && item.remaining_charges != null) body.remaining_charges = item.remaining_charges;
      if (item.kind === 'parcelamento' || (item.kind === 'obrigacao' && item.installment_count)) {body.installment_count = item.installment_count; body.next_installment_number = item.next_installment_number;}
      const saved = await api(`/v1/commitments/${item.commitment_id}`, {method: 'PUT', body: JSON.stringify(body)});
      if (!saved?.commitment_id && saved?.status === undefined && !saved?.revision) throw new Error('O servidor não confirmou o término.');
      if (!valid()) return;
      setEndEdit(null);
      Alert.alert('Término gravado', 'As cobranças depois dessa data saíram da projeção. Vínculos e extrato permanecem.');
      await onUpdated();
    } catch (failure: any) {
      if (valid()) Alert.alert('Não foi possível gravar o término', failure?.message || 'Tente novamente.');
    } finally {if (valid()) setBusy(false);}
  };
  const askEnd = (card: Card) => {
    const match = /^(\d{2})\/(\d{4})$/.exec(endMonth.trim());
    if (!match || Number(match[1]) < 1 || Number(match[1]) > 12) {Alert.alert('Confira o mês', 'Informe o mês como mm/aaaa, por exemplo 12/2027.'); return;}
    const month = Number(match[1]);
    const year = Number(match[2]);
    const anchor = Number(card.payload.schedule_anchor_day || String(card.payload.next_due_date || '').slice(8, 10) || 1);
    const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const day = Math.min(anchor || 1, last);
    const end = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const purchase = String(card.payload.purchase_date || '').slice(0, 10);
    if (purchase && end < purchase) {Alert.alert('Confira o mês', 'O término não pode anteceder a contratação.'); return;}
    const name = ['', 'jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'][month];
    Alert.alert('Terminar em', `Última cobrança em ${name}/${year}. Depois disso não há projeção. Vínculos e extrato permanecem.`, [
      {text: 'Cancelar', style: 'cancel'},
      {text: 'Confirmar término', onPress: () => {void saveEnd(card, end);}},
    ]);
  };
  const askClose = (card: Card) => {
    const item = card.payload;
    Alert.alert('Encerrar compromisso', closureWarning(item.kind === 'assinatura' ? 'assinatura' : 'financiamento'), [
      {text: 'Cancelar', style: 'cancel'},
      {text: `Confirmar encerramento de ${item.name}`, onPress: () => {void closeManaged(card);}},
    ]);
  };
  const confirmRemoval = (message: string, label: string, run: () => void, title = 'Remover parcelas futuras') => {
    Alert.alert(title, message, [
      {text: 'Cancelar', style: 'cancel'},
      {text: label, style: 'destructive', onPress: run},
    ]);
  };
  const askRemove = async (card: Card, purpose: 'encerrar' | 'remover' = 'remover') => {
    const valid = captureScope();
    if (!valid()) return;
    const closing = purpose === 'encerrar';
    const dialogTitle = closing ? 'Encerrar compromisso' : 'Remover parcelas futuras';
    if (card.source === 'obligation') {
      const message = `${closing ? closureWarning('financiamento') + '\n' : ''}${futureRemovalLabel(card.payload.items.length, card.title, card.payload.total)}. Pagamentos já realizados não mudam.\n${closureWarning('financiamento')}`;
      const run = () => removeSeries(card);
      const confirmLabel = closing ? `Confirmar encerramento de ${card.title}` : `Confirmar remoção de ${card.title}`;
      if (reinforcesClosure(card.payload.items.length, card.payload.total)) {
        Alert.alert(dialogTitle, message, [
          {text: 'Cancelar', style: 'cancel'},
          {text: closing ? `Continuar com o encerramento de ${card.title}` : `Continuar com a remoção de ${card.title}`, onPress: () => confirmRemoval(message, confirmLabel, run, dialogTitle)},
        ]);
      } else confirmRemoval(message, confirmLabel, run, dialogTitle);
      return;
    }
    const source = card.source === 'managed' ? 'managed' : 'installment';
    const identity = card.source === 'managed' ? card.payload.commitment_id : card.payload.id;
    const kind = card.source === 'managed' && card.payload.kind === 'assinatura' ? 'assinatura' : 'financiamento';
    try {
      const fresh = await api(removalPreviewPath(source, identity));
      if (!valid()) return;
      const rows = (fresh.rows || []).filter((row: any) => row.editable);
      const message = `${closing ? closureWarning(kind) + '\n' : ''}${futureRemovalLabel(fresh.editable_count || rows.length, card.title, sumAmounts(rows.map((row: any) => row.amount || 0)))}\n${closureWarning(kind)}\nPagamentos já realizados e já vinculados ficam fora desta remoção.`;
      const removePreview = async () => {
        if (!valid()) return;
        setBusy(true);
        try {
          const saved = await api(`/v1/future-commitments/${source}/${encodeURIComponent(identity)}`, {method: 'DELETE', body: JSON.stringify({request_id: creationId(), preview_token: fresh.preview_token})});
          if (!valid()) return;
          if (!saved.persisted || saved.identity !== String(identity) || saved.source !== source) throw new Error('O servidor não confirmou esta alteração. Tente novamente.');
          rememberUndo(card.key, source, card.title, [{identity: String(identity), delete_request_id: String(saved.delete_request_id || '')}]);
          Alert.alert('Compromissos futuros excluídos', 'A alteração foi confirmada. O histórico foi preservado.');
          await onUpdated();
        } catch (failure: any) {
          if (valid()) Alert.alert('Não foi possível concluir a remoção', failure?.message || 'Tente novamente.');
        } finally {if (valid()) setBusy(false);}
      };
      if (closing) {
        Alert.alert(dialogTitle, message, [
          {text: 'Cancelar', style: 'cancel'},
          {text: `Confirmar encerramento de ${card.title}`, onPress: () => {void removePreview();}},
        ]);
      } else confirmRemoval(message, 'Confirmar remoção', removePreview);
    } catch (failure: any) {
      if (valid()) Alert.alert('Não foi possível conferir o cronograma', failure?.message || 'Tente novamente.');
    }
  };
  const undoImported = async (offer: {source: 'installment' | 'obligation'; identity: string; revision: number; title: string; transactionId?: string; provisionalId?: string}) => {
    if (busy || !offer.identity || !offer.revision || !captureScope()()) return;
    const valid = captureScope();
    setBusy(true);
    try {
      const saved = await api(`/v1/future-commitments/${offer.source}/${encodeURIComponent(offer.identity)}/payment-unlink`, {method: 'POST', body: JSON.stringify({request_id: creationId(), expected_revision: offer.revision, ...(offer.transactionId ? {transaction_id: offer.transactionId} : {provisional_id: offer.provisionalId})})});
      if (!valid()) return;
      if (!saved.persisted) throw new Error('O servidor não confirmou o desfazer.');
      setImportedUndo(null);
      Alert.alert('Vínculo desfeito', 'A cobrança voltou para conciliação. O lançamento do extrato permanece.');
      await onUpdated();
    } catch (error: any) {
      if (valid()) Alert.alert('Não foi possível desfazer', error.message);
    } finally {if (valid()) setBusy(false);}
  };
  const linkImported = async (card: Card, transaction: any, due: string) => {
    const full = String(due || '').slice(0, 10);
    const target = card.source === 'installment'
      ? {source: 'installment' as const, identity: String(card.payload.id || '')}
      : {source: 'obligation' as const, identity: String((card.payload.items || []).find((part: any) => String(part.due_date || '').slice(0, 10) === full && !part.payment_link)?.id || (card.payload.items || []).find((part: any) => String(part.due_date || '').slice(0, 10) === full)?.id || '')};
    if (!target.identity || !transaction?.source_token) {Alert.alert('Não foi possível vincular', 'Essa parcela não está mais na lista.'); return;}
    const valid = captureScope();
    if (!valid()) return;
    setBusy(true);
    try {
      const saved = await api(`/v1/future-commitments/${target.source}/${encodeURIComponent(target.identity)}/payment`, {method: 'POST', body: JSON.stringify({
        request_id: creationId(), expected_source_token: transaction.source_token,
        ...(full.length === 10 ? {due_date: full} : {competence: full.slice(0, 7)}),
        ...(transaction.source_kind === 'confirmed_capture' ? {provisional_id: transaction.provisional_id} : {transaction_id: transaction.id}),
      })});
      if (!valid()) return;
      if (!saved?.persisted) throw new Error('O servidor não confirmou o vínculo.');
      setImportedUndo({...target, revision: Number(saved.revision || 0), title: card.title, transactionId: transaction.source_kind === 'confirmed_capture' ? undefined : transaction.id, provisionalId: transaction.source_kind === 'confirmed_capture' ? transaction.provisional_id : undefined});
      setLinkRequest(null);
      Alert.alert('Pagamento vinculado', 'O vínculo foi registrado. O plano importado não foi reescrito. Use Desfazer enquanto o aviso estiver na tela.');
      await onUpdated();
    } catch (error: any) {
      if (valid()) Alert.alert('Não foi possível vincular', error.message);
    } finally {if (valid()) setBusy(false);}
  };
  const scheduleOverlay = useScheduleOverlay();
  const scheduleView = (card: Card) => {
    const figures = commitmentFigures(card.source, card.payload);
    const paid = settledRows(card.source, card.payload).slice().sort((left, right) => left.due.localeCompare(right.due) || left.key.localeCompare(right.key)).map(row => ({
      key: row.key,
      due: row.due,
      amount: row.amount,
      state: row.state,
      note: '',
      payment: row.paymentLink || (row.state === 'Pago vinculado' ? {paid_date: row.paidDate, actual_amount: row.amount} : null),
    }));
    const openRows = figures.pending.map(row => ({key: row.key, due: row.due, amount: row.amount, state: row.state, note: '', estimated: row.estimated}));
    const summary = figures.endless
      ? `${paid.length} pagas · a pagar sem data final`
      : `${paid.length} pagas · ${figures.count} a pagar · total restante ${money(figures.sum)}`;
    const visible = scheduleSegment === 'paid' ? paid : openRows;
    const entries: any[] = [];
    let year = '';
    for (const row of visible) {
      const nextYear = String(row.due).slice(0, 4) || 'sem-ano';
      if (nextYear !== year) {
        year = nextYear;
        entries.push({kind: 'year', key: `${card.key}:year:${year}`, year: nextYear});
      }
      if (collapsedYears[`${card.key}:${year}`]) continue;
      const dueDay = String(row.due).slice(0, 10);
      const payment = row.payment || (card.payload.linked_transactions || []).find((link: any) => String(link.due_date || link.occurrence_due_date || '').slice(0, 10) === dueDay);
      const statusText = row.state === 'Pago (assumido)' ? 'Pago (assumido)' : row.state === 'Pago' ? 'Pago' : row.state === 'Pago vinculado' ? `Pago · extrato · ${dateLabel(payment?.paid_date || dueDay)} · ${money(Number(payment?.actual_amount || row.amount || 0))}` : '';
      entries.push({
        kind: 'row', key: row.key, due: dueDay, amount: row.amount,
        label: `${dateLabel(row.due) || row.due} · ${amountText(money, row.amount, false)}`,
        state: row.state, statusText, note: row.note,
        link: card.source === 'managed' && row.state === 'Previsto' && needsLinkAction(dueDay, false),
      });
    }
    return {entries, summary};
  };
  const paidRows = (card: Card) => settledRows(card.source, card.payload).slice().sort((left, right) => right.due.localeCompare(left.due) || left.key.localeCompare(right.key));
  const detailBody = (card: Card) => {
    const item = card.payload;
    const figures = commitmentFigures(card.source, card.payload);
    const restante = restanteCopy(figures, money);
    const estimated = figures.estimated;
    const upcoming = figures.pending;
    const preview = upcoming.slice(0, 3);
    const paid = paidRows(card);
    const valueOrigin = estimated ? (item.amount_estimate?.requires_review ? 'Média das 3 últimas contas · a conferir' : 'Média das 3 últimas contas') : card.source === 'managed' ? 'Valor fixo' : '';
    const payment = paymentOf(card, cardLabelFor, accountLabelFor);
    const hypothetical = item.kind === 'obrigacao' && (item.obligation_plan === 'parcelado' || Number(item.installment_count || 0) >= 2);
    const balance = [['Restante', restante.detail], ['Termina em', restante.termina]];
    const instrument = dedupeCardLabel(item.card_label) || payment;
    const facts = card.source === 'managed' && item.kind === 'obrigacao'
      ? [['Tipo', 'Obrigação pontual'], ['Plano', hypothetical ? `Hipotético · ${item.installment_count} parcelas` : 'À vista'], ['Valor total', money(Number(item.amount || 0))], ['Data à vista', dateLabel(String(item.cash_due_date || '').slice(0, 10))], ['Pagamento', payment], ...balance]
      : card.source === 'managed'
      ? [['Tipo', kindLabel(card.source, item)], ['Frequência', frequencyLabel(item.frequency)], ['Pagamento', payment], ['Renovação', renewalLabel(item.renewal_status)], ['Origem do valor', valueOrigin], ...balance]
      : [['Tipo', kindLabel(card.source, item)], ['Pagamento', instrument], ...balance];
    const shown = facts.filter(([, fact]) => fact);
    const futureDue = upcoming.find(row => row.state !== 'Vencido · sem vínculo')?.due || '';
    const overdueRows = upcoming.filter(row => row.state === 'Vencido · sem vínculo');
    const overdueNote = overdueRows.length === 1 ? `1 parcela vencida sem vínculo (${dateLabel(overdueRows[0].due)})` : overdueRows.length > 1 ? `${overdueRows.length} parcelas vencidas sem vínculo (${dateLabel(overdueRows[0].due)})` : '';
    const filledIndex = overdueRows.length ? upcoming.findIndex(row => row.state === 'Vencido · sem vínculo') : 0;
    return <View style={s.detail}>
      <View style={s.heroRow}>
        <Text style={s.hero}>{formatCharge(figures.chargeAmount, figures.suffix, estimated, money)}</Text>
        {estimated && <View style={s.pill}><Text style={s.pillText}>Estimado</Text></View>}
      </View>
      {!!futureDue && <Text style={s.nextDue}>{`Próxima cobrança: ${dateLabel(futureDue)}`}</Text>}
      {!!overdueNote && <Text style={s.overdue}>{overdueNote}</Text>}
      {!!shown.length && <View style={s.summaryCard}>{shown.map(([label, fact], index) => <View key={label} style={[s.fact, index === shown.length - 1 && s.factLast]}><Text style={s.factLabel}>{label}</Text><Text style={s.factValue}>{fact}</Text></View>)}</View>}
      {hypothetical && <Text style={s.meta}>Parcelamento hipotético. Não é contrato com banco, não cria lançamento e não quita o tributo.</Text>}
      {endEdit === card.key && <View style={s.block}>
        <Text style={s.section}>Terminar em</Text>
        <Text style={s.meta}>Mês da última cobrança. Essa cobrança permanece.</Text>
        <TextInput accessibilityLabel={`Mês final de ${card.title}`} value={endMonth} onChangeText={value => setEndMonth(maskMonth(value))} placeholder="mm/aaaa" placeholderTextColor="#92A4A2" keyboardType="number-pad" style={s.monthInput}/>
        <Pressable accessibilityRole="button" accessibilityLabel={`Definir término de ${card.title}`} onPress={() => askEnd(card)} style={s.compactPrimary}><Text style={s.primaryText}>Definir término</Text></Pressable>
      </View>}
      {!!upcoming.length && <View style={s.block}>
        <Text style={s.section}>A pagar</Text>
        {preview.map((row, index) => {
          const overdueRow = row.state === 'Vencido · sem vínculo';
          const stateLabel = overdueRow ? 'Vencida · sem vínculo' : 'Previsto';
          const showLink = index === filledIndex && !card.completed;
          return <View key={row.key} style={s.charge}>
            <View style={s.chargeCopy}>
              <Text style={s.chargeDate}>{dateLabel(row.due) || row.due}</Text>
              {overdueRow && <Text style={s.seal}>{stateLabel}</Text>}
            </View>
            <Text style={s.chargeAmount}>{estimated ? `~ ${money(row.amount)}` : money(row.amount)}</Text>
            {showLink ? <Pressable accessibilityRole="button" accessibilityLabel={`Vincular movimentação de ${item.name}`} onPress={() => setLinkRequest({cardKey: card.key, due: row.due, amount: row.amount, state: row.state, origin: 'detail'})} style={s.linkButton}><Text style={s.primaryText}>Vincular</Text></Pressable> : <Text style={s.chargeState}>{overdueRow ? '' : stateLabel}</Text>}
          </View>;
        })}
        <Pressable accessibilityRole="button" accessibilityLabel={`Ver cronograma de ${card.title}`} onPress={() => setScheduleKey(card.key)} style={s.scheduleLink}><Text style={s.scheduleLinkText}>Ver cronograma completo ›</Text></Pressable>
      </View>}
      {!!paid.length && <View style={s.block}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Pagas (${paid.length})`} accessibilityState={{expanded: paidOpen}} onPress={() => setPaidOpen(value => !value)} style={s.paidCard}>
          <Text style={s.paidTitle}>{`Pagas (${paid.length})`}</Text>
          <Text style={s.paidChevron}>{paidOpen ? '⌃' : '⌄'}</Text>
        </Pressable>
        {paidOpen && <View style={s.paidBody}>{paid.map((row: {key: string; due: string; amount: number; label: string; paymentLink?: any; identity?: string; source?: 'installment' | 'obligation'; revision?: number}) => <View key={row.key}><Text style={s.paidLine}>{`${dateLabel(row.due) || 'sem data'} · ${money(row.amount)} · ${row.label}`}</Text>{row.paymentLink && <Pressable accessibilityRole="button" accessibilityLabel={`Desfazer vínculo de ${dateLabel(row.due) || row.due}`} disabled={busy} onPress={() => {void undoImported({source: row.source || 'obligation', identity: String(row.identity || ''), revision: Number(row.revision || 0), title: card.title, transactionId: row.paymentLink.transaction_id, provisionalId: row.paymentLink.provisional_id});}} style={s.scheduleLink}><Text style={s.scheduleLinkText}>Desfazer vínculo</Text></Pressable>}</View>)}</View>}
      </View>}
      {profileEdit === card.key && <CommitmentProfileEditor card={card} api={api} cardLabelFor={cardLabelFor} accountLabelFor={accountLabelFor} cardChoices={cardChoices} accountChoices={accountChoices} onUpdated={onUpdated} onClose={() => setProfileEdit(null)} />}
      {futureEdit === card.key && card.source !== 'managed' && <FutureCommitmentEditor source={card.source === 'installment' ? 'installment' : 'obligation'} identity={card.source === 'installment' ? String(item.id) : String((item.items || []).find((part: {payment_link?: unknown; id?: string}) => !part.payment_link)?.id || item.items?.[0]?.id || '')} name={card.title} api={api} onUpdated={onUpdated} onRemoved={({delete_request_id}) => {const identity = card.source === 'installment' ? String(item.id) : String((item.items || []).find((part: {payment_link?: unknown; id?: string}) => !part.payment_link)?.id || item.items?.[0]?.id || ''); rememberUndo(card.key, card.source === 'installment' ? 'installment' : 'obligation', card.title, [{identity, delete_request_id}]);}} />}
      {futureEdit === card.key && card.source === 'managed' && <FutureCommitmentEditor source="managed" identity={String(item.commitment_id)} kind={item.kind === 'assinatura' ? 'assinatura' : 'parcelamento'} name={card.title} api={api} onUpdated={onUpdated} />}
      {card.source === 'managed' && linkUndo?.commitmentId === item.commitment_id && <Pressable accessibilityRole="button" accessibilityLabel="Desfazer vínculo" onPress={onUndoLink} style={s.scheduleLink}><Text style={s.scheduleLinkText}>Vinculado · Desfazer</Text></Pressable>}
    </View>;
  };
  const confirmSelection = async (transaction: any) => {
    if (!linkRequest || busy) return;
    const card = cards.find(item => item.key === linkRequest.cardKey);
    if (linkRequest.swap && card?.source === 'managed') {
      const {item, link} = linkRequest.swap;
      const source = link.transaction_id ? {transaction_id: link.transaction_id} : {provisional_id: link.provisional_id};
      setBusy(true);
      try {
        await api(`/v1/commitments/${item.commitment_id}/payment-swap`, {method: 'POST', body: JSON.stringify({
          request_id: creationId(), expected_revision: item.revision || 1, expected_source_token: transaction.source_token, ...source,
          ...(transaction.source_kind === 'confirmed_capture' ? {replacement_provisional_id: transaction.provisional_id} : {replacement_transaction_id: transaction.id}),
        })});
        setLinkRequest(null);
        Alert.alert('Lançamento trocado', 'O vínculo passou a usar o lançamento escolhido. A agenda não mudou.');
        await onUpdated();
      } catch (error: any) {
        Alert.alert('Não foi possível trocar', error.message);
      } finally {setBusy(false);}
      return;
    }
    if (!card) return;
    if (card.source !== 'managed') {await linkImported(card, transaction, linkRequest.due); return;}
    const saved = await onCommitLink(card.payload, transaction);
    if (saved !== false) setLinkRequest(null);
  };
  useLayoutEffect(() => {
    if (editing) return;
    const opened = cards.find(item => item.key === openKey);
    const linking = linkRequest ? cards.find(item => item.key === linkRequest.cardKey) : null;
    const paid = paidTarget ? cards.map(card => card.payload).filter(item => item?.commitment_id && paidTarget.startsWith(`${item.commitment_id}:`)).flatMap(item => (item.linked_transactions || []).map((link: any) => ({item, link}))).find(entry => paidTarget === `${entry.item.commitment_id}:${entry.link.transaction_id || entry.link.provisional_id}`) : null;
    const scheduled = cards.find(item => item.key === scheduleKey);
    if (linking && linkRequest && (linking.source === 'managed' || !linkRequest.swap)) {
      const managedLink = linking.source === 'managed';
      const subject = managedLink ? linking.payload : {name: linking.title, commitment_id: linking.payload?.commitment_id || linking.payload?.id || linking.key};
      scheduleOverlay.setPanel({
        title: 'Vincular pagamento',
        scroll: false,
        onClose: () => setLinkRequest(null),
        body: <PaymentLinkScreen api={api} commitment={subject} due={linkRequest.due} amount={linkRequest.amount} stateLabel={linkRequest.state} money={money} preferredId={linkRequest.preferredId} disabled={busy} lookup={!managedLink} onConfirm={transaction => {void confirmSelection(transaction);}}/>,
      });
    } else if (paid) {
      scheduleOverlay.setPanel({title: 'Pagamento vinculado', onClose: () => setPaidTarget(null), body: <LinkedPaymentSheet item={paid.item} link={paid.link} api={api} money={money} onChanged={async () => {setPaidTarget(null); await onUpdated();}} onSwap={() => setLinkRequest({cardKey: `managed:${paid.item.commitment_id}`, due: String(paid.link.due_date || paid.item.next_due_date || '').slice(0, 10), amount: Number(paid.link.actual_amount || paid.item.amount || 0), state: 'Pago vinculado', origin: 'detail', swap: {item: paid.item, link: paid.link}})}/>});
    } else if (scheduled) {
      const view = scheduleView(scheduled);
      scheduleOverlay.show({
        title: scheduled.title,
        rows: view.entries,
        summary: view.summary,
        segment: scheduleSegment,
        onSegment: setScheduleSegment,
        onClose: () => {setScheduleKey(null); setScheduleSegment('open');},
        onToggleYear: year => setCollapsedYears(current => ({...current, [`${scheduled.key}:${year}`]: !current[`${scheduled.key}:${year}`]})),
        onLink: due => setLinkRequest({cardKey: scheduled.key, due: String(due || scheduled.payload.next_due_date || '').slice(0, 10), amount: Number(scheduled.payload.amount || 0), state: 'Previsto', origin: 'schedule'}),
      });
    } else if (!opened) scheduleOverlay.clear();
    else {
      const canEnd = opened.source === 'managed' && opened.payload.status === 'ativo' && (opened.payload.kind === 'assinatura' || (opened.payload.kind === 'obrigacao' && (opened.payload.obligation_plan === 'parcelado' || Number(opened.payload.installment_count || 0) >= 2)));
      const canClose = !opened.completed && (opened.source !== 'managed' || opened.payload.status === 'ativo');
      const menu = opened.completed ? [] : [
        ...(canEnd ? [{label: 'Terminar em', accessibilityLabel: `Terminar ${opened.title} em`, onPress: () => {const stored = String(opened.payload.end_date || '').slice(0, 10); setEndMonth(stored.length === 10 ? `${stored.slice(5, 7)}/${stored.slice(0, 4)}` : ''); setEndEdit(opened.key);}}] : []),
        ...(canClose ? [{label: 'Encerrar', accessibilityLabel: `Encerrar ${opened.source === 'managed' ? opened.payload.name : opened.title}`, onPress: () => opened.source === 'managed' ? askClose(opened) : void askRemove(opened, 'encerrar')}] : []),
        {label: 'Remover parcelas futuras', accessibilityLabel: `Remover parcelas futuras de ${opened.title}`, onPress: () => {void askRemove(opened);}},
        {label: 'Editar parcelas futuras', accessibilityLabel: `Editar futuras de ${opened.title}`, onPress: () => setFutureEdit(opened.key)},
      ];
      scheduleOverlay.setPanel({
        title: `Detalhe de ${opened.title}`,
        onClose: () => {setOpenKey(null); setActionsKey(null); setCloseKey(null); setPaidOpen(false); setFutureEdit(null); setProfileEdit(null); setEndEdit(null);},
        onEdit: opened.completed ? undefined : () => setProfileEdit(opened.key),
        menuLabel: `Mais ações de ${opened.title}`,
        menu,
        scrollToken: paymentStamp,
        body: detailBody(opened),
      });
    }
    return () => scheduleOverlay.clear();
  }, [editing, scheduleKey, openKey, paidTarget, paidOpen, futureEdit, profileEdit, endEdit, endMonth, linkRequest, scheduleSegment, collapsedYears, actionsKey, closeKey, confirmKey, armedKey, items, legacy, obligations, busy, paymentStamp, linkUndo]);
  const cardView = (card: Card) => {
    const item = card.payload;
    const figures = commitmentFigures(card.source, card.payload);
    const copy = restanteCopy(figures, money);
    const open = () => {setScheduleKey(null); setActionsKey(null); setOpenKey(card.key);};
    const charge = formatCharge(figures.chargeAmount, figures.suffix, figures.estimated && !figures.chargeMuted, money);
    if (card.completed) {
      const when = card.source === 'managed' ? (item.status === 'cancelado' ? item.cancelled_at || item.updated_at : item.updated_at) : item.completed_at;
      const cardBit = card.source === 'installment' ? ` · Cartão: ${dedupeCardLabel(item.card_label) || 'não vinculado'}` : '';
      return <Pressable key={card.key} accessibilityRole="button" accessibilityLabel={`Abrir ${card.title}`} onPress={open} style={[s.inactive, wide && s.cardHalf]}>
        <View style={s.line}><Text style={s.inactiveName}>{card.title}</Text><Text style={s.inactiveMeta}>{charge}</Text></View>
        <Text style={s.inactiveMeta}>{inactiveLine(card.source === 'managed' ? item.status : 'concluido', when)} · último valor {money(figures.chargeAmount)}{cardBit}</Text>
      </Pressable>;
    }
    const badge = figures.pending[0]?.kind === 'vencida' ? 'Vencido · sem vínculo' : '';
    return <View key={card.key} style={[s.card, wide && s.cardHalf]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Abrir ${card.title}`} onPress={open}>
        <View style={s.line}><Text style={s.name}>{card.title}</Text><Text style={s.value}>{charge}</Text></View>
        {!!card.subtitle && <Text style={s.meta}>{card.subtitle}</Text>}
        <View style={s.line}>
          <Text style={s.meta}>{cardLine2(card.source, item, figures, paymentOf(card, cardLabelFor, accountLabelFor))}</Text>
          {!!badge && <Text style={s.badge}>{badge}</Text>}
        </View>
        <Text style={s.meta}><Text style={s.restAmount}>{copy.amountPiece}</Text><Text style={s.restTail}>{copy.restPiece}</Text></Text>
      </Pressable>
    </View>;
  };

  if (monthOpen) return <View style={s.monthDestination} accessibilityLabel="Destino do mês">{chart}</View>;
  return <View style={wide ? s.wide : undefined}>
    <View style={wide ? s.column : undefined} accessibilityLabel="Lista de compromissos">
      {undoOffers.map(offer => <View key={offer.key} style={s.card}><Text style={s.meta}>A exclusão de {offer.title} saiu da lista e pode ser desfeita. Pagamentos já vinculados não foram alterados.</Text><Pressable accessibilityRole="button" accessibilityLabel={`Desfazer exclusão de ${offer.title}`} disabled={busy} onPress={() => undoOffer(offer)} style={s.secondary}><Text style={s.secondaryText}>Desfazer exclusão de futuras</Text></Pressable></View>)}
      {linkUndo && !openKey && <View style={s.card}><Text style={s.meta}>Pagamento vinculado a {linkUndo.name}.</Text><Pressable accessibilityRole="button" accessibilityLabel="Desfazer vínculo" onPress={onUndoLink} style={s.compactPrimary}><Text style={s.primaryText}>Vinculado · Desfazer</Text></Pressable></View>}
      {importedUndo && !openKey && <View style={s.card}><Text style={s.meta}>Pagamento vinculado a {importedUndo.title}.</Text><Pressable accessibilityRole="button" accessibilityLabel="Desfazer vínculo" disabled={busy} onPress={() => {void undoImported(importedUndo);}} style={s.compactPrimary}><Text style={s.primaryText}>Vinculado · Desfazer</Text></Pressable></View>}
      {summary}
      {!!reconcile.length && <View accessibilityLabel="A conciliar" style={s.card}><Text style={s.heading}>{`A conciliar (${reconcile.length})`}</Text>{shownReconcile.map(entry => {
        return <View key={entry.key} style={s.reconcile}>
          <View style={s.reconcileCopy}>
            <Text style={s.name}>{entry.title}</Text>
            <Text style={s.reconcileMeta}>{reconcileMeta(entry.amount, entry.estimated, entry.due, entry.state, money)}</Text>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={`Conciliar ${entry.title}`} onPress={() => setLinkRequest({cardKey: entry.cardKey, due: entry.due, amount: entry.amount, state: entry.state, origin: 'list'})} style={s.ghostButton}><Text style={s.secondaryText}>Conciliar</Text></Pressable>
        </View>;
      })}{reconcile.length > 3 && !reconcileAll && <Pressable accessibilityRole="button" accessibilityLabel={`Ver todos (${reconcile.length})`} onPress={() => setReconcileAll(true)} style={s.scheduleLink}><Text style={s.scheduleLinkText}>{`Ver todos (${reconcile.length})`}</Text></Pressable>}</View>}
      {chart}
      <View style={s.sectionHead}><Text style={s.heading}>Todos os compromissos</Text><Pressable accessibilityRole="button" accessibilityLabel="Como ler esta lista" onPress={() => setHelpOpen(true)} style={s.iconButton}><Text style={s.secondaryText}>ⓘ</Text></Pressable></View>
      {helpOpen && <View style={s.card}><Text style={s.meta}>Compromissos previstos aparecem aqui. Pagamentos reais continuam em Movimentações. Cada compromisso aparece uma vez. Toque o card para abrir o detalhe. Faturas de cartão não entram nas barras. Contas variáveis usam a média atual. Cobranças anteriores a 01/10/2026 aparecem quitadas como linha de base inicial. De outubro em diante, sem vínculo fica previsto ou atrasado, mesmo nos meses seguintes.</Text><Pressable accessibilityRole="button" accessibilityLabel="Fechar explicação da lista" onPress={() => setHelpOpen(false)} style={s.compactSecondary}><Text style={s.secondaryText}>Fechar</Text></Pressable></View>}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.filterRow} contentContainerStyle={s.filters}>{filters.map(option => <Pressable key={option} accessibilityRole="button" accessibilityState={{selected: filter === option}} onPress={() => setFilter(option)} style={[s.chip, filter === option && s.chipOn]}><Text style={s.secondaryText}>{option}</Text></Pressable>)}</ScrollView>
      {managedLoaded && items.length === 0 && <Text style={s.meta}>Nenhum compromisso cadastrado ainda.</Text>}
      <View style={wide ? s.cardGrid : undefined}>{active.map(cardView)}</View>
      {!!completed.length && <Pressable accessibilityRole="button" accessibilityState={{expanded: showCompleted}} accessibilityLabel="Concluídos e cancelados" onPress={() => setShowCompleted(value => !value)} style={s.secondary}><Text style={s.secondaryText}>{`Concluídos e cancelados (${completed.length})`}</Text></Pressable>}
      {showCompleted && completed.map(cardView)}
    </View>
  </View>;
}

const s = StyleSheet.create({
  wide: {flexDirection: 'row', alignItems: 'flex-start', gap: 12},
  monthDestination: {flex: 1},
  column: {flex: 1, minWidth: 0},
  card: {backgroundColor: '#122124', borderColor: '#315054', borderWidth: 1, borderRadius: 14, padding: 12, marginVertical: 8},
  heading: {color: '#F4F7F5', fontSize: 16, fontWeight: '800', marginBottom: 8},
  line: {flexDirection: 'row', justifyContent: 'space-between', gap: 8},
  name: {color: '#F4F7F5', fontSize: 15, fontWeight: '700', flex: 1},
  value: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  detail: {paddingBottom: 24},
  heroRow: {flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 20},
  hero: {color: '#F4F7F5', fontSize: 32, fontWeight: '800'},
  pill: {borderWidth: 1, borderColor: '#395358', borderRadius: 14, paddingHorizontal: 10, paddingVertical: 4},
  pillText: {color: '#C5D5D2', fontSize: 13, fontWeight: '600'},
  nextDue: {color: '#C5D5D2', fontSize: 15, marginTop: 6},
  summaryCard: {backgroundColor: '#142326', borderRadius: 16, marginTop: 20, paddingHorizontal: 16},
  fact: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, minHeight: 48, borderBottomWidth: 1, borderBottomColor: '#263D40'},
  factLast: {borderBottomWidth: 0},
  factLabel: {color: '#C5D5D2', fontSize: 15},
  factValue: {color: '#F4F7F5', fontSize: 15, fontWeight: '700', flexShrink: 1, textAlign: 'right'},
  block: {marginTop: 20},
  section: {color: '#F4F7F5', fontSize: 18, fontWeight: '800', marginBottom: 4},
  charge: {minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 8, borderBottomWidth: 1, borderBottomColor: '#263D40'},
  chargeDate: {color: '#F4F7F5', fontSize: 15, flex: 1},
  chargeAmount: {color: '#C5D5D2', fontSize: 15},
  chargeState: {color: '#C5D5D2', fontSize: 15, minWidth: 72, textAlign: 'right'},
  linkButton: {backgroundColor: '#49C5AA', borderRadius: 12, minHeight: 44, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center'},
  scheduleLink: {minHeight: 48, justifyContent: 'center', marginTop: 4},
  scheduleLinkText: {color: '#67DFC3', fontSize: 16, fontWeight: '700'},
  paidCard: {minHeight: 56, borderWidth: 1, borderColor: '#315054', borderRadius: 16, paddingHorizontal: 16, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between'},
  paidTitle: {color: '#F4F7F5', fontSize: 16, fontWeight: '700'},
  paidChevron: {color: '#C5D5D2', fontSize: 18},
  paidBody: {paddingTop: 8, paddingHorizontal: 4},
  paidLine: {color: '#C5D5D2', fontSize: 14, lineHeight: 22, paddingVertical: 6},
  monthInput: {color: '#F4F7F5', fontSize: 16, minHeight: 48, borderWidth: 1, borderColor: '#395358', borderRadius: 12, paddingHorizontal: 12, marginTop: 8},
  meta: {color: '#C5D5D2', fontSize: 13, lineHeight: 18, marginTop: 4},
  pending: {color: '#F4F7F5', fontSize: 13, marginTop: 4},
  overdue: {color: '#E7B15A', fontSize: 13, fontWeight: '700', marginTop: 4},
  primary: {backgroundColor: '#49C5AA', borderRadius: 10, minHeight: 48, paddingHorizontal: 14, paddingVertical: 12, alignItems: 'center', justifyContent: 'center', marginTop: 8},
  compactPrimary: {backgroundColor: '#49C5AA', borderRadius: 10, minHeight: 48, paddingHorizontal: 14, alignSelf: 'flex-start', alignItems: 'center', justifyContent: 'center', marginTop: 8},
  primaryText: {color: '#061412', fontSize: 15, fontWeight: '800'},
  secondary: {minHeight: 48, borderRadius: 10, borderWidth: 1, borderColor: '#395358', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12, marginTop: 8},
  compactSecondary: {minHeight: 48, borderRadius: 10, borderWidth: 1, borderColor: '#395358', alignSelf: 'flex-start', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12, marginTop: 8},
  cardGrid: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'stretch'},
  cardHalf: {width: '48%', flexGrow: 1},
  reconcile: {paddingVertical: 4, flexDirection: 'row', alignItems: 'center', gap: 8, borderTopWidth: 1, borderColor: '#263D40'},
  reconcileCopy: {flex: 1, minWidth: 0},
  reconcileMeta: {color: '#C5D5D2', fontSize: 13, lineHeight: 18},
  badge: {color: '#E7B15A', fontSize: 13, fontWeight: '700'},
  restAmount: {color: '#F4F7F5', fontSize: 13},
  restTail: {color: '#92A4A2', fontSize: 13},
  confirmChip: {minHeight: 40, borderRadius: 12, backgroundColor: '#244842', paddingHorizontal: 10, alignItems: 'center', justifyContent: 'center'},
  confirmChipText: {color: '#67DFC3', fontSize: 13, fontWeight: '800'},
  ghostButton: {minHeight: 40, borderRadius: 12, borderWidth: 1, borderColor: '#395358', paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center'},
  chargeCopy: {flex: 1, minWidth: 0},
  seal: {color: '#E7B15A', fontSize: 12, fontWeight: '700', marginTop: 2},
  inactive: {paddingVertical: 8, borderBottomWidth: 1, borderColor: '#263D40'},
  inactiveName: {color: '#8FA29E', fontSize: 14, fontWeight: '700'},
  inactiveMeta: {color: '#8FA29E', fontSize: 12, marginTop: 2},
  secondaryText: {color: '#D7E5E1', fontSize: 14, fontWeight: '700'},
  danger: {minHeight: 48, borderRadius: 10, backgroundColor: '#8E2F2F', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12, marginTop: 8},
  dangerText: {color: '#F4F7F5', fontSize: 14, fontWeight: '800'},
  sectionHead: {flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8},
  filterRow: {marginVertical: 8},
  filters: {flexDirection: 'row', gap: 6},
  iconButton: {minWidth: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center'},
  chip: {minHeight: 48, borderRadius: 18, borderWidth: 1, borderColor: '#395358', paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center'},
  chipOn: {backgroundColor: '#244842', borderColor: '#49C5AA'},

});
