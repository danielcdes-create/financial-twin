export async function redirectSystemPath({path}:{path:string;initial:boolean}) {
  try {
    if (new URL(path).hostname === 'expo-sharing') {
      (globalThis as any).__financialTwinPendingShare = true;
      (globalThis as any).__financialTwinShareSeq = (Number((globalThis as any).__financialTwinShareSeq) || 0) + 1;
      return '/handle-share';
    }
  } catch {
    // Relative routes continue through Expo Router unchanged.
  }
  return path;
}
