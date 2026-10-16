import { useEffect } from 'react';
import { AppState, Platform } from 'react-native';
import * as ScreenCapture from 'expo-screen-capture';

export function useScreenPrivacy() {
  useEffect(() => {
    if (Platform.OS === 'android') {
      // Owner policy: native screenshots/recording are allowed during active use.
      // AuthProvider provides the separate opaque cover while backgrounded.
      const allow = () => {void ScreenCapture.allowScreenCaptureAsync().catch(() => {});};
      allow();
      const subscription = AppState.addEventListener('change', state => {if (state === 'active') allow();});
      return () => subscription.remove();
    }
    if (Platform.OS !== 'ios') return;
    // Keep the iOS app-switcher cover without blocking screenshots while active.
    void ScreenCapture.enableAppSwitcherProtectionAsync().catch(() => {});
    return () => {
      void ScreenCapture.disableAppSwitcherProtectionAsync().catch(() => {});
    };
  }, []);
}
