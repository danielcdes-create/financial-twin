export async function redirectSystemPath({path}:{path:string;initial:boolean}) {
  try {
    const parsed = new URL(path);
    if (parsed.hostname === 'expo-sharing') {
      (globalThis as any).__financialTwinPendingShare = true;
      (globalThis as any).__financialTwinShareSeq = (Number((globalThis as any).__financialTwinShareSeq) || 0) + 1;
      return '/handle-share';
    }
    if (parsed.hostname !== 'handle-share' && !/^\/handle-share(?:[/?#]|$)/.test(parsed.pathname)) {
      (globalThis as any).__financialTwinPendingShare = false;
    }
  } catch {
    // Relative routes continue through Expo Router unchanged.
    if (!/^\/?handle-share(?:[/?#]|$)/.test(String(path || '').trim())) {
      (globalThis as any).__financialTwinPendingShare = false;
    }
  }
  return path;
}
