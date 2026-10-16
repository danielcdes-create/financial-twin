import React, {forwardRef, useSyncExternalStore} from 'react';
import {Pressable as NativePressable, TextInput as NativeTextInput, type PressableProps, type TextInputProps, type View} from 'react-native';
import {Alert} from '../utils/appAlert';
import {isUserInteractionBlocked, runUserAction, subscribeUserOperation, userOperationSnapshot} from '../utils/userOperation';

export const reportActionError = (error: unknown) => {
  if (error instanceof Error && error.name === 'AbortError') return;
  Alert.alert('Não foi possível concluir', error instanceof Error ? error.message : 'Tente novamente.');
};

// Check the store in the event itself: React's next render is too late to
// reject two taps delivered in the same event turn. Ignored taps are not queued.
export const Pressable = forwardRef<View, PressableProps>(function ActionPressable({onPress, onLongPress, disabled, ...props}, ref) {
  const invoke = (handler: PressableProps['onPress'], event: Parameters<NonNullable<PressableProps['onPress']>>[0]) => {
    if (disabled || !handler || isUserInteractionBlocked()) return;
    return runUserAction(() => handler(event), 'Processando…', reportActionError);
  };
  return <NativePressable {...props} ref={ref} disabled={disabled}
    onPress={onPress ? event => invoke(onPress, event) : undefined}
    onLongPress={onLongPress ? event => invoke(onLongPress, event) : undefined}/>;
});

export const TextInput = forwardRef<NativeTextInput, TextInputProps>(function ActionTextInput({onSubmitEditing, onChange, onChangeText, onFocus, editable, ...props}, ref) {
  const operation = useSyncExternalStore(subscribeUserOperation, userOperationSnapshot, userOperationSnapshot);
  const blocked = operation.busy || operation.dialogOpen;
  return <NativeTextInput {...props} ref={ref} editable={editable !== false && !blocked}
    onChange={onChange ? event => {if (!isUserInteractionBlocked()) onChange(event);} : undefined}
    onChangeText={onChangeText ? text => {if (!isUserInteractionBlocked()) onChangeText(text);} : undefined}
    onFocus={onFocus ? event => {if (!isUserInteractionBlocked()) onFocus(event);} : undefined}
    onSubmitEditing={onSubmitEditing ? event => {
      if (!isUserInteractionBlocked()) return runUserAction(() => onSubmitEditing(event), 'Processando…', reportActionError);
    } : undefined}/>;
});
