import { useEffect, useState } from 'react';

export function useForeground() {
  const [active, setActive] = useState(() => typeof document !== 'undefined' && document.visibilityState === 'visible');
  useEffect(() => {
    const update = () => setActive(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  return active;
}
