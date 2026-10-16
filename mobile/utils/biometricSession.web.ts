export async function biometricAvailable() {return false;}
export async function biometricEnabled() {return false;}
export async function saveBiometricSession(_token: string, _expiresAt: number) {throw new Error('Biometria disponível no aplicativo Android ou iOS.');}
export async function updateBiometricSession(_token: string, _expiresAt: number) {throw new Error('Biometria indisponível no navegador.');}
export async function readBiometricSession(): Promise<{token: string; expiresAt: number}> {throw new Error('Entre com sua senha.');}
export async function clearBiometricSession() {}
export async function removeLegacySession() {}
