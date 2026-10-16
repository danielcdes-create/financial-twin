import React, {useEffect, useLayoutEffect, useRef, useSyncExternalStore} from 'react';
import {ActivityIndicator, BackHandler, Keyboard, Platform, StyleSheet, Text, View} from 'react-native';
import {isUserInteractionBlocked, subscribeUserOperation, userOperationSnapshot} from '../utils/userOperation';

export default function UserOperationBoundary({children}: {children: React.ReactNode}) {
  const operation = useSyncExternalStore(subscribeUserOperation, userOperationSnapshot, userOperationSnapshot);
  const blocked = operation.busy || operation.dialogOpen;
  const content = useRef<View>(null), progress = useRef<View>(null);

  useEffect(() => {
    // Subscribe once; querying synchronously also covers Back before render.
    if (Platform.OS !== 'web') {
      const back = BackHandler.addEventListener('hardwareBackPress', isUserInteractionBlocked);
      return () => back.remove();
    }
    const stop = (event: Event) => {
      if (!isUserInteractionBlocked()) return;
      const target = event.target as Node | null;
      if (!(content.current as unknown as HTMLElement | null)?.contains(target)) return;
      event.preventDefault(); event.stopImmediatePropagation();
    };
    const events = ['click', 'pointerdown', 'keydown', 'submit'];
    events.forEach(name => document.addEventListener(name, stop, true));
    return () => events.forEach(name => document.removeEventListener(name, stop, true));
  }, []);

  useLayoutEffect(() => {
    if (!blocked) return;
    Keyboard.dismiss();
    if (Platform.OS !== 'web') return;
    const background = content.current as unknown as HTMLElement | null;
    const previous = document.activeElement as HTMLElement | null;
    if (background) background.inert = true;
    if (operation.busy && !operation.dialogOpen) (progress.current as unknown as HTMLElement | null)?.focus({preventScroll: true});
    return () => {
      if (background) background.inert = false;
      // Do not move focus behind a result/confirmation dialog.
      if (!isUserInteractionBlocked() && previous?.isConnected && document.visibilityState !== 'hidden') previous.focus({preventScroll: true});
    };
  }, [blocked, operation.busy, operation.dialogOpen]);

  return <View style={s.root}>
    <View ref={content} style={s.root} pointerEvents={blocked ? 'none' : 'auto'} accessibilityElementsHidden={blocked}
      importantForAccessibility={blocked ? 'no-hide-descendants' : 'auto'}>{children}</View>
    {operation.busy && !operation.dialogOpen && <View ref={progress} style={s.overlay} focusable
      accessibilityViewIsModal accessibilityRole="alert" accessibilityLabel="Processando. Aguarde a conclusão." aria-live="polite"
      onStartShouldSetResponder={() => true}>
      <View style={s.card}><ActivityIndicator color="#67DFC3"/><Text style={s.title}>Processando…</Text>
        <Text style={s.message}>Aguarde a conclusão desta ação.</Text></View>
    </View>}
  </View>;
}

const s = StyleSheet.create({root: {flex: 1}, overlay: {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0,
  zIndex: 2000, elevation: 32, backgroundColor: 'rgba(2,9,10,.65)', justifyContent: 'center', alignItems: 'center', padding: 24},
  card: {width: '100%', maxWidth: 340, backgroundColor: '#142326', borderRadius: 16, padding: 24, gap: 12, alignItems: 'center'},
  title: {color: '#F4F7F5', fontSize: 18, fontWeight: '700'}, message: {color: '#C5D8D4', fontSize: 14, textAlign: 'center'}});
