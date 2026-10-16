import React from 'react';
import {StyleSheet, View} from 'react-native';

// Decorative mark only; the OS prompt and its sensor perform authentication.
export default function FingerprintMark() {
  return <View style={s.mark} accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
    {[0, 1, 2, 3, 4].map(index => <View key={index} style={[s.ridge, {left: index * 6, top: index * 6, width: 58 - index * 12, height: 66 - index * 7, borderRadius: 29 - index * 6}]}/>)}
    <View style={s.break}/>
  </View>;
}
const s = StyleSheet.create({
  mark: {width: 58, height: 72},
  ridge: {position: 'absolute', borderWidth: 2.5, borderColor: '#4FD1B5', borderBottomWidth: 0},
  break: {position: 'absolute', width: 8, height: 20, bottom: 0, left: 10, backgroundColor: '#172238', transform: [{rotate: '-25deg'}]},
});
