// Keep an explicitly opened system picker alive under an opaque privacy cover.
// Network access is suspended by AuthProvider, which applies the background grace period.
let active = 0;
let deadline = 0;
const listeners = new Set<() => void>();
export const devicePromptActive = () => active > 0 && Date.now() < deadline;
export const subscribeDevicePrompt = (listener: () => void) => {listeners.add(listener); return () => {listeners.delete(listener);};};
export async function withDevicePrompt<T>(action: () => Promise<T>): Promise<T> {
  active++; deadline = Date.now() + 120_000;
  const timer = setTimeout(() => listeners.forEach(listener => listener()), 120_000);
  try {return await action();}
  finally {clearTimeout(timer); active--; listeners.forEach(listener => listener());}
}
