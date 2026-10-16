import React, {useEffect, useRef, useSyncExternalStore} from 'react';
import {ActivityIndicator, BackHandler, Keyboard, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View} from 'react-native';
import {cancelDialog, dialogSnapshot, dismissDialog, subscribeDialog} from '../utils/appAlert';
import {isUserOperationBusy, subscribeUserOperation, userOperationSnapshot} from '../utils/userOperation';

export default function AppDialogHost() {
  const dialog = useSyncExternalStore(subscribeDialog, dialogSnapshot, () => null);
  const operation = useSyncExternalStore(subscribeUserOperation, userOperationSnapshot, userOperationSnapshot);
  const panelRef = useRef<View>(null);
  useEffect(() => {
    if (!dialog) return;
    Keyboard.dismiss();
    if (Platform.OS !== 'web') return;
    const previous = document.activeElement as HTMLElement | null;
    const controls = () => Array.from((panelRef.current as unknown as HTMLElement | null)?.querySelectorAll<HTMLElement>('[role="button"], button, [tabindex]') || [])
      .filter(element => element.tabIndex >= 0 && element.getAttribute('aria-disabled') !== 'true' && element.getClientRects().length > 0);
    controls()[0]?.focus({preventScroll: true});
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {event.preventDefault(); if (!isUserOperationBusy()) cancelDialog();}
      if (event.key !== 'Tab') return;
      const items = controls(), first = items[0], last = items[items.length - 1];
      if (!first) {event.preventDefault(); return;}
      if (!(panelRef.current as unknown as HTMLElement | null)?.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last)) {event.preventDefault(); first.focus({preventScroll: true});}
      else if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus({preventScroll: true});}
    };
    document.addEventListener('keydown', key, true);
    return () => {document.removeEventListener('keydown', key, true); if (previous?.isConnected && document.visibilityState !== 'hidden') previous.focus({preventScroll: true});};
  }, [dialog]);
  useEffect(() => {
    if (Platform.OS === 'web' || !dialog?.options?.inActivity) return;
    const back = BackHandler.addEventListener('hardwareBackPress', () => {
      if (!isUserOperationBusy() && dialogSnapshot() === dialog) cancelDialog();
      return true;
    });
    return () => back.remove();
  }, [dialog]);
  if (!dialog || (Platform.OS !== 'web' && !dialog.options?.inActivity)) return null;
  const body =
    <View style={s.backdrop}>
      <View ref={panelRef} accessibilityRole="alert" accessibilityViewIsModal accessibilityLabel={dialog.title} aria-modal style={s.dialog}>
        <ScrollView style={{flexShrink: 1}}><Text style={s.title}>{dialog.title}</Text><Text style={s.message}>{dialog.message}</Text></ScrollView>
        {operation.busy && <View style={s.wait}><ActivityIndicator color="#67DFC3"/><Text style={s.message}>Concluindo a ação…</Text></View>}
        <View style={s.actions}>{dialog.buttons.map((button, index) =>
          <Pressable key={index} accessibilityRole="button" disabled={operation.busy} onPress={() => {if (!isUserOperationBusy()) dismissDialog(button, dialog);}} style={[s.button, button.style === 'cancel' && s.cancel, button.style === 'destructive' && s.destructive, operation.busy && s.disabled]}>
            <Text style={s.buttonText}>{button.text || 'OK'}</Text>
          </Pressable>)}</View>
      </View>
    </View>;
  return Platform.OS === 'web' ? <Modal transparent visible onShow={() => {
    (panelRef.current as unknown as HTMLElement | null)?.querySelector<HTMLElement>('[role="button"]:not([aria-disabled="true"])')?.focus({preventScroll: true});
  }} onRequestClose={() => {if (!isUserOperationBusy()) cancelDialog();}}>{body}</Modal> :
    <View style={s.activityOverlay} onStartShouldSetResponder={() => true}>{body}</View>;
}
const s = StyleSheet.create({
  activityOverlay: {position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, zIndex: 2100, elevation: 33},
  backdrop: {flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#0009', padding: 20},
  dialog: {width: '100%', maxWidth: 460, maxHeight: '85%', backgroundColor: '#142326', borderRadius: 16, padding: 20},
  title: {color: '#F4F7F5', fontSize: 19, fontWeight: '700', marginBottom: 12},
  message: {color: '#C5D8D4', fontSize: 15, lineHeight: 22},
  actions: {flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 10, marginTop: 20},
  button: {backgroundColor: '#49C5AA', minHeight: 44, maxWidth: '100%', padding: 12, borderRadius: 9},
  cancel: {backgroundColor: '#92A4A2'},
  destructive: {backgroundColor: '#EB7770'}, disabled: {opacity: .5},
  wait: {flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 12},
  buttonText: {color: '#061412', fontWeight: '700'},
});
