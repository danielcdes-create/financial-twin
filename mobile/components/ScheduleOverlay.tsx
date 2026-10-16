import React, {createContext, useContext, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {FlatList, ScrollView, StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';
import {useLocalBack} from '../utils/localBack';

export type ScheduleRow = {
  kind: 'year' | 'row';
  key: string;
  year?: string;
  due?: string;
  label?: string;
  state?: string;
  note?: string;
  link?: boolean;
  statusText?: string;
};
export type ScheduleRequest = {
  title: string;
  rows: ScheduleRow[];
  summary?: string;
  segment?: 'open' | 'paid';
  onSegment?: (segment: 'open' | 'paid') => void;
  onClose: () => void;
  onToggleYear: (year: string) => void;
  onLink: (due: string) => void;
};
export type ActivityMenuItem = {label: string; accessibilityLabel: string; onPress: () => void};
export type ActivityPanel = {
  title: string; onClose: () => void; body: React.ReactNode;
  onEdit?: () => void; menuLabel?: string; menu?: ActivityMenuItem[];
  scrollToken?: string;
  scroll?: boolean;
};
type Host = {show: (request: ScheduleRequest) => void; clear: () => void; setPanel: (panel: ActivityPanel | null) => void};
const Context = createContext<Host | null>(null);

/** Schedule list in the Activity window. A native Modal opens an Android Dialog and the privacy cover flickers until the process dies. */
export function ScheduleOverlayProvider({children}: {children: React.ReactNode}) {
  const [schedule, setSchedule] = useState<ScheduleRequest | null>(null);
  const [panel, setPanel] = useState<ActivityPanel | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const host = useMemo<Host>(() => ({
    show: request => {setPanel(null); setSchedule(request);},
    clear: () => {setSchedule(null); setPanel(null);},
    setPanel: next => {setSchedule(null); setPanel(next);},
  }), []);
  const layerTitle = panel?.title || schedule?.title || '';
  const [menuLayer, setMenuLayer] = useState(layerTitle);
  if (menuLayer !== layerTitle) {
    setMenuLayer(layerTitle);
    setMenuOpen(false);
  }
  useLayoutEffect(() => {
    scroller.current?.scrollTo?.({y: 0, animated: false});
  }, [panel?.scrollToken]);
  const closePanel = panel?.onClose || schedule?.onClose;
  const goBack = () => {if (menuOpen) {setMenuOpen(false); return;} closePanel?.();};
  // Page editors stop at 35. This layer sits above them, so hardware back
  // closes its menu and then the panel before the screen underneath.
  // dispatchLocalBack still consumes the lock, the dialog, and the selector first.
  useLocalBack(goBack, Boolean(panel || schedule), 40);
  return <Context.Provider value={host}><View style={s.root}>
    {children}
    {panel && <View style={s.overlay} accessibilityLabel={panel.title}>
      <View style={s.bar}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Voltar de ${panel.title}`} onPress={goBack} style={s.close}><Text style={s.closeText}>←</Text></Pressable>
        <Text style={s.barTitle} numberOfLines={1}>{panel.title.replace(/^Detalhe de |^Cronograma de /, '')}</Text>
        {!!panel.onEdit && <Pressable accessibilityRole="button" accessibilityLabel={`Editar compromisso ${panel.title.replace(/^Detalhe de /, '')}`} onPress={panel.onEdit} style={s.barAction}><Text style={s.barActionText}>Editar</Text></Pressable>}
        {!!panel.menu?.length && <Pressable accessibilityRole="button" accessibilityLabel={panel.menuLabel || 'Mais ações'} accessibilityState={{expanded: menuOpen}} onPress={() => setMenuOpen(value => !value)} style={s.close}><Text style={s.menuMark}>⋯</Text></Pressable>}
      </View>
      {menuOpen && !!panel.menu?.length && <View style={s.popup} accessibilityLabel="Menu do compromisso">{panel.menu.map(action => <Pressable accessibilityRole="button" accessibilityLabel={action.accessibilityLabel} key={action.accessibilityLabel} onPress={() => {setMenuOpen(false); action.onPress();}} style={s.popupItem}><Text style={s.popupText}>{action.label}</Text></Pressable>)}</View>}
      {panel.scroll === false
        ? <View style={s.body}>{panel.body}</View>
        : <ScrollView ref={scroller} style={s.body} contentContainerStyle={s.bodyContent} keyboardShouldPersistTaps="handled">{panel.body}</ScrollView>}
    </View>}
    {!panel && schedule && <View style={s.overlay} accessibilityLabel={`Cronograma de ${schedule.title}`}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Fechar cronograma de ${schedule.title}`} onPress={schedule.onClose} style={s.close}><Text style={s.closeText}>Fechar</Text></Pressable>
      {!!schedule.summary && <Text style={s.meta} accessibilityLabel="Resumo do cronograma">{schedule.summary}</Text>}
      {!!schedule.onSegment && <View style={s.segment} accessibilityLabel="A pagar ou pagas">
        <Pressable accessibilityRole="button" accessibilityLabel="A pagar" accessibilityState={{selected: schedule.segment !== 'paid'}} onPress={() => schedule.onSegment?.('open')} style={s.link}><Text style={s.linkText}>A pagar</Text></Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Pagas" accessibilityState={{selected: schedule.segment === 'paid'}} onPress={() => schedule.onSegment?.('paid')} style={s.link}><Text style={s.linkText}>Pagas</Text></Pressable>
      </View>}
      <FlatList data={schedule.rows} keyExtractor={row => row.key} initialNumToRender={8} windowSize={5} scrollEnabled style={s.list}
        renderItem={({item}) => item.kind === 'year'
          ? <Pressable accessibilityRole="button" accessibilityLabel={`Ano ${item.year} de ${schedule.title}`} onPress={() => schedule.onToggleYear(String(item.year))}><Text style={s.year}>{item.year}</Text></Pressable>
          : <View style={s.row}>
            <Text style={s.line}>{item.label}</Text>
            {item.statusText ? <Text style={s.meta}>{item.statusText}</Text> : !!item.state && item.state !== 'Previsto' && <Text style={item.state === 'Vencido · sem vínculo' ? s.overdue : s.meta}>{item.state}</Text>}
            {item.link && <Pressable accessibilityRole="button" accessibilityLabel={`Vincular parcela ${item.due} de ${schedule.title}`} onPress={() => schedule.onLink(String(item.due))} style={s.link}><Text style={s.linkText}>Vincular</Text></Pressable>}
            {!!item.note && <Text style={s.meta}>{item.note}</Text>}
          </View>} />
    </View>}
  </View></Context.Provider>;
}

export function useScheduleOverlay() {
  const host = useContext(Context);
  if (!host) throw new Error('O cronograma precisa da camada da Activity.');
  return host;
}

const s = StyleSheet.create({
  root: {flex: 1},
  overlay: {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, zIndex: 40, elevation: 24, backgroundColor: '#102124', paddingHorizontal: 16, paddingBottom: 16},
  body: {flex: 1},
  bodyContent: {paddingBottom: 32},
  bar: {flexDirection: 'row', alignItems: 'center', gap: 8, height: 56},
  barTitle: {color: '#F4F7F5', fontSize: 20, fontWeight: '800', flex: 1},
  barAction: {minHeight: 48, justifyContent: 'center', paddingHorizontal: 8},
  barActionText: {color: '#67DFC3', fontSize: 16, fontWeight: '700'},
  popup: {position: 'absolute', top: 56, right: 16, zIndex: 50, minWidth: 220, backgroundColor: '#183034', borderRadius: 12, borderWidth: 1, borderColor: '#395358', elevation: 8},
  popupItem: {minHeight: 48, justifyContent: 'center', paddingHorizontal: 14},
  popupText: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  close: {minHeight: 48, minWidth: 48, alignItems: 'center', justifyContent: 'center'},
  closeText: {color: '#67DFC3', fontWeight: '700', fontSize: 22},
  menuMark: {color: '#F4F7F5', fontWeight: '700', fontSize: 22},
  list: {flex: 1},
  year: {color: '#C5D5D2', fontSize: 13, fontWeight: '700', marginTop: 12},
  row: {paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: '#263D40'},
  line: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  meta: {color: '#C5D5D2', fontSize: 13, marginTop: 4},
  overdue: {color: '#E7B15A', fontSize: 13, fontWeight: '700', marginTop: 4},
  segment: {flexDirection: 'row', gap: 8},
  link: {minHeight: 48, alignSelf: 'flex-start', justifyContent: 'center', paddingHorizontal: 12, borderRadius: 10, borderWidth: 1, borderColor: '#395358', marginTop: 8},
  linkText: {color: '#D7E5E1', fontWeight: '700'},
});
