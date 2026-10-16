import React, {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {Platform, ScrollView, StyleSheet, Text, useWindowDimensions, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {alignProjectionSeries, projectionFigures, readableShares} from '../utils/commitmentPresentation';
import {recordChartOffset, restoreChartOffset, showMonthAtTop, takeChartOffset} from '../utils/chartScroll';
import {CHART_TRACK, chartSeries, monthHref, monthLabel, periodoFromMonths, type ChargeEntry} from '../utils/monthDetail';
import MonthDetailScreen from './MonthDetailScreen';

type Month = {month: string; total: string; installments: string; subscriptions: string; accounts?: string; other: string; estimated: string; bill_balance: string; items: ChargeEntry[]};
type Projection = {items: Month[]; months: number; start_month: string; end_month: string; last_installment_month: string | null; warnings: string[]; total: string; as_of: string; automatic_horizon_capped: boolean; summary?: {this_month: string; next_12: string | null; overdue?: string; upcoming?: string; installments: string; subscriptions: string; other: string; bill_balance: string; window_months: number} | null};
type Frame = {summary: React.ReactNode; chart: React.ReactNode; monthOpen: boolean; bindCharge: (opener: (entry: ChargeEntry) => void) => void};

export default function CommitmentProjection({api, money, refreshKey = 0, renderFrame, initialMonth = null, initialMonths, urlDriven = false}: {
  api: (path: string) => Promise<any>;
  money: (value: number) => string;
  refreshKey?: number;
  renderFrame?: (parts: Frame) => React.ReactNode;
  initialMonth?: string | null;
  initialMonths?: number | null;
  urlDriven?: boolean;
}) {
  const [months, setMonths] = useState<number | null>(urlDriven && initialMonths !== undefined ? initialMonths : null);
  const [draft, setDraft] = useState('');
  const [data, setData] = useState<Projection | null>(null);
  const [settledKey, setSettledKey] = useState('');
  const [error, setError] = useState('');
  const [validation, setValidation] = useState('');
  const [selected, setSelected] = useState<string | null>(urlDriven ? initialMonth : null);
  const [highlighted, setHighlighted] = useState<string | null>(urlDriven ? initialMonth : null);
  const [listOffset, setListOffset] = useState(0);
  const [retry, setRetry] = useState(0);
  const [customOpen, setCustomOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const narrow = useWindowDimensions().width < 960;
  const generation = useRef(0);
  const openCharge = useRef<(entry: ChargeEntry) => void>(() => {});
  const listRef = useRef<ScrollView>(null);
  const restorePending = useRef(false);
  const bindCharge = useCallback((opener: (entry: ChargeEntry) => void) => {openCharge.current = opener;}, []);
  const queryKey = `${months ?? 'auto'}:${refreshKey}:${retry}`;
  const loading = settledKey !== queryKey;
  const visibleError = loading ? '' : error;
  const periodo = periodoFromMonths(months);
  useLayoutEffect(() => {const counter = generation; return () => {counter.current++;};}, [api, months, refreshKey, retry]);
  useEffect(() => {
    if (urlDriven || Platform.OS !== 'web' || typeof window === 'undefined') return;
    const onPop = () => setSelected(null);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [urlDriven]);
  useEffect(() => () => {
    if (!restorePending.current) return;
    restorePending.current = false;
    restoreChartOffset();
  }, []);
  useEffect(() => {
    const current = ++generation.current;
    api(`/v1/commitment-projection${months === null ? '' : `?months=${months}`}`).then(response => {
      if (current !== generation.current) return;
      if (!Array.isArray(response.items) || !response.items.length || !Number.isInteger(response.months)) throw Error('O servidor não confirmou a projeção mensal.');
      response.items = alignProjectionSeries(response.items);
      if (response.items.some((row: Month) => !Array.isArray(row.items) || ![row.total, row.installments, row.subscriptions, row.accounts, row.other, row.bill_balance].every(value => Number.isFinite(Number(value))))) throw Error('O servidor não confirmou a projeção mensal.');
      setError('');
      setData(response);
      setSelected(old => old && response.items.some((row: Month) => row.month === old) ? old : urlDriven ? old : null);
    }).catch(failure => {if (current === generation.current) setError(failure.message || 'Não foi possível carregar a projeção.');})
      .finally(() => {if (current === generation.current) setSettledKey(queryKey);});
  }, [api, months, refreshKey, retry, queryKey, urlDriven]);
  useLayoutEffect(() => {
    if (!selected || !narrow) return;
    showMonthAtTop();
  }, [selected, narrow]);
  useLayoutEffect(() => {
    if (selected || !restorePending.current) return;
    restorePending.current = false;
    listRef.current?.scrollTo?.({y: listOffset, animated: false});
    restoreChartOffset();
  }, [selected, listOffset]);
  const choose = (value: number | null) => {setValidation(''); setDraft(value === null ? '' : String(value)); setMonths(value);};
  const apply = () => {if (!/^\d+$/.test(draft) || Number(draft) < 1 || Number(draft) > 120) {setValidation('Escolha de 1 a 120 meses.'); return;} choose(Number(draft));};
  const publish = (month: string | null, mode: 'push' | 'replace' | 'close') => {
    if (urlDriven) {
      const {router} = require('expo-router') as {router: {push: (href: string) => void; replace: (href: string) => void; back: () => void; canGoBack: () => boolean}};
      if (mode === 'close') {if (router.canGoBack()) router.back(); else router.replace('/'); return;}
      const href = monthHref(month || '', periodoFromMonths(months));
      if (mode === 'replace') router.replace(href); else router.push(href);
      return;
    }
    if (Platform.OS !== 'web' || typeof window === 'undefined') return;
    if (mode === 'close') {
      if (window.history.state && (window.history.state as {monthScreen?: boolean}).monthScreen) window.history.back();
      return;
    }
    const href = monthHref(month || '', periodoFromMonths(months));
    if (mode === 'replace') window.history.replaceState({monthScreen: true}, '', href);
    else window.history.pushState({monthScreen: true}, '', href);
  };
  const openMonth = (month: string) => {
    restorePending.current = true;
    setListOffset(takeChartOffset());
    if (narrow) showMonthAtTop();
    setHighlighted(month);
    setSelected(month);
    publish(month, 'push');
  };
  const shiftMonth = (month: string) => {
    setHighlighted(month);
    setSelected(month);
    publish(month, 'replace');
  };
  const closeMonth = () => {
    publish(null, 'close');
    setSelected(null);
  };
  const activeIndex = data?.items.findIndex(row => row.month === selected) ?? -1;
  const active = activeIndex >= 0 && data ? data.items[activeIndex] : undefined;
  const detail = active && data ? <MonthDetailScreen row={active} series={chartSeries} money={money} asOf={data.as_of} current={active.month === data.items[0]?.month} periodo={periodo} canPrev={activeIndex > 0} canNext={activeIndex < data.items.length - 1} onClose={closeMonth} onPrev={() => activeIndex > 0 && shiftMonth(data.items[activeIndex - 1].month)} onNext={() => activeIndex >= 0 && activeIndex < data.items.length - 1 && shiftMonth(data.items[activeIndex + 1].month)} onOpenCharge={entry => openCharge.current(entry)} scroll={urlDriven || !narrow} /> : null;
  const figures = data ? projectionFigures(data) : null;
  const overdueAmount = Number(figures?.this_month_overdue || 0), upcomingAmount = Number(figures?.this_month_upcoming || 0);
  const split = figures && (overdueAmount > 0 || upcomingAmount > 0) ? <Text style={s.split}>{overdueAmount > 0 ? <Text style={s.amber}>{`Vencidas ${money(overdueAmount)}`}</Text> : null}{overdueAmount > 0 && upcomingAmount > 0 ? ' · ' : ''}{upcomingAmount > 0 ? `A vencer ${money(upcomingAmount)}` : ''}</Text> : null;
  const summaryNode = figures ? <View style={s.summary}><View style={s.summaryCard}><Text style={s.summaryLabel}>Falta pagar este mês</Text><Text style={s.summaryValue}>{money(Number(figures.this_month))}</Text>{split}</View><View style={s.summaryCard}><Text style={s.summaryLabel}>Próximos 12 meses</Text><Text style={s.summaryValue}>{figures.next_12 == null ? 'Toque em 12 meses para ver este horizonte.' : money(Number(figures.next_12))}</Text></View></View> : null;
  const monthList = data ? <ScrollView ref={listRef} accessibilityLabel="Lista de meses" onScroll={event => recordChartOffset(event.nativeEvent.contentOffset.y)} scrollEventThrottle={16} contentOffset={{x: 0, y: listOffset}} style={narrow ? undefined : s.chartColumn}>
    {data.items.map((row, index) => {
      const shares = readableShares(chartSeries.map(group => ({key: group.key, amount: Number(row[group.key] || 0)})));
      const pending = index === 0;
      return <Pressable key={row.month} accessibilityRole="button" accessibilityLabel={`${monthLabel(row.month)}: ${money(Number(row.total))} ${pending ? 'pendente no mês' : 'previstos'}`} accessibilityState={{selected: highlighted === row.month}} onPress={() => openMonth(row.month)} style={[s.row, highlighted === row.month && s.selected]}>
        <View style={s.line}><Text style={s.text}>{monthLabel(row.month)}{pending ? ' · pendente no mês' : ''}</Text><Text style={s.amount}>{money(Number(row.total))}</Text></View>
        <View style={s.track}>{chartSeries.map(group => {const share = shares.find(part => part.key === group.key)?.share || 0; return share > 0 ? <View key={group.key} style={{height: 12, width: `${share * 100}%`, backgroundColor: group.color}} /> : null;})}</View>
      </Pressable>;
    })}
  </ScrollView> : null;
  const showChart = !detail || !narrow;
  const chart = <View style={s.card}>
    {showChart && <>
      <Text style={s.heading}>Compromissos por mês</Text>
      <Text style={s.help}>Quanto já está previsto para pagar, antes de novas compras. Toque em um mês para abrir o detalhe.</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} accessibilityLabel="Período do gráfico" contentContainerStyle={s.choices}>{[{label: '6 meses', value: 6}, {label: '12 meses', value: 12}, {label: '24 meses', value: 24}, {label: 'Tudo', value: null}].map(option => <Pressable key={option.label} accessibilityRole="button" accessibilityState={{selected: months === option.value}} onPress={() => {setCustomOpen(false); choose(option.value);}} style={[s.chip, months === option.value && s.chosen]}><Text style={s.text}>{option.label}</Text></Pressable>)}
        <Pressable accessibilityRole="button" accessibilityState={{selected: months !== null && ![6, 12, 24].includes(months)}} onPress={() => setCustomOpen(true)} style={[s.chip, months !== null && ![6, 12, 24].includes(months) && s.chosen]}><Text style={s.text}>Personalizado</Text></Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Como ler o gráfico" onPress={() => setHelpOpen(value => !value)} style={s.chip}><Text style={s.text}>ⓘ</Text></Pressable>
      </ScrollView>
      {customOpen && <View accessibilityLabel="Período personalizado" style={s.customSheet}><Text style={s.label}>Quantidade de meses (1 a 120)</Text><View style={s.custom}><TextInput accessibilityLabel="Quantidade de meses do gráfico" value={draft} placeholder="Ex.: 16" placeholderTextColor="#92A4A2" onChangeText={setDraft} keyboardType="number-pad" maxLength={3} style={s.input} /><Pressable accessibilityRole="button" onPress={() => {apply(); if (/^\d+$/.test(draft) && Number(draft) >= 1 && Number(draft) <= 120) setCustomOpen(false);}} style={s.chip}><Text style={s.text}>Aplicar período</Text></Pressable></View></View>}
      {!!validation && <Text accessibilityRole="alert" style={s.error}>{validation}</Text>}
      {loading && <Text style={s.help}>Atualizando gráfico…</Text>}
      {!!visibleError && <View><Text accessibilityRole="alert" style={s.error}>{visibleError}{data ? ' O gráfico abaixo conserva a última leitura.' : ''}</Text><Pressable accessibilityRole="button" onPress={() => setRetry(value => value + 1)} style={s.chip}><Text style={s.text}>Atualizar gráfico</Text></Pressable></View>}
      {data && <>
        {!renderFrame && summaryNode}
        <Text style={s.label}>Exibindo {monthLabel(data.start_month)} a {monthLabel(data.end_month)} · {data.months} meses</Text>
        <Text style={s.help}>{data.last_installment_month ? `Última parcela prevista: ${monthLabel(data.last_installment_month)}.` : 'Sem parcelas futuras; período automático de 12 meses.'} {data.automatic_horizon_capped ? 'O período automático foi limitado a 120 meses.' : ''}</Text>
        <View style={s.legend}>{(chartSeries.filter(group => data.items.some(row => Number(row[group.key] || 0) > 0))).map(group => <View key={group.key} style={s.legendItem}><View style={[s.dot, {backgroundColor: group.color}]} /><Text style={s.help}>{group.label}</Text></View>)}</View>
        <View style={!narrow && detail ? s.beside : undefined}>
          {monthList}
          {!narrow && detail}
        </View>
        {helpOpen && <Text style={s.help}>Barras: parcelas, assinaturas, contas variáveis e dívidas, com a mesma classificação da lista. Uma série zerada não aparece. Faturas aparecem à parte para evitar somar suas compras duas vezes. A barra do mês atual é o pendente com vencimento nesse mês. Atrasos de meses anteriores entram em Falta pagar este mês e em A conciliar, e não nas barras seguintes. Meses sem cobranças aparecem com zero. Contas variáveis usam a média atual em todo o período; renovações e novos gastos podem mudar os valores.</Text>}
        {data.warnings.map(message => <Text key={message} accessibilityRole="alert" style={s.error}>Conferir: {message}</Text>)}
      </>}
    </>}
    {narrow && detail}
  </View>;
  if (renderFrame) return renderFrame({summary: summaryNode, chart, monthOpen: Boolean(detail) && narrow, bindCharge});
  return chart;
}

const s = StyleSheet.create({
  card: {backgroundColor: '#142326', borderColor: '#263D40', borderWidth: 1, borderRadius: 14, padding: 14, marginVertical: 12},
  heading: {color: '#F4F7F5', fontSize: 17, fontWeight: '700'}, help: {color: '#92A4A2', fontSize: 12, lineHeight: 18, marginVertical: 4}, label: {color: '#D7E5E1', fontSize: 13, marginTop: 10, marginBottom: 5},
  choices: {flexDirection: 'row', gap: 6}, chip: {borderWidth: 1, borderColor: '#395358', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, minHeight: 48, justifyContent: 'center'}, chosen: {backgroundColor: '#244842', borderColor: '#49C5AA'}, text: {color: '#D7E5E1', fontSize: 13},
  customSheet: {backgroundColor: '#102124', borderRadius: 12, padding: 12, marginTop: 8},
  summary: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginVertical: 8}, summaryCard: {flexGrow: 1, minWidth: 140, backgroundColor: '#10282A', borderRadius: 10, padding: 12}, summaryLabel: {color: '#C5D5D2', fontSize: 13}, summaryValue: {color: '#F4F7F5', fontSize: 18, fontWeight: '800', marginTop: 4}, split: {color: '#C5D5D2', fontSize: 12, marginTop: 4}, amber: {color: '#E7B15A', fontSize: 12},
  custom: {flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap'}, input: {color: '#F4F7F5', borderColor: '#395358', borderWidth: 1, borderRadius: 8, minHeight: 48, width: 70, paddingHorizontal: 10},
  legend: {flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginVertical: 6}, legendItem: {flexDirection: 'row', alignItems: 'center', gap: 5}, dot: {width: 8, height: 8, borderRadius: 4},
  beside: {flexDirection: 'row', alignItems: 'flex-start'}, chartColumn: {flex: 1, minWidth: 0, maxHeight: 640},
  row: {paddingHorizontal: 8, height: 108, minHeight: 48, justifyContent: 'center', borderRadius: 8, borderWidth: 1, borderColor: 'transparent', marginVertical: 2}, selected: {backgroundColor: '#1A3335', borderColor: '#49C5AA'}, line: {flexDirection: 'row', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start'}, amount: {color: '#F4F7F5', fontSize: 13, fontWeight: '700', flexShrink: 0}, track: {height: 12, flexDirection: 'row', backgroundColor: CHART_TRACK, borderRadius: 4, overflow: 'hidden', marginTop: 8},
  error: {color: '#FF8C83', fontSize: 12, lineHeight: 18, marginVertical: 5},
});
