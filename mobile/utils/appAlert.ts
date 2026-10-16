import {Alert as NativeAlert, Platform} from 'react-native';
import type {AlertButton, AlertOptions} from 'react-native';
import {afterUserOperation, isUserOperationBusy, runUserAction, setUserDialogOpen} from './userOperation';

type AppAlertOptions = AlertOptions & {inActivity?: boolean};
export type AppDialog = {title: string; message: string; buttons: AlertButton[]; options?: AppAlertOptions};
let dialogs: AppDialog[] = [];
let nativeShown: AppDialog | null = null;
let generation = 0;
const listeners = new Set<() => void>();
const notify = () => {setUserDialogOpen(dialogs.length > 0); listeners.forEach(listener => listener());};
export function clearDialogs({preserveNative = false}: {preserveNative?: boolean} = {}) {
  // Showing a system Alert itself blurs the active Android Activity. Keep its
  // callback in that case; actual background/lock still invalidates it.
  if (preserveNative && Platform.OS !== 'web' && nativeShown) return;
  generation++; dialogs = []; nativeShown = null; notify();
}
export const dialogSnapshot = () => dialogs[0] || null;
export const subscribeDialog = (listener: () => void) => {listeners.add(listener); return () => {listeners.delete(listener);};};
function presentNativeDialog() {
  const dialog = dialogSnapshot();
  if (Platform.OS === 'web' || !dialog || dialog.options?.inActivity || nativeShown === dialog) return;
  nativeShown = dialog;
  NativeAlert.alert(dialog.title, dialog.message,
    dialog.buttons.map(button => ({...button, onPress: () => dismissDialog(button, dialog)})),
    {...dialog.options, onDismiss: () => {
      if (dialogSnapshot() !== dialog) return;
      dismissDialog(undefined, dialog); dialog.options?.onDismiss?.();
    }});
}
export function dismissDialog(button?: AlertButton, expected = dialogSnapshot()) {
  const current = dialogSnapshot();
  if (!current || current !== expected || isUserOperationBusy() || (button && !current.buttons.includes(button))) return;
  dialogs = dialogs.slice(1); nativeShown = null;
  const epoch = generation;
  if (!button?.onPress) {notify(); presentNativeDialog(); return;}
  // A preview can open a confirmation before its async handler has settled.
  // Start that confirmation's next step only after the previous step finishes.
  setUserDialogOpen(false);
  listeners.forEach(listener => listener());
  afterUserOperation(() => {
    if (epoch !== generation) return;
    runUserAction(() => button.onPress?.(), 'Processando…', error => {
      if (error instanceof Error && error.name === 'AbortError') return;
      Alert.alert('Não foi possível concluir', error instanceof Error ? error.message : 'Tente novamente.');
    });
    notify(); presentNativeDialog();
  });
}
export function cancelDialog() {
  const dialog = dialogSnapshot();
  if (!dialog) return;
  const cancel = dialog.buttons.find(button => button.style === 'cancel');
  if (cancel) dismissDialog(cancel);
  else if (dialog.buttons.length === 1) dismissDialog(dialog.buttons[0]);
}
export const Alert = {
  alert(title: string, message = '', buttons?: AlertButton[], options?: AppAlertOptions) {
    dialogs = [...dialogs, {title, message, buttons: buttons?.length ? buttons : [{text: 'OK'}], options: {inActivity: true, ...options}}];
    notify(); presentNativeDialog();
  },
};

// App confirmations must not open a system window: its delayed blur can cancel
// the write started by the confirmation, or the refresh after a saved result.
export const ActivityAlert = {
  alert(title: string, message = '', buttons?: AlertButton[], options?: AlertOptions) {
    Alert.alert(title, message, buttons, {...options, inActivity: true});
  },
};
