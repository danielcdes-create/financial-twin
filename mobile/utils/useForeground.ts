import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

export function useForeground() {
  const [active, setActive] = useState(AppState.currentState !== 'background' && AppState.currentState !== 'inactive');
  useEffect(() => {
    const change = AppState.addEventListener('change', state => setActive(state === 'active'));
    const blur = AppState.addEventListener('blur', () => setActive(false));
    const focus = AppState.addEventListener('focus', () => setActive(AppState.currentState === 'active'));
    return () => { change.remove(); blur.remove(); focus.remove(); };
  }, []);
  return active;
}
