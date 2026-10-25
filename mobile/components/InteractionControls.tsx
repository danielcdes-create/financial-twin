import React, {forwardRef, useSyncExternalStore} from 'react';
import {Pressable as NativePressable, TextInput as NativeTextInput, type PressableProps, type TextInputProps, type View} from 'react-native';
import {Alert} from '../utils/appAlert';
import {isUserInteractionBlocked, runUserAction, subscribeUserOperation, userOperationSnapshot} from '../utils/userOperation';

export const reportActionError = (error: unknown) => {
  if (error instanceof Error && error.name === 'AbortError') return;
  Alert.alert('Não foi possível concluir', error instanceof Error ? error.message : 'Tente novamente.');
};

const DYNAMIC_PREFIX_LABELS: [string, string][] = [
  ['Escolher ', 'Selecionar opção'],
  ['Selecionar ', 'Abrir seletor'],
  ['Detalhes de ', 'Abrir detalhes da conta'],
  ['Ver faturas de ', 'Ver faturas do cartão'],
  ['Excluir cartão ', 'Excluir cartão'],
  ['Editar subcategoria ', 'Editar subcategoria'],
  ['Subcategorias de ', 'Subcategorias'],
  ['Editar futuras de ', 'Editar parcelas futuras'],
  ['Checklist de ', 'Abrir checklist'],
  ['Informar saldos de ', 'Informar saldos'],
  ['Liquidar ocorrência ', 'Liquidar ocorrência'],
  ['Concluir tentativa de ', 'Concluir tentativa'],
  ['Revisar ', 'Revisar lote'],
  ['Abrir ', 'Abrir item'],
];

const sanitizeActionLabel = (raw: string | undefined | null): string => {
  const cleaned = String(raw || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) return 'Processando…';
  for (const [prefix, replacement] of DYNAMIC_PREFIX_LABELS) {
    if (cleaned.startsWith(prefix) && cleaned !== 'Abrir movimentações') {
      return replacement;
    }
  }
  if (cleaned.length > 42 || /\d/.test(cleaned) || /R\$/i.test(cleaned)) {
    return 'Processando…';
  }
  return cleaned;
};

const extractSingleTextChild = (children: React.ReactNode): string | null => {
  const items = React.Children.toArray(children);
  if (items.length !== 1) return null;
  const child = items[0];
  if (typeof child === 'string') return child;
  if (React.isValidElement(child)) {
    const grandChildren = React.Children.toArray((child.props as any)?.children);
    if (grandChildren.length === 1 && typeof grandChildren[0] === 'string') {
      return grandChildren[0];
    }
  }
  return null;
};

const inferPressableLabel = (props: PressableProps): string => {
  if (typeof props.accessibilityLabel === 'string' && props.accessibilityLabel.trim()) {
    return sanitizeActionLabel(props.accessibilityLabel);
  }
  const singleText = extractSingleTextChild(props.children as React.ReactNode);
  if (singleText) {
    return sanitizeActionLabel(singleText);
  }
  return 'Processando…';
};

// Check the store in the event itself: React's next render is too late to
// reject two taps delivered in the same event turn. Ignored taps are not queued.
export const Pressable = forwardRef<View, PressableProps>(function ActionPressable({onPress, onLongPress, disabled, ...props}, ref) {
  const label = inferPressableLabel(props);
  const invoke = (handler: PressableProps['onPress'], event: Parameters<NonNullable<PressableProps['onPress']>>[0]) => {
    if (disabled || !handler || isUserInteractionBlocked()) return;
    return runUserAction(() => handler(event), label, reportActionError);
  };
  return <NativePressable {...props} ref={ref} disabled={disabled}
    onPress={onPress ? event => invoke(onPress, event) : undefined}
    onLongPress={onLongPress ? event => invoke(onLongPress, event) : undefined}/>;
});

export const TextInput = forwardRef<NativeTextInput, TextInputProps>(function ActionTextInput({onSubmitEditing, onChange, onChangeText, onFocus, editable, ...props}, ref) {
  const operation = useSyncExternalStore(subscribeUserOperation, userOperationSnapshot, userOperationSnapshot);
  const blocked = operation.busy || operation.dialogOpen;
  const submitBase = sanitizeActionLabel(props.accessibilityLabel || props.placeholder);
  const submitLabel = submitBase === 'Processando…' ? 'Submeter campo' : `Submeter: ${submitBase}`;
  return <NativeTextInput {...props} ref={ref} editable={editable !== false && !blocked}
    onChange={onChange ? event => {if (!isUserInteractionBlocked()) onChange(event);} : undefined}
    onChangeText={onChangeText ? text => {if (!isUserInteractionBlocked()) onChangeText(text);} : undefined}
    onFocus={onFocus ? event => {if (!isUserInteractionBlocked()) onFocus(event);} : undefined}
    onSubmitEditing={onSubmitEditing ? event => {
      if (!isUserInteractionBlocked()) return runUserAction(() => onSubmitEditing(event), submitLabel, reportActionError);
    } : undefined}/>;
});
