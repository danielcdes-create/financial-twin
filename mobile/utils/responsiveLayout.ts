import { Platform, type TextStyle } from 'react-native';

// Available space in text-sized units: accessibility fonts get the same
// single-column layout as a narrow display.
export const compactLayout = (width: number, fontScale = 1) =>
  width / Math.max(1, fontScale || 1) < 360;

export const readableText: TextStyle = Platform.OS === 'web'
  ? { overflowWrap: 'anywhere' } as TextStyle
  : {};
