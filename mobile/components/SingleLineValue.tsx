import React from 'react';
import { Text, type StyleProp, type TextStyle } from 'react-native';

export type SingleLineValueProps = { children: string; style?: StyleProp<TextStyle> };

export default function SingleLineValue({ children, style }: SingleLineValueProps) {
  // Financial values must stay complete at the user's chosen text size.
  return <Text allowFontScaling accessibilityLabel={children}
    style={[{ minWidth: 0, flexShrink: 1, maxWidth: '100%' }, style]}>
    {children}
  </Text>;
}
