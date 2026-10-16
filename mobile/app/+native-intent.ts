export async function redirectSystemPath({path}:{path:string;initial:boolean}) {
  try {
    if (new URL(path).hostname === 'expo-sharing') return '/handle-share';
  } catch {
    // Relative routes continue through Expo Router unchanged.
  }
  return path;
}
