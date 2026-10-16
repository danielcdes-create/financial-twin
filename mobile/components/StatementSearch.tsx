import {Alert} from '../utils/appAlert';
import {financialDateLabel} from '../utils/classification';
import {parseMoneyInput} from '../utils/moneyInput';
import React, {useEffect, useRef, useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Commitment = {commitment_id: string; amount: number | string; next_due_date?: string};
type Filters = {text: string; from: string; to: string; amount: string; band: boolean};

const dateLabel = financialDateLabel;
const civilShift = (iso: string, days: number) => {
  const [year, month, day] = iso.split('-').map(Number);
  if (!year || !month || !day) return '';
  const stamp = new Date(Date.UTC(year, month - 1, day));
  stamp.setUTCDate(stamp.getUTCDate() + days);
  return stamp.toISOString().slice(0, 10);
};
const bandFor = (amount: number) => {
  const margin = Math.max(5, amount * 0.25);
  return {min: Math.max(0.01, amount - margin).toFixed(2), max: (amount + margin).toFixed(2)};
};
const validDay = (value: string) => value === '' || /^\d{4}-\d{2}-\d{2}$/.test(value);
const differenceLabel = (expected: number, valor: number, money: (value: number) => string) => {
  const delta = Math.abs(Number(valor)) - expected;
  if (!Number.isFinite(delta)) return '';
  if (Math.abs(delta) < 0.005) return 'Valor igual ao previsto';
  return `Diferença ${money(delta)}`;
};

export function paymentDifference(expected: number, valor: number, money: (value: number) => string) {
  const actual = Math.abs(Number(valor));
  const delta = actual - expected;
  if (!Number.isFinite(expected) || !Number.isFinite(actual)) return '';
  if (Math.abs(delta) < 0.005) return 'Valor igual ao previsto.';
  return `Diferença ${money(delta)} em relação ao previsto ${money(expected)}.`;
}

export default function StatementSearch({api, commitment, suggestionIds, money, disabled, onSelect}: {
  api: Api; commitment: Commitment; suggestionIds: string[]; money: (value: number) => string; disabled?: boolean;
  onSelect: (transaction: any) => void;
}) {
  const expected = Number(commitment.amount);
  const due = String(commitment.next_due_date || '').slice(0, 10);
  const initial: Filters = {text: '', from: civilShift(due, -45), to: civilShift(due, 15), amount: '', band: true};
  const [filters, setFilters] = useState<Filters>(initial);
  const [items, setItems] = useState<any[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState('');
  const [activePath, setActivePath] = useState('');
  const [occurrences, setOccurrences] = useState<Record<string, string>>({});
  const generation = useRef(0);
  const queryFor = (next: Filters) => {
    const params = new URLSearchParams();
    const typed = next.text.trim();
    const typedAmount = typed ? parseMoneyInput(typed, {allowZero: false, max: 100_000_000}) : null;
    if (typed && !typedAmount) params.set('q', typed);
    if (typedAmount) params.set('amount', typedAmount);
    else if (next.amount) params.set('amount', next.amount);
    else if (next.band && Number.isFinite(expected) && expected > 0) {
      const band = bandFor(expected);
      params.set('amount_min', band.min);
      params.set('amount_max', band.max);
    }
    if (next.from) params.set('date_from', next.from);
    if (next.to) params.set('date_to', next.to);
    const text = params.toString();
    return text ? `/v1/commitments/${commitment.commitment_id}/statement-search?${text}` : '';
  };
  const run = async (next: Filters) => {
    const current = ++generation.current;
    if (!validDay(next.from) || !validDay(next.to)) {
      Alert.alert('Confira o período', 'Informe as datas como AAAA-MM-DD ou deixe o campo vazio.');
      return;
    }
    if (next.from && next.to && next.from > next.to) {
      Alert.alert('Confira o período', 'A data inicial deve ser anterior à data final.');
      return;
    }
    const path = queryFor(next);
    if (!path) {
      setItems([]);
      setTruncated(false);
      setNote('Informe um nome, um valor ou um período para consultar o extrato.');
      return;
    }
    setLoading(true);
    setNote('');
    try {
      const result = await api(path);
      if (current !== generation.current) return;
      setItems(result.items || []);
      setTruncated(Boolean(result.truncated));
    } catch (error: any) {
      if (current !== generation.current) return;
      setItems([]);
      Alert.alert('Não foi possível consultar o extrato', error.message);
    } finally {
      if (current === generation.current) setLoading(false);
    }
  };
  const searchPath = queryFor(initial);
  if (searchPath !== activePath) {
    setActivePath(searchPath);
    setLoading(Boolean(searchPath));
  } else if (!searchPath && loading) setLoading(false);
  useEffect(() => {
    const current = ++generation.current;
    let active = true;
    if (!searchPath) return undefined;
    void api(searchPath).then(result => {
      if (!active || current !== generation.current) return;
      setItems(result.items || []);
      setTruncated(Boolean(result.truncated));
    }).catch((error: any) => {
      if (active && current === generation.current) Alert.alert('Não foi possível consultar o extrato', error.message);
    }).finally(() => {if (active && current === generation.current) setLoading(false);});
    return () => {active = false;};
  }, [searchPath]);
  const hidden = new Set(suggestionIds);
  const visible = items.filter(item => !hidden.has(String(item.id || item.provisional_id || '')));
  const update = (key: keyof Filters, value: string) => setFilters(current => ({...current, [key]: value}));
  return <View>
    <Text style={s.label}>Nome ou valor</Text>
    <TextInput accessibilityLabel="Nome ou valor do lançamento" value={filters.text} onChangeText={value => update('text', value)} placeholder="Nome do lançamento ou valor" placeholderTextColor="#92A4A2" style={s.input}/>
    <Text style={s.label}>Período inicial</Text>
    <TextInput accessibilityLabel="Período inicial" value={filters.from} onChangeText={value => update('from', value)} placeholder="AAAA-MM-DD" placeholderTextColor="#92A4A2" autoCapitalize="none" style={s.input}/>
    <Text style={s.label}>Período final</Text>
    <TextInput accessibilityLabel="Período final" value={filters.to} onChangeText={value => update('to', value)} placeholder="AAAA-MM-DD" placeholderTextColor="#92A4A2" autoCapitalize="none" style={s.input}/>
    <Text style={s.label}>Valor exato (opcional)</Text>
    <TextInput accessibilityLabel="Valor exato do lançamento" value={filters.amount} onChangeText={value => update('amount', value)} placeholder="Deixe vazio para usar a margem" placeholderTextColor="#92A4A2" keyboardType="decimal-pad" style={s.input}/>
    <Text style={s.help}>A busca consulta o extrato inteiro no servidor, no período e no valor informados. Com o valor vazio, a margem fica em torno do previsto. Limpe o período e a margem para achar qualquer lançamento pelo nome ou pelo valor.</Text>
    <Pressable accessibilityRole="button" accessibilityLabel="Buscar no extrato" disabled={disabled || loading} onPress={() => {
      const typedAmount = filters.amount ? parseMoneyInput(filters.amount, {allowZero: false, max: 100_000_000}) : null;
      if (filters.amount && !typedAmount) {Alert.alert('Confira o valor', 'Informe o valor como 1.234,56.'); return;}
      const next = {...filters, amount: typedAmount || ''};
      setFilters(next);
      return run(next);
    }} style={s.action}><Text style={s.actionText}>{loading ? 'Buscando...' : 'Buscar no extrato'}</Text></Pressable>
    <Pressable accessibilityRole="button" accessibilityLabel="Limpar período e valor" disabled={disabled || loading} onPress={() => {
      const next = {...filters, from: '', to: '', amount: '', band: false};
      setFilters(next);
      return run(next);
    }}><Text style={s.cancel}>Limpar período e valor</Text></Pressable>
    {!!note && <Text style={s.help}>{note}</Text>}
    {truncated && <Text style={s.help}>Há mais lançamentos do que cabem nesta lista. Afine o nome, o período ou o valor.</Text>}
    {!loading && !visible.length && <Text style={s.help}>Nenhum outro lançamento neste filtro.</Text>}
    {visible.map(tx => {
      const key = String(tx.provisional_id || tx.id);
      const title = tx.vendedor || tx.fornecedor || tx.merchant_raw || tx.estabelecimento_original || tx.descricao_original || 'Movimentação';
      const meta = `${dateLabel(String(tx.data_evento || ''))} · ${money(Math.abs(Number(tx.valor)))} · ${tx.source_kind === 'confirmed_capture' ? 'captura' : 'extrato'}${tx.linkable === false ? '' : ` · ${differenceLabel(expected, Number(tx.valor), money)}`}`;
      if (tx.linkable === false) return <View key={key} style={s.payment}><Text style={s.name}>{title}</Text><Text style={s.meta}>{meta}</Text><Text style={s.help}>{tx.block_reason || 'Não disponível para vínculo.'}</Text></View>;
      const chosen = occurrences[key];
      return <View key={key} style={s.payment}>
        {tx.requires_occurrence_selection && <View style={s.choices}>{(tx.occurrence_options || []).map((part: any) => <Pressable accessibilityRole="button" accessibilityLabel={`Liquidar ocorrência ${dateLabel(part.due_date)} da busca`} disabled={disabled || part.due_date !== tx.next_occurrence_due_date} key={part.due_date} onPress={() => setOccurrences(current => ({...current, [key]: part.due_date}))} style={[s.chip, chosen === part.due_date && s.selected]}><Text style={s.chipText}>{dateLabel(part.due_date)}</Text></Pressable>)}</View>}
        <Pressable accessibilityRole="button" disabled={disabled || (tx.requires_occurrence_selection && !chosen)} onPress={() => onSelect({...tx, occurrence_due_date: chosen})}><Text style={s.name}>{title}</Text><Text style={s.meta}>{meta}</Text></Pressable>
      </View>;
    })}
  </View>;
}

const s = StyleSheet.create({
  label: {color: '#AFC1BF', fontSize: 12, marginTop: 12, marginBottom: 5},
  help: {color: '#92A4A2', fontSize: 12, lineHeight: 18, marginBottom: 8},
  input: {backgroundColor: '#18292C', borderRadius: 10, paddingHorizontal: 12, paddingVertical: 11, color: '#F5F8F6', fontSize: 15},
  action: {backgroundColor: '#49C5AA', borderRadius: 10, paddingHorizontal: 13, paddingVertical: 11, alignItems: 'center', marginVertical: 8},
  actionText: {color: '#061412', fontSize: 12, fontWeight: '800'},
  cancel: {color: '#C5D8D4', textAlign: 'center', padding: 10},
  payment: {borderTopWidth: 1, borderColor: '#315054', paddingVertical: 10},
  name: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  meta: {color: '#92A4A2', fontSize: 11, marginTop: 6},
  choices: {flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginBottom: 5},
  chip: {borderWidth: 1, borderColor: '#315054', borderRadius: 18, paddingHorizontal: 11, paddingVertical: 8},
  selected: {backgroundColor: '#49C5AA', borderColor: '#49C5AA'},
  chipText: {color: '#C5D8D4', fontSize: 11, fontWeight: '600'},
});
