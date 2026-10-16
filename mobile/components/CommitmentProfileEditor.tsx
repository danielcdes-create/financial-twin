import React, {useRef, useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import ControlledChoice from './ControlledChoice';
import {Alert} from '../utils/appAlert';
import {creationId} from '../utils/commitmentCreate';
import {frequencyLabel} from '../utils/commitmentPresentation';
import {paymentCodeLabel} from '../utils/commitmentProfile';
import {displayMoney, formatCivilDate, maskCivilDate, maskMoneyInput, parseCivilDate} from '../utils/formMask';
import {parseMoneyInput} from '../utils/moneyInput';
import {natureLabel} from '../utils/movementFilters';
import {useAsyncScope} from '../utils/useAsyncScope';
import {useClassificationOptions} from '../utils/useClassificationOptions';
import DiscardDraftAction from './DiscardDraftAction';
import {protectUnsavedBack, useLocalBack} from '../utils/localBack';

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Card = {key: string; source: 'managed' | 'installment' | 'obligation'; title: string; subtitle: string; payload: any};
type Choice = {value: string; label: string};

const PAYMENT_CODES = ['cartao', 'debito', 'pix', 'boleto', 'paypal', 'outro'];
const NATURES = ['COMPRA', 'IMPOSTO_TRIBUTO', 'TARIFA_ENCARGO', 'ESTORNO', 'RENDIMENTO', 'TRANSFERENCIA'];
const METHODS = ['cartao_credito', 'cartao_debito', 'cartao', 'debito', 'pix', 'boleto', 'paypal', 'dinheiro', 'transferencia', 'conta_corrente', 'outro'];
const FREQUENCIES = ['semanal', 'mensal', 'trimestral', 'semestral', 'anual'];

const needsScope = (card: Card) => card.source === 'installment' || card.source === 'obligation' || card.payload?.kind === 'parcelamento';

function identityOf(card: Card): string {
  if (card.source === 'managed') return String(card.payload.commitment_id || '');
  if (card.source === 'installment') return String(card.payload.id || '');
  return String(card.payload.series_key || '');
}

function originalOf(card: Card): string {
  const item = card.payload || {};
  return String(item.original_description || item.descricao_original || item.linked_purchase?.descricao_original || card.subtitle || item.descricao || item.raw_name || '').trim();
}

function moneyField(value: unknown): string {
  if (value == null || value === '') return '';
  const shown = displayMoney(value as string | number);
  return shown ? `R$ ${shown}` : '';
}

function storedDue(item: any): string {
  return formatCivilDate(String(item.next_due_date || item.due_date || item.nextDue || '').slice(0, 10));
}

/** Category, subcategory, and platform are already the words stored and shown. */
function namedValues(values: string[]): {values: string[]; labelOf: (value: string) => string} {
  const unique = [...new Set(values.map(value => String(value || '').trim()).filter(Boolean))];
  return {values: unique, labelOf: value => unique.includes(value) ? value : ''};
}

/** Keep the current stored code selectable, and never offer that code as its own label. */
function choiceValues(options: Choice[], current: string, label: string): {values: string[]; labelOf: (value: string) => string} {
  const labels = new Map<string, string>();
  for (const option of options) {
    const name = String(option.label || '').trim();
    if (option.value && name && name !== option.value) labels.set(option.value, name);
  }
  if (current && label && label !== current) labels.set(current, label);
  return {values: [...labels.keys()], labelOf: value => labels.get(value) || ''};
}

function named(values: string[], labelOf: (value: string) => string): Choice[] {
  return values.filter(Boolean).map(value => ({value, label: labelOf(value)})).filter(option => option.label && option.label !== option.value);
}

/** Whole-commitment editor. Financing uses the series key, not the next installment id. */
export default function CommitmentProfileEditor({card, api, cardLabelFor = () => '', accountLabelFor = () => '', cardChoices = [], accountChoices = [], onUpdated, onClose}: {
  card: Card; api: Api; cardLabelFor?: (id: string) => string | undefined; accountLabelFor?: (id: string) => string;
  cardChoices?: Choice[]; accountChoices?: Choice[]; onUpdated: () => Promise<void>; onClose: () => void;
}) {
  const item = card.payload || {};
  const linked = item.linked_purchase || {};
  const identity = identityOf(card);
  const original = originalOf(card);
  const catalog = useClassificationOptions(api, card.key).options;
  const initialDisplayName = String(item.display_name || linked.vendedor || (card.title !== original ? card.title : '') || '');
  const initialNatureza = String(item.natureza || linked.natureza || '');
  const initialCategoria = String(item.categoria || item.category || linked.categoria || '');
  const initialSubcategoria = String(item.subcategoria || linked.subcategoria || '');
  const initialPlataforma = String(item.plataforma || item.billing_platform || linked.plataforma || '');
  const initialMethod = String(linked.metodo_pagamento || item.payment_method || item.metodo_pagamento || '');
  const initialAccount = String(item.account_id || linked.account_id || '');
  const initialCardId = String(item.card_id || linked.card_id || '');
  const initialAmount = moneyField(item.amount ?? linked.amount ?? '');
  const initialFrequency = String(item.frequency || 'mensal');
  const initialDue = storedDue(item);
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [natureza, setNatureza] = useState(initialNatureza);
  const [categoria, setCategoria] = useState(initialCategoria);
  const [subcategoria, setSubcategoria] = useState(initialSubcategoria);
  const [plataforma, setPlataforma] = useState(initialPlataforma);
  const [method, setMethod] = useState(initialMethod);
  const [account, setAccount] = useState(initialAccount);
  const [cardId, setCardId] = useState(initialCardId);
  const [amount, setAmount] = useState(initialAmount);
  const [frequency, setFrequency] = useState(initialFrequency);
  const [due, setDue] = useState(initialDue);
  const [scope, setScope] = useState<'next' | 'future'>('next');
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const captureScope = useAsyncScope(`profile:${card.key}`);
  const scoped = needsScope(card);
  const dirty = displayName !== initialDisplayName || natureza !== initialNatureza || categoria !== initialCategoria ||
    subcategoria !== initialSubcategoria || plataforma !== initialPlataforma || method !== initialMethod ||
    account !== initialAccount || cardId !== initialCardId || amount !== initialAmount ||
    frequency !== initialFrequency || due !== initialDue;
  const resetDraft = () => {
    setDisplayName(initialDisplayName);
    setNatureza(initialNatureza);
    setCategoria(initialCategoria);
    setSubcategoria(initialSubcategoria);
    setPlataforma(initialPlataforma);
    setMethod(initialMethod);
    setAccount(initialAccount);
    setCardId(initialCardId);
    setAmount(initialAmount);
    setFrequency(initialFrequency);
    setDue(initialDue);
    setScope('next');
    onClose();
  };
  const back = () => protectUnsavedBack(dirty, onClose);
  useLocalBack(back, true, 35);
  const natureText = (value: string) => String(catalog.natureza_labels?.[value] || natureLabel(value) || '');
  const methodText = (value: string) => paymentCodeLabel(value) || String(catalog.metodo_pagamento_labels?.[value] || '');
  const storedCardId = String(item.card_id || linked.card_id || '');
  const storedCardLabel = String(item.card_label || linked.card_label || linked.cartao_label || '').trim();
  const cardText = (value: string) => {
    const known = String(cardLabelFor(value) || cardChoices.find(option => option.value === value)?.label || '').trim();
    if (known && known !== value) return known;
    if (value && value === storedCardId && storedCardLabel && storedCardLabel !== value) return storedCardLabel;
    return '';
  };
  const storedAccountId = String(item.account_id || linked.account_id || '');
  const storedAccountLabel = String(linked.conta_label || item.account_label || '').trim();
  const accountText = (value: string) => {
    const known = String(accountLabelFor(value) || accountChoices.find(option => option.value === value)?.label || '').trim();
    if (known && known !== value) return known;
    if (value && value === storedAccountId && storedAccountLabel && storedAccountLabel !== value) return storedAccountLabel;
    return '';
  };
  const natures = choiceValues(named([...(catalog.natureza || []), ...NATURES], natureText), natureza, natureText(natureza));
  const methods = choiceValues(named([...(catalog.metodo_pagamento || []), ...METHODS], methodText), method, methodText(method));
  const frequencies = choiceValues(named(FREQUENCIES, frequencyLabel), frequency, frequencyLabel(frequency));
  const categories = namedValues([...(catalog.categoria || []), categoria]);
  const subcategories = namedValues([...(catalog.categoria_subcategorias?.[categoria] || []), subcategoria]);
  const platforms = namedValues([...(catalog.plataforma || []), plataforma]);
  const cards = choiceValues(cardChoices, cardId, cardText(cardId));
  const accounts = choiceValues(accountChoices, account, accountText(account));
  const save = async () => {
    if (running.current || !captureScope()()) return;
    const parsedAmount = amount.trim() ? parseMoneyInput(amount, {allowZero: false}) : null;
    if (amount.trim() && !parsedAmount) {
      Alert.alert('Confira o valor', 'Informe o valor em reais, com centavos.');
      return;
    }
    const dueIso = due.trim() ? parseCivilDate(due) : '';
    if (due.trim() && !dueIso) {
      Alert.alert('Confira a data', 'Use o formato dd/mm/aaaa.');
      return;
    }
    running.current = true;
    setBusy(true);
    const valid = captureScope();
    const methodCode = method.trim();
    const expectedRevision = card.source === 'managed' && item.revision != null ? Number(item.revision) : undefined;
    try {
      const saved = await api('/v1/commitment-profile', {method: 'PUT', body: JSON.stringify({
        request_id: creationId(),
        source: card.source,
        identity,
        ...(expectedRevision !== undefined ? {expected_revision: expectedRevision} : {}),
        display_name: displayName.trim(),
        natureza: natureza.trim() || null,
        categoria: categoria.trim(),
        subcategoria: subcategoria.trim(),
        plataforma: plataforma.trim(),
        metodo_pagamento: methodCode,
        payment_method: PAYMENT_CODES.includes(methodCode) ? methodCode : null,
        account_id: account.trim(),
        card_id: cardId.trim(),
        amount: parsedAmount,
        frequency: frequency.trim() || null,
        due_date: dueIso || null,
        scope: scoped ? scope : null,
      })});
      if (!saved?.persisted) throw new Error('O servidor não confirmou o compromisso.');
      if (!valid()) return;
      Alert.alert('Compromisso atualizado', 'Os lançamentos vinculados no extrato foram atualizados.');
      onClose();
      void Promise.resolve().then(onUpdated).catch(error => {if (valid()) Alert.alert('Alteração salva', `A gravação foi confirmada. A lista não atualizou: ${error.message}`);});
    } catch (error: any) {
      if (valid()) Alert.alert('Não foi possível salvar', error.message || 'Tente novamente.');
    } finally {
      running.current = false;
      if (valid()) setBusy(false);
    }
  };
  return <View accessibilityLabel="Editor do compromisso inteiro" accessibilityValue={{text: identity}} style={s.box}>
    <Text style={s.section}>Editar compromisso</Text>
    <Text style={s.label}>Vendedor / nome de exibição</Text>
    <TextInput accessibilityLabel="Nome de exibição" editable={!busy} value={displayName} onChangeText={setDisplayName} placeholderTextColor="#92A4A2" style={s.input}/>
    <Text style={s.label}>Descrição original</Text>
    <Text accessibilityLabel="Descrição original" style={s.readonly}>{original}</Text>
    <ControlledChoice disabled={busy} label="Natureza" value={natureza} options={natures.values} display={natures.labelOf} onChange={setNatureza} allowClear/>
    <ControlledChoice disabled={busy} label="Categoria" value={categoria} options={categories.values} display={categories.labelOf} onChange={value => {setCategoria(value); setSubcategoria('');}} allowClear/>
    <ControlledChoice disabled={busy} label="Subcategoria" value={subcategoria} options={subcategories.values} display={subcategories.labelOf} onChange={setSubcategoria} allowClear/>
    <ControlledChoice disabled={busy} label="Plataforma" value={plataforma} options={platforms.values} display={platforms.labelOf} onChange={setPlataforma} allowClear/>
    <ControlledChoice disabled={busy} label="Meio de pagamento" value={method} options={methods.values} display={methods.labelOf} onChange={setMethod} allowClear/>
    <ControlledChoice disabled={busy} label="Conta" value={account} options={accounts.values} display={accounts.labelOf} onChange={setAccount} allowClear/>
    <ControlledChoice disabled={busy} label="Cartão" value={cardId} options={cards.values} display={cards.labelOf} onChange={setCardId} allowClear/>
    <Text style={s.label}>Valor</Text>
    <TextInput accessibilityLabel="Valor do compromisso" editable={!busy} value={amount} onChangeText={value => setAmount(value ? `R$ ${maskMoneyInput(value)}` : '')} placeholder="R$ 0,00" placeholderTextColor="#92A4A2" keyboardType="decimal-pad" style={s.input}/>
    <ControlledChoice disabled={busy} label="Frequência" value={frequency} options={frequencies.values} display={frequencies.labelOf} onChange={setFrequency} allowClear={false}/>
    <Text style={s.label}>Vencimento</Text>
    <TextInput accessibilityLabel="Vencimento" editable={!busy} value={due} onChangeText={value => setDue(maskCivilDate(value))} placeholder="dd/mm/aaaa" placeholderTextColor="#92A4A2" keyboardType="number-pad" style={s.input}/>
    {scoped && <View>
      <Pressable accessibilityRole="button" accessibilityLabel="Apenas a próxima parcela em aberto" accessibilityState={{selected: scope === 'next'}} disabled={busy} onPress={() => setScope('next')} style={s.choice}><Text style={s.choiceText}>{scope === 'next' ? '✓ ' : ''}Apenas a próxima parcela em aberto</Text></Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Todas as parcelas futuras em aberto" accessibilityState={{selected: scope === 'future'}} disabled={busy} onPress={() => setScope('future')} style={s.choice}><Text style={s.choiceText}>{scope === 'future' ? '✓ ' : ''}Todas as parcelas futuras em aberto</Text></Pressable>
    </View>}
    <View style={s.actions}>
      <Pressable accessibilityRole="button" accessibilityLabel="Salvar compromisso" disabled={busy} onPress={() => {void save();}} style={s.save}><Text style={s.saveText}>Salvar</Text></Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Cancelar edição do compromisso" disabled={busy} onPress={back} style={s.cancel}><Text style={s.choiceText}>Cancelar</Text></Pressable>
    </View>
    {dirty && <DiscardDraftAction disabled={busy} owner={`profile:${card.key}`} label="Excluir rascunho do compromisso" onDiscard={resetDraft} />}
  </View>;
}

const s = StyleSheet.create({
  box: {marginTop: 12},
  section: {fontSize: 16, fontWeight: '600', color: '#C5D5D2'},
  label: {marginTop: 8, color: '#C5D5D2', fontSize: 12},
  readonly: {color: '#C5D5D2', fontSize: 14, lineHeight: 20, marginTop: 4},
  input: {minHeight: 44, borderWidth: 1, borderColor: '#395358', borderRadius: 12, paddingHorizontal: 12, color: '#F4F7F5', backgroundColor: '#142326'},
  choice: {minHeight: 44, justifyContent: 'center'},
  choiceText: {color: '#C5D5D2', fontSize: 15},
  actions: {flexDirection: 'row', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginTop: 8},
  save: {minHeight: 44, justifyContent: 'center', backgroundColor: '#49C5AA', borderRadius: 12, paddingHorizontal: 16, alignSelf: 'flex-start'},
  saveText: {color: '#061412', fontSize: 15, fontWeight: '800'},
  cancel: {minHeight: 44, justifyContent: 'center', paddingHorizontal: 12},
});
