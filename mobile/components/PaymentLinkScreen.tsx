import React, {useEffect, useRef, useState} from 'react';
import {ScrollView, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {financialDateLabel} from '../utils/classification';
import {maskCivilDate, parseCivilDate} from '../utils/formMask';
import {parseMoneyInput} from '../utils/moneyInput';
import {amountWords, suggestionWindow} from '../utils/commitmentPresentation';
import {Alert} from '../utils/appAlert';

type Api = (path: string, init?: RequestInit) => Promise<any>;
type Commitment = {commitment_id: string; name?: string; amount?: number | string; revision?: number};

const dateLabel = financialDateLabel;
const shortDay = (iso: string) => {
  const day = String(iso || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? `${day.slice(8, 10)}/${day.slice(5, 7)}` : '';
};
const bandMargin = (amount: number) => Math.max(5, amount * 0.25);

export default function PaymentLinkScreen({api, commitment, due, amount, stateLabel, money, preferredId, disabled, lookup = false, canConfirm = true, onConfirm}: {
  api: Api;
  commitment: Commitment;
  due: string;
  amount: number;
  stateLabel: string;
  money: (value: number) => string;
  preferredId?: string;
  disabled?: boolean;
  /** Imported charge: search the statement without a managed commitment id. */
  lookup?: boolean;
  canConfirm?: boolean;
  onConfirm: (transaction: any) => void;
}) {
  const window = suggestionWindow(due);
  const [text, setText] = useState('');
  const [from, setFrom] = useState(window?.from || '');
  const [to, setTo] = useState(window?.to || '');
  const [usePeriod, setUsePeriod] = useState(true);
  const [margin, setMargin] = useState(bandMargin(amount).toFixed(2));
  const [useBand, setUseBand] = useState(true);
  const [sheet, setSheet] = useState<'period' | 'amount' | null>(null);
  const [draftFrom, setDraftFrom] = useState('');
  const [draftTo, setDraftTo] = useState('');
  const [draftMargin, setDraftMargin] = useState('');
  const [help, setHelp] = useState(false);
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(preferredId || null);
  const [revision, setRevision] = useState<number | null>(null);
  const [occurrences, setOccurrences] = useState<Record<string, string>>({});
  const generation = useRef(0);
  const typedAmount = (value: string) => parseMoneyInput(value, {allowZero: false, max: 100_000_000});
  const queryFor = (nextText: string, period: boolean, band: boolean, start: string, end: string, marginText: string) => {
    const params = new URLSearchParams();
    const typed = nextText.trim();
    const asAmount = typed ? typedAmount(typed) : null;
    if (typed && !asAmount) params.set('q', typed);
    if (asAmount) params.set('amount', asAmount);
    else if (band && Number.isFinite(amount) && amount > 0) {
      const width = Number(marginText) || bandMargin(amount);
      params.set('amount_min', Math.max(0.01, amount - width).toFixed(2));
      params.set('amount_max', (amount + width).toFixed(2));
    }
    if (period && !typed) {
      if (start) params.set('date_from', start);
      if (end) params.set('date_to', end);
    }
    params.set('rank', 'relevance');
    if (due) params.set('due', due.slice(0, 10));
    if (Number.isFinite(amount) && amount > 0) params.set('expected', amount.toFixed(2));
    if (lookup) params.set('name', commitment.name || '');
    const path = lookup ? '/v1/statement-search' : `/v1/commitments/${commitment.commitment_id}/statement-search`;
    return `${path}?${params.toString()}`;
  };
  const run = async (nextText: string, period = usePeriod, band = useBand, start = from, end = to, marginText = margin) => {
    const current = ++generation.current;
    setLoading(true);
    try {
      const result = await api(queryFor(nextText, period, band, start, end, marginText));
      if (current !== generation.current) return;
      const found = result.items || [];
      setItems(found);
      if (result.commitment_revision) setRevision(Number(result.commitment_revision));
      setSelected(currentId => {
        if (currentId && found.some((row: any) => String(row.id || row.provisional_id) === currentId && row.linkable !== false)) return currentId;
        const preferred = preferredId && found.find((row: any) => String(row.id || row.provisional_id) === preferredId && row.linkable !== false);
        return preferred ? String(preferred.id || preferred.provisional_id) : null;
      });
    } catch (error: any) {
      if (current !== generation.current) return;
      setItems([]);
      Alert.alert('Não foi possível consultar o extrato', error.message);
    } finally {
      if (current === generation.current) setLoading(false);
    }
  };
  useEffect(() => {
    const timer = setTimeout(() => {void run(text);}, text ? 300 : 0);
    return () => clearTimeout(timer);
  }, [text, commitment.commitment_id, due]);
  const chosen = items.find(row => String(row.id || row.provisional_id) === selected && row.linkable !== false);
  const occurrence = chosen ? occurrences[String(chosen.id || chosen.provisional_id)] : '';
  const needsOccurrence = Boolean(chosen?.requires_occurrence_selection && !occurrence);
  const words = chosen ? amountWords(amount, Number(chosen.valor), money) : '';
  const large = chosen && amount > 0 && Math.abs(Math.abs(Number(chosen.valor)) - amount) / amount > 0.2;
  const titleOf = (row: any) => row.vendedor || row.fornecedor || row.merchant_raw || row.estabelecimento_original || row.descricao_original || 'Movimentação';
  return <View style={s.root}>
    <ScrollView style={s.list} keyboardShouldPersistTaps="handled" contentContainerStyle={s.listContent}>
      <View style={s.context}>
        <View style={s.contextTop}>
          <Text style={s.contextName}>{commitment.name || 'Compromisso'}</Text>
          <Text style={s.contextAmount}>{money(amount)}</Text>
        </View>
        <Text style={s.contextMeta}>{`Parcela de ${dateLabel(due)}`}</Text>
        {!!stateLabel && <Text style={stateLabel.startsWith('Vencid') ? s.overdue : s.contextMeta}>{stateLabel}</Text>}
      </View>
      <View style={s.searchRow}>
        <TextInput accessibilityLabel="Buscar por nome ou valor" value={text} onChangeText={setText} placeholder="Buscar por nome ou valor" placeholderTextColor="#92A4A2" style={s.search}/>
        <Pressable accessibilityRole="button" accessibilityLabel="Como funciona a busca" onPress={() => setHelp(value => !value)} style={s.info}><Text style={s.infoText}>ⓘ</Text></Pressable>
      </View>
      {help && <Text style={s.help}>A busca consulta o extrato inteiro. Um nome ignora o período. Um número filtra pelo valor. A margem e o período padrão ficam nos filtros.</Text>}
      <View style={s.chips}>
        <Pressable accessibilityRole="button" accessibilityLabel="Período do vínculo" onPress={() => {setDraftFrom(from ? maskCivilDate(from.split('-').reverse().join('')) : ''); setDraftTo(to ? maskCivilDate(to.split('-').reverse().join('')) : ''); setSheet('period');}} style={s.chip}><Text style={s.chipText}>{usePeriod && from && to ? `Período: ${shortDay(from)} – ${shortDay(to)} ▾` : 'Período: qualquer ▾'}</Text></Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Valor do vínculo" onPress={() => {setDraftMargin(margin); setSheet('amount');}} style={s.chip}><Text style={s.chipText}>{useBand ? `Valor: ± ${money(Number(margin))} ▾` : 'Valor: qualquer ▾'}</Text></Pressable>
      </View>
      {sheet === 'period' && <View style={s.sheet}>
        <Text style={s.help}>Datas em dd/mm/aaaa.</Text>
        <TextInput accessibilityLabel="Início do período" value={draftFrom} onChangeText={value => setDraftFrom(maskCivilDate(value))} placeholder="dd/mm/aaaa" placeholderTextColor="#92A4A2" keyboardType="number-pad" style={s.search}/>
        <TextInput accessibilityLabel="Fim do período" value={draftTo} onChangeText={value => setDraftTo(maskCivilDate(value))} placeholder="dd/mm/aaaa" placeholderTextColor="#92A4A2" keyboardType="number-pad" style={s.search}/>
        <View style={s.sheetActions}>
          <Pressable accessibilityRole="button" accessibilityLabel="Limpar período" onPress={() => {setUsePeriod(false); setSheet(null); void run(text, false, useBand, from, to, margin);}} style={s.secondary}><Text style={s.secondaryText}>Limpar</Text></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Aplicar período" onPress={() => {
            const start = parseCivilDate(draftFrom);
            const end = parseCivilDate(draftTo);
            if (!start || !end || start > end) {Alert.alert('Confira o período', 'Informe o início e o fim como dd/mm/aaaa.'); return;}
            setFrom(start); setTo(end); setUsePeriod(true); setSheet(null);
            void run(text, true, useBand, start, end, margin);
          }} style={s.apply}><Text style={s.applyText}>Aplicar</Text></Pressable>
        </View>
      </View>}
      {sheet === 'amount' && <View style={s.sheet}>
        <Text style={s.help}>Margem em torno do valor previsto.</Text>
        <TextInput accessibilityLabel="Margem do valor" value={draftMargin} onChangeText={setDraftMargin} placeholder="0,00" placeholderTextColor="#92A4A2" keyboardType="decimal-pad" style={s.search}/>
        <View style={s.sheetActions}>
          <Pressable accessibilityRole="button" accessibilityLabel="Limpar valor" onPress={() => {setUseBand(false); setSheet(null); void run(text, usePeriod, false, from, to, margin);}} style={s.secondary}><Text style={s.secondaryText}>Limpar</Text></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Aplicar valor" onPress={() => {
            const parsed = typedAmount(draftMargin.includes(',') ? draftMargin : draftMargin.replace('.', ','));
            const width = parsed || (Number(draftMargin) > 0 ? Number(draftMargin).toFixed(2) : '');
            if (!width) {Alert.alert('Confira a margem', 'Informe a margem como 40,00.'); return;}
            setMargin(width); setUseBand(true); setSheet(null);
            void run(text, usePeriod, true, from, to, width);
          }} style={s.apply}><Text style={s.applyText}>Aplicar</Text></Pressable>
        </View>
      </View>}
      <Text style={s.section}>{loading ? 'Buscando lançamentos…' : 'Mais prováveis'}</Text>
      {!loading && !items.length && <Text style={s.help}>Nenhum lançamento encontrado. Ajuste o período ou o valor.</Text>}
      {items.map(row => {
        const key = String(row.id || row.provisional_id);
        const title = titleOf(row);
        const source = row.source_kind === 'confirmed_capture' ? 'Captura' : 'Extrato';
        const wordsForRow = row.linkable === false ? '' : amountWords(amount, Number(row.valor), money);
        const active = selected === key;
        return <Pressable key={key} accessibilityRole="button" accessibilityState={{selected: active, disabled: row.linkable === false}} disabled={disabled || row.linkable === false} onPress={() => setSelected(key)} style={[s.row, active && s.rowOn]}>
          <View style={s.rowTop}>
            <Text style={s.rowName}>{title}</Text>
            <Text style={s.rowAmount}>{money(Math.abs(Number(row.valor)))}</Text>
          </View>
          <Text style={s.rowMeta}>{`${dateLabel(String(row.data_evento || ''))} · ${source}${wordsForRow ? ` · ${wordsForRow}` : ''}`}</Text>
          {row.linkable === false && <Text style={s.blocked}>{row.block_reason || 'Já vinculado.'}</Text>}
          {active && row.requires_occurrence_selection && <View style={s.choices}>{(row.occurrence_options || []).map((part: any) => <Pressable key={part.due_date} accessibilityRole="button" accessibilityLabel={`Liquidar ocorrência ${dateLabel(part.due_date)}`} disabled={disabled || part.due_date !== row.next_occurrence_due_date} onPress={() => setOccurrences(current => ({...current, [key]: part.due_date}))} style={[s.occurrence, occurrences[key] === part.due_date && s.occurrenceOn]}><Text style={s.chipText}>{dateLabel(part.due_date)}</Text></Pressable>)}</View>}
        </Pressable>;
      })}
    </ScrollView>
    <View style={s.footer}>
      <Text style={s.footerText}>{chosen ? `${titleOf(chosen)} · ${money(Math.abs(Number(chosen.valor)))} · ${words}` : 'Selecione um lançamento'}</Text>
      {!!large && <Text style={s.warning}>Diferença acima de 20% do previsto.</Text>}
      {canConfirm ? <Pressable accessibilityRole="button" accessibilityLabel="Vincular este lançamento" disabled={disabled || !chosen || needsOccurrence} onPress={() => onConfirm({...chosen, occurrence_due_date: occurrence || undefined, commitment_revision: revision || chosen.commitment_revision})} style={[s.confirm, (!chosen || needsOccurrence) && s.confirmOff]}><Text style={s.confirmText}>Vincular este lançamento</Text></Pressable> : <Text style={s.help}>Estes são os lançamentos mais próximos. O vínculo de uma cobrança importada não grava no plano.</Text>}
    </View>
  </View>;
}

const s = StyleSheet.create({
  root: {flex: 1},
  list: {flex: 1},
  listContent: {paddingBottom: 16},
  context: {backgroundColor: '#142326', borderRadius: 16, padding: 14, marginTop: 8},
  contextTop: {flexDirection: 'row', justifyContent: 'space-between', gap: 8},
  contextName: {color: '#F4F7F5', fontSize: 16, fontWeight: '800', flex: 1},
  contextAmount: {color: '#F4F7F5', fontSize: 16, fontWeight: '800'},
  contextMeta: {color: '#C5D5D2', fontSize: 13, marginTop: 4},
  overdue: {color: '#E7B15A', fontSize: 13, fontWeight: '700', marginTop: 4},
  searchRow: {flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 12},
  search: {flex: 1, backgroundColor: '#18292C', borderRadius: 12, minHeight: 48, paddingHorizontal: 12, color: '#F4F7F5', fontSize: 15},
  info: {minWidth: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center'},
  infoText: {color: '#67DFC3', fontSize: 18, fontWeight: '700'},
  help: {color: '#C5D5D2', fontSize: 13, lineHeight: 18, marginTop: 8},
  chips: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10},
  chip: {minHeight: 40, borderRadius: 18, borderWidth: 1, borderColor: '#395358', paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center'},
  chipText: {color: '#D7E5E1', fontSize: 13, fontWeight: '700'},
  sheet: {backgroundColor: '#142326', borderRadius: 16, padding: 12, marginTop: 10},
  sheetActions: {flexDirection: 'row', gap: 8, marginTop: 10},
  secondary: {flex: 1, minHeight: 48, borderRadius: 12, borderWidth: 1, borderColor: '#395358', alignItems: 'center', justifyContent: 'center'},
  secondaryText: {color: '#D7E5E1', fontWeight: '700'},
  apply: {flex: 1, minHeight: 48, borderRadius: 12, backgroundColor: '#49C5AA', alignItems: 'center', justifyContent: 'center'},
  applyText: {color: '#061412', fontWeight: '800'},
  section: {color: '#F4F7F5', fontSize: 15, fontWeight: '800', marginTop: 16, marginBottom: 6},
  row: {borderRadius: 14, paddingVertical: 10, paddingHorizontal: 12, marginBottom: 8, borderWidth: 1, borderColor: 'transparent'},
  rowOn: {borderColor: '#49C5AA', backgroundColor: '#142326'},
  rowTop: {flexDirection: 'row', justifyContent: 'space-between', gap: 8},
  rowName: {color: '#F4F7F5', fontSize: 15, fontWeight: '700', flex: 1},
  rowAmount: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  rowMeta: {color: '#C5D5D2', fontSize: 13, marginTop: 4},
  blocked: {color: '#8FA29E', fontSize: 12, marginTop: 4},
  warning: {color: '#E7B15A', fontSize: 13, fontWeight: '700', marginBottom: 6},
  choices: {flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8},
  occurrence: {minHeight: 36, borderRadius: 14, borderWidth: 1, borderColor: '#395358', paddingHorizontal: 10, alignItems: 'center', justifyContent: 'center'},
  occurrenceOn: {backgroundColor: '#244842', borderColor: '#49C5AA'},
  footer: {borderTopWidth: 1, borderTopColor: '#263D40', paddingTop: 10, paddingBottom: 8, backgroundColor: '#102124'},
  footerText: {color: '#F4F7F5', fontSize: 14, fontWeight: '700', marginBottom: 8},
  confirm: {minHeight: 48, borderRadius: 12, backgroundColor: '#49C5AA', alignItems: 'center', justifyContent: 'center'},
  confirmOff: {opacity: 0.45},
  confirmText: {color: '#061412', fontSize: 16, fontWeight: '800'},
});
