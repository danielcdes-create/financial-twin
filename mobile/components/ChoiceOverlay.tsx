import React, {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react';
import {AppState, BackHandler, FlatList, Keyboard, Platform, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {SafeAreaView} from 'react-native-safe-area-context';
import {readableText} from '../utils/responsiveLayout';
import {isUserInteractionBlocked, subscribeUserOperation, userOperationSnapshot} from '../utils/userOperation';
import {setChoiceBack} from '../utils/localBack';

type ChoiceRequest = {owner: string; label: string; value: string; options: {value: string; label: string}[];
  allowClear: boolean; onSelect: (value: string) => void | Promise<unknown>};
type ChoiceOverlay = {open: (request: ChoiceRequest) => void; close: (owner: string) => void};
const Context = createContext<ChoiceOverlay | null>(null);
const normalized = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');

// One overlay in the Activity's existing window. A native Modal creates a Dialog
// and transfers Android focus, triggering the application's privacy suspension.
export function ChoiceOverlayProvider({children}: {children: React.ReactNode}) {
  const [choice, setChoice] = useState<ChoiceRequest | null>(null), [query, setQuery] = useState('');
  const choiceRef = useRef<ChoiceRequest | null>(null);
  const backgroundRef = useRef<View>(null), panelRef = useRef<View>(null), closeRef = useRef<View>(null);
  const dismiss = useCallback(() => {choiceRef.current = null;setChoiceBack(null); setChoice(null); setQuery(''); Keyboard.dismiss();}, []);
  const open = useCallback((request: ChoiceRequest) => {
    if (Platform.OS !== 'web' && (AppState.currentState === 'background' || AppState.currentState === 'inactive')) return;
    if (Platform.OS === 'web' && document.visibilityState === 'hidden') return;
    // Release the editor's keyboard before showing choices. Search is opt-in:
    // automatically focusing it resizes the Android window while this opens.
    Keyboard.dismiss();
    choiceRef.current = request;setChoiceBack(dismiss); setQuery(''); setChoice(request);
  }, [dismiss]);
  useEffect(()=>()=>setChoiceBack(null),[]);
  const close = useCallback((owner: string) => {if (choiceRef.current?.owner === owner) dismiss();}, [dismiss]);
  const context = useMemo(() => ({open, close}), [open, close]);
  useEffect(() => {
    return subscribeUserOperation(() => {if (userOperationSnapshot().busy && choiceRef.current) dismiss();});
  }, [dismiss]);
  useEffect(() => {
    // Subscribe before a choice opens: opening and background can be queued in
    // the same event turn, before the choice-specific effect would be installed.
    if (Platform.OS === 'web') {
      const visibility = () => {if (document.visibilityState === 'hidden') dismiss();};
      document.addEventListener('visibilitychange', visibility);
      return () => document.removeEventListener('visibilitychange', visibility);
    }
    const state = AppState.addEventListener('change', value => {if (value !== 'active') dismiss();});
    return () => state.remove();
  }, [dismiss]);
  useEffect(() => {
    if (!choice) return;
    if (Platform.OS === 'web') {
      const previous = document.activeElement as HTMLElement | null;
      const background = backgroundRef.current as unknown as HTMLElement | null;
      const panel = panelRef.current as unknown as HTMLElement | null;
      // Focus a button, never the search input: this cannot summon a keyboard.
      (closeRef.current as unknown as HTMLElement | null)?.focus({preventScroll: true});
      if (background) background.inert = true;
      const escape = (event: KeyboardEvent) => {
        if (isUserInteractionBlocked()) {event.preventDefault(); return;}
        if (event.key === 'Escape') {event.preventDefault(); dismiss();}
        if (event.key !== 'Tab' || !panel) return;
        const controls = Array.from(panel.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]'))
          .filter(element => element.tabIndex >= 0 && !element.hasAttribute('disabled') && element.getAttribute('aria-disabled') !== 'true' && element.getClientRects().length > 0);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus({preventScroll: true});}
        else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus({preventScroll: true});}
      };
      document.addEventListener('keydown', escape);
      return () => {
        document.removeEventListener('keydown', escape);
        if (background) background.inert = false;
        if (previous?.isConnected && document.visibilityState !== 'hidden') previous.focus({preventScroll: true});
      };
    }
    const back = BackHandler.addEventListener('hardwareBackPress', () => {if (!isUserInteractionBlocked()) dismiss(); return true;});
    return () => back.remove();
  }, [choice, dismiss]);
  const options = useMemo(() => {
    const needle = normalized(query.trim());
    return choice?.options.filter(item => !needle || normalized(item.label).includes(needle)) || [];
  }, [choice, query]);
  const select = (value: string) => {
    const current = choiceRef.current;
    if (!current || current !== choice) return;
    dismiss(); return current.onSelect(value);
  };
  // A button delivered after close/reopen still has the previous render's
  // closure. Owner identity is insufficient when that same field opens again.
  const dismissCurrent = () => {if (choiceRef.current === choice) dismiss();};
  return <Context.Provider value={context}><View style={s.root}>
    <View ref={backgroundRef} style={s.root} accessibilityElementsHidden={!!choice} importantForAccessibility={choice ? 'no-hide-descendants' : 'auto'}>{children}</View>
    {choice && <SafeAreaView ref={panelRef} style={s.overlay} edges={['top', 'bottom']} accessibilityViewIsModal role="dialog" accessibilityLabel={choice.label} aria-modal>
      <Pressable accessibilityRole="button" accessibilityLabel={`Fechar opções de ${choice.label}`} onPress={dismissCurrent} style={s.backdrop}/>
      <View style={s.sheet}>
        <View style={s.header}><Text accessibilityRole="header" style={[s.title,readableText]}>{choice.label}</Text>
          <Pressable ref={closeRef} accessibilityRole="button" accessibilityLabel="Fechar opções" onPress={dismissCurrent} style={s.close}><Text style={s.closeText}>Fechar</Text></Pressable></View>
        {choice.options.length > 8 && <TextInput accessibilityLabel={`Buscar opção de ${choice.label}`} value={query} onChangeText={setQuery}
          placeholder="Buscar opção" placeholderTextColor="#92A4A2" style={s.search}/>}
        <FlatList style={s.list} data={options} keyExtractor={item => item.value} keyboardShouldPersistTaps="handled"
          renderItem={({item}) => <Pressable accessibilityRole="button" accessibilityLabel={`Escolher ${item.label}`}
            accessibilityState={{selected: item.value === choice.value}} aria-pressed={item.value === choice.value}
            onPress={() => select(item.value)} style={[s.option, item.value === choice.value && s.optionActive]}>
            <Text style={[s.optionText,readableText, item.value === choice.value && s.optionTextActive]}>{item.label}</Text>
            {item.value === choice.value && <Text style={s.check}>✓</Text>}</Pressable>}
          ListEmptyComponent={<Text style={s.empty}>Nenhuma opção encontrada.</Text>}/>
        {choice.allowClear && !!choice.value && <Pressable accessibilityRole="button" accessibilityLabel={`Limpar seleção de ${choice.label}`} onPress={() => select('')} style={s.clear}><Text style={s.clearText}>Limpar seleção</Text></Pressable>}
      </View>
    </SafeAreaView>}
  </View></Context.Provider>;
}

export function useChoiceOverlay() {
  const context = useContext(Context);
  if (!context) throw new Error('O seletor precisa do host de opções do aplicativo.');
  return context;
}

const s = StyleSheet.create({root: {flex: 1}, overlay: {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, zIndex: 1000, elevation: 24, justifyContent: 'flex-end'},
  backdrop: {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, backgroundColor: 'rgba(2,9,10,.72)'},
  // Give the virtualized list a definite viewport. Content-driven sheet sizing
  // feeds list measurements back into its own height as rows render/filter.
  sheet: {backgroundColor: '#0E1C1E', borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 18, height: '85%'},
  header: {flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 12}, title: {color: '#F4F7F5', fontSize: 20, fontWeight: '700', flexGrow: 1, flexShrink: 1, flexBasis: 180, minWidth: 0},
  close: {minHeight: 44, maxWidth: '50%', justifyContent: 'center', padding: 8}, closeText: {color: '#67DFC3', fontWeight: '700'},
  search: {backgroundColor: '#142326', borderWidth: 1, borderColor: '#263D40', borderRadius: 11, color: '#F5F8F6', paddingHorizontal: 13, paddingVertical: 11, marginBottom: 10},
  list: {flex: 1, minHeight: 0}, option: {minHeight: 48, borderBottomWidth: 1, borderBottomColor: '#1E3235', paddingHorizontal: 12, paddingVertical: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between'},
  optionActive: {backgroundColor: '#17322F'}, optionText: {color: '#D8E4E1', fontSize: 15, flex: 1, minWidth: 0}, optionTextActive: {color: '#67DFC3', fontWeight: '700'},
  check: {color: '#67DFC3', fontSize: 17, fontWeight: '800'}, empty: {color: '#92A4A2', padding: 20, textAlign: 'center'}, clear: {minHeight: 44, padding: 14, alignItems: 'center', marginTop: 6}, clearText: {color: '#FF8C83', fontWeight: '700'}});
