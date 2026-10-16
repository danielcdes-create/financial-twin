import React, {useLayoutEffect, useRef} from 'react';
import {StyleSheet, Text} from 'react-native';
import {Pressable} from './InteractionControls';
import {Alert} from '../utils/appAlert';
import {useAsyncScope} from '../utils/useAsyncScope';

type Props = {
  onDiscard: () => void | Promise<void>;
  disabled?: boolean;
  owner?: unknown;
  label?: string;
  message?: string;
  title?: string;
  confirmLabel?: string;
  light?: boolean;
};

export default function DiscardDraftAction({onDiscard, disabled = false, owner, label = 'Excluir rascunho', message = 'As alterações ainda não salvas serão apagadas. Os registros já salvos continuam preservados.', title = 'Excluir rascunho?', confirmLabel = 'Excluir rascunho', light = false}: Props) {
  const captureScope = useAsyncScope(owner);
  const currentAction = useRef({disabled, onDiscard});
  useLayoutEffect(() => {currentAction.current = {disabled, onDiscard};}, [disabled, onDiscard]);
  const confirm = () => {
    if (disabled) return;
    const valid = captureScope();
    Alert.alert(title, message, [
      {text: 'Cancelar', style: 'cancel'},
      {text: confirmLabel, style: 'destructive', onPress: () => {
        if (!valid() || currentAction.current.disabled) return;
        return currentAction.current.onDiscard();
      }},
    ]);
  };
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{disabled}} disabled={disabled}
    onPress={confirm} style={[s.button, disabled && s.disabled]}><Text style={[s.label, light && s.lightLabel]}>{label}</Text></Pressable>;
}

const s = StyleSheet.create({button: {minHeight: 48, borderWidth: 1, borderColor: '#B45850', padding: 12, borderRadius: 12, alignItems: 'center', justifyContent: 'center', marginVertical: 8}, label: {color: '#DD776D', fontWeight: '700', textAlign: 'center'}, lightLabel: {color: '#A92F2B'}, disabled: {opacity: .5}});
