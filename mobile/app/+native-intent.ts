export async function redirectSystemPath({path}:{path:string;initial:boolean}) {
  try {
    if (new URL(path).hostname === 'expo-sharing') {
      (globalThis as any).__financialTwinPendingShare = true;
      return '/handle-share';
    }
  } catch {
    // Relative routes continue through Expo Router unchanged.
  }
  return path;
}
