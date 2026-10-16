import React from 'react';
import {ScrollView, StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';
import {countPhrase, readableShares} from '../utils/commitmentPresentation';
import {CHART_TRACK, chargeMetaLine, chargeOverdue, monthHref, monthLabel, sortCharges, type ChargeEntry} from '../utils/monthDetail';
import {useLocalBack} from '../utils/localBack';

export type MonthSeries = {key: 'installments' | 'subscriptions' | 'accounts' | 'other'; label: string; color: string};
export type MonthRow = {
  month: string;
  total: string;
  installments: string;
  subscriptions: string;
  accounts?: string;
  other: string;
  estimated: string;
  bill_balance: string;
  items: ChargeEntry[];
};

export default function MonthDetailScreen({row, series, money, asOf, current, periodo, canPrev, canNext, onClose, onPrev, onNext, onOpenCharge, scroll}: {
  row: MonthRow;
  series: readonly MonthSeries[];
  money: (value: number) => string;
  asOf?: string | null;
  current: boolean;
  periodo: string;
  canPrev: boolean;
  canNext: boolean;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onOpenCharge?: (entry: ChargeEntry) => void;
  scroll: boolean;
}) {
  useLocalBack(onClose, true, 30);
  const label = monthLabel(row.month);
  const items = sortCharges(row.items || []);
  const shares = readableShares(series.map(group => ({key: group.key, amount: Number(row[group.key] || 0)})));
  const visible = series.filter(group => Number(row[group.key] || 0) > 0);
  const status = current ? 'Pendente no mês' : 'Previsto no mês';
  const shown = (value: string) => money(Number(value));
  const amountOf = (entry: ChargeEntry) => entry.estimated ? `~ ${shown(entry.amount)}` : shown(entry.amount);
  const body = <>
    <Text style={s.total}>{shown(row.total)}</Text>
    <Text style={s.status}>{`${status} · ${countPhrase(items.length, 'compromisso', 'compromissos')}`}</Text>
    <View style={s.track} accessibilityLabel="Barra do mês">{visible.map(group => {
      const share = shares.find(part => part.key === group.key)?.share || 0;
      return share > 0 ? <View key={group.key} style={{height: 12, width: `${share * 100}%`, backgroundColor: group.color}} /> : null;
    })}</View>
    {visible.map(group => <View key={group.key} style={s.legendLine} accessibilityLabel={`${group.label} ${shown(String(row[group.key] || 0))}`}>
      <View style={[s.dot, {backgroundColor: group.color}]} />
      <Text style={s.legendLabel}>{group.label}</Text>
      <Text style={s.legendAmount}>{shown(String(row[group.key] || 0))}</Text>
    </View>)}
    {Number(row.estimated) > 0 && <Text style={s.note}>{`Inclui ${shown(row.estimated)} de contas variáveis estimadas pela média atual.`}</Text>}
    {Number(row.bill_balance) > 0 && <Text style={s.note}>{`Faturas conhecidas em aberto: ${shown(row.bill_balance)} (fora das barras).`}</Text>}
    <Text style={s.listHeading}>{`Compromissos do mês (${items.length})`}</Text>
    {items.length === 0 && <Text style={s.note}>Nenhum compromisso neste mês.</Text>}
    {items.map((entry, index) => <Pressable key={`${entry.source_ref}:${entry.due_date || index}`} accessibilityRole="button" accessibilityLabel={`Abrir cobrança ${entry.name}`} onPress={() => onOpenCharge?.(entry)} style={s.item}>
      <View style={s.line}><Text style={s.name}>{entry.name}</Text><Text style={s.amount}>{amountOf(entry)}</Text></View>
      <Text style={s.meta}>{chargeMetaLine(entry)}</Text>
      {chargeOverdue(entry, asOf, current) && <Text style={s.badge}>Vencido · sem vínculo</Text>}
    </Pressable>)}
  </>;
  return <View style={s.screen} accessibilityLabel="Tela do mês" accessibilityValue={{text: monthHref(row.month, periodo)}}>
    <View style={s.bar}>
      <Pressable accessibilityRole="button" accessibilityLabel="Voltar à lista de meses" onPress={onClose} style={s.icon}><Text style={s.back}>←</Text></Pressable>
      <Text style={s.title}>{label}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel="Mês anterior" accessibilityState={{disabled: !canPrev}} disabled={!canPrev} onPress={onPrev} style={s.icon}><Text style={[s.step, !canPrev && s.stepOff]}>‹</Text></Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Mês seguinte" accessibilityState={{disabled: !canNext}} disabled={!canNext} onPress={onNext} style={s.icon}><Text style={[s.step, !canNext && s.stepOff]}>›</Text></Pressable>
    </View>
    {scroll ? <ScrollView style={s.scroll} contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">{body}</ScrollView> : <View style={s.body}>{body}</View>}
  </View>;
}

const s = StyleSheet.create({
  screen: {flex: 1, backgroundColor: '#102124'},
  bar: {flexDirection: 'row', alignItems: 'center', height: 56, gap: 4},
  icon: {minWidth: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center'},
  back: {color: '#67DFC3', fontSize: 22, fontWeight: '700'},
  title: {color: '#F4F7F5', fontSize: 20, fontWeight: '800', flex: 1},
  step: {color: '#67DFC3', fontSize: 28, fontWeight: '700'},
  stepOff: {color: '#395358'},
  scroll: {flex: 1},
  body: {paddingBottom: 32},
  total: {color: '#F4F7F5', fontSize: 28, fontWeight: '800'},
  status: {color: '#C5D5D2', fontSize: 14, marginTop: 4, marginBottom: 12},
  track: {height: 12, flexDirection: 'row', backgroundColor: CHART_TRACK, borderRadius: 4, overflow: 'hidden'},
  legendLine: {flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 32, marginTop: 6},
  dot: {width: 8, height: 8, borderRadius: 4},
  legendLabel: {color: '#D7E5E1', fontSize: 14, flex: 1},
  legendAmount: {color: '#F4F7F5', fontSize: 14, fontWeight: '700'},
  note: {color: '#92A4A2', fontSize: 13, lineHeight: 18, marginTop: 8},
  listHeading: {color: '#F4F7F5', fontSize: 16, fontWeight: '800', marginTop: 20, marginBottom: 4},
  item: {paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#263D40', minHeight: 56},
  line: {flexDirection: 'row', justifyContent: 'space-between', gap: 8},
  name: {color: '#F4F7F5', fontSize: 15, fontWeight: '700', flex: 1},
  amount: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  meta: {color: '#92A4A2', fontSize: 13, marginTop: 2},
  badge: {color: '#E7B15A', fontSize: 12, fontWeight: '700', marginTop: 4},
});
