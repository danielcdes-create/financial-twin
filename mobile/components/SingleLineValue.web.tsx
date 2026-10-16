import React from 'react';
import { Text } from 'react-native';
import type { SingleLineValueProps } from './SingleLineValue';
import { readableText } from '../utils/responsiveLayout';

export default function SingleLineValue({ children, style }: SingleLineValueProps) {
  return <Text allowFontScaling accessibilityLabel={children}
    style={[{ minWidth: 0, flexShrink: 1, maxWidth: '100%' }, style, readableText]}>
    {children}
  </Text>;
}
