import * as SecureStore from 'expo-secure-store';
import * as LocalAuthentication from 'expo-local-authentication';
import { Platform } from 'react-native';

const KEY = 'financial-twin-biometric-session-v1';
const ENABLED = 'financial-twin-biometric-enabled-v1';
const ENVELOPE_AAD = 'financial-twin-biometric-v2';
const options: SecureStore.SecureStoreOptions = {
  requireAuthentication: true,
  keychainService: 'financial-twin-biometric-v1',
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  authenticationPrompt: 'Desbloquear Financial Twin',
};

let inMemoryAnchor = '';
let cachedEnvelope = '';

function utf8Encode(str: string): Uint8Array {
  const bytes: number[] = [];
  for (let i = 0; i < str.length; i++) {
    let code = str.charCodeAt(i);
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < str.length) {
      const low = str.charCodeAt(++i);
      const cp = ((code - 0xd800) << 10) + (low - 0xdc00) + 0x10000;
      bytes.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    } else {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    }
  }
  return new Uint8Array(bytes);
}

function utf8Decode(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; ) {
    const b0 = bytes[i++];
    if (b0 < 0x80) {
      out += String.fromCharCode(b0);
    } else if ((b0 & 0xe0) === 0xc0) {
      const b1 = bytes[i++] & 0x3f;
      out += String.fromCharCode(((b0 & 0x1f) << 6) | b1);
    } else if ((b0 & 0xf0) === 0xe0) {
      const b1 = bytes[i++] & 0x3f;
      const b2 = bytes[i++] & 0x3f;
      out += String.fromCharCode(((b0 & 0x0f) << 12) | (b1 << 6) | b2);
    } else {
      const b1 = bytes[i++] & 0x3f;
      const b2 = bytes[i++] & 0x3f;
      const b3 = bytes[i++] & 0x3f;
      const cp = (((b0 & 0x07) << 18) | (b1 << 12) | (b2 << 6) | b3) - 0x10000;
      out += String.fromCharCode(0xd800 + (cp >> 10), 0xdc00 + (cp & 0x3ff));
    }
  }
  return out;
}

export function bytesToHex(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, '0');
  }
  return out;
}

function hexToBytes(hex: string): Uint8Array | null {
  if (!hex || hex.length % 2 !== 0 || !/^[0-9a-fA-F]+$/.test(hex)) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export function createSha256Hasher(): {update: (chunk: Uint8Array) => void; digest: () => Uint8Array} {
  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
  let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  const w = new Uint32Array(64);
  const buf = new Uint8Array(64);
  let bufLen = 0;
  let totalBytes = 0;
  const rotr = (x: number, n: number) => ((x >>> n) | (x << (32 - n))) >>> 0;
  const processBlock = (block: Uint8Array, offset: number) => {
    for (let i = 0; i < 16; i++) {
      const j = offset + i * 4;
      w[i] = ((block[j] << 24) | (block[j + 1] << 16) | (block[j + 2] << 8) | block[j + 3]) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = (rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)) >>> 0;
      const s1 = (rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)) >>> 0;
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let i = 0; i < 64; i++) {
      const S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
      const ch = ((e & f) ^ ((~e) & g)) >>> 0;
      const temp1 = (h + S1 + ch + SHA256_K[i] + w[i]) >>> 0;
      const S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
      const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
      const temp2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
  };
  return {
    update(chunk: Uint8Array) {
      if (!chunk || chunk.length === 0) return;
      totalBytes += chunk.length;
      let pos = 0;
      if (bufLen > 0) {
        const take = Math.min(chunk.length, 64 - bufLen);
        buf.set(chunk.subarray(0, take), bufLen);
        bufLen += take;
        pos += take;
        if (bufLen === 64) {
          processBlock(buf, 0);
          bufLen = 0;
        }
      }
      while (pos + 64 <= chunk.length) {
        processBlock(chunk, pos);
        pos += 64;
      }
      if (pos < chunk.length) {
        buf.set(chunk.subarray(pos), 0);
        bufLen = chunk.length - pos;
      }
    },
    digest(): Uint8Array {
      const bitLen = totalBytes * 8;
      const padLen = bufLen + 9 <= 64 ? 64 : 128;
      const tail = new Uint8Array(padLen);
      if (bufLen > 0) tail.set(buf.subarray(0, bufLen), 0);
      tail[bufLen] = 0x80;
      const hi = Math.floor(bitLen / 0x100000000) >>> 0;
      const lo = bitLen >>> 0;
      tail[padLen - 8] = (hi >>> 24) & 0xff;
      tail[padLen - 7] = (hi >>> 16) & 0xff;
      tail[padLen - 6] = (hi >>> 8) & 0xff;
      tail[padLen - 5] = hi & 0xff;
      tail[padLen - 4] = (lo >>> 24) & 0xff;
      tail[padLen - 3] = (lo >>> 16) & 0xff;
      tail[padLen - 2] = (lo >>> 8) & 0xff;
      tail[padLen - 1] = lo & 0xff;
      processBlock(tail, 0);
      if (padLen === 128) processBlock(tail, 64);
      const out = new Uint8Array(32);
      const words = [h0, h1, h2, h3, h4, h5, h6, h7];
      for (let i = 0; i < 8; i++) {
        out[i * 4] = (words[i] >>> 24) & 0xff;
        out[i * 4 + 1] = (words[i] >>> 16) & 0xff;
        out[i * 4 + 2] = (words[i] >>> 8) & 0xff;
        out[i * 4 + 3] = words[i] & 0xff;
      }
      return out;
    },
  };
}

export function sha256Bytes(data: Uint8Array): Uint8Array {
  const hasher = createSha256Hasher();
  hasher.update(data);
  return hasher.digest();
}

function randomBytes(length: number): Uint8Array {
  const out = new Uint8Array(length);
  const g = globalThis as {crypto?: {getRandomValues?: (arr: Uint8Array) => Uint8Array}};
  const cryptoObj = g?.crypto ?? (typeof crypto !== 'undefined' ? (crypto as {getRandomValues?: (arr: Uint8Array) => Uint8Array}) : undefined);
  if (typeof cryptoObj?.getRandomValues !== 'function') {
    throw new Error('Gerador criptográfico seguro indisponível neste dispositivo.');
  }
  cryptoObj.getRandomValues(out);
  return out;
}

function rotl32(v: number, c: number): number {
  return ((v << c) | (v >>> (32 - c))) >>> 0;
}

function quarterRound(s: Uint32Array, a: number, b: number, c: number, d: number) {
  s[a] = (s[a] + s[b]) >>> 0; s[d] = rotl32(s[d] ^ s[a], 16);
  s[c] = (s[c] + s[d]) >>> 0; s[b] = rotl32(s[b] ^ s[c], 12);
  s[a] = (s[a] + s[b]) >>> 0; s[d] = rotl32(s[d] ^ s[a], 8);
  s[c] = (s[c] + s[d]) >>> 0; s[b] = rotl32(s[b] ^ s[c], 7);
}

function readU32LE(b: Uint8Array, i: number): number {
  return (b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)) >>> 0;
}

function chacha20Block(key32: Uint8Array, counter: number, nonce12: Uint8Array): Uint8Array {
  const init = new Uint32Array(16);
  init[0] = 0x61707865; init[1] = 0x3320646e; init[2] = 0x79622d32; init[3] = 0x6b206574;
  for (let i = 0; i < 8; i++) init[4 + i] = readU32LE(key32, i * 4);
  init[12] = counter >>> 0;
  init[13] = readU32LE(nonce12, 0);
  init[14] = readU32LE(nonce12, 4);
  init[15] = readU32LE(nonce12, 8);
  const work = new Uint32Array(init);
  for (let i = 0; i < 10; i++) {
    quarterRound(work, 0, 4, 8, 12); quarterRound(work, 1, 5, 9, 13);
    quarterRound(work, 2, 6, 10, 14); quarterRound(work, 3, 7, 11, 15);
    quarterRound(work, 0, 5, 10, 15); quarterRound(work, 1, 6, 11, 12);
    quarterRound(work, 2, 7, 8, 13); quarterRound(work, 3, 4, 9, 14);
  }
  const out = new Uint8Array(64);
  for (let i = 0; i < 16; i++) {
    const v = (work[i] + init[i]) >>> 0;
    out[i * 4] = v & 0xff;
    out[i * 4 + 1] = (v >>> 8) & 0xff;
    out[i * 4 + 2] = (v >>> 16) & 0xff;
    out[i * 4 + 3] = (v >>> 24) & 0xff;
  }
  return out;
}

function chacha20Xor(key32: Uint8Array, counter: number, nonce12: Uint8Array, input: Uint8Array): Uint8Array {
  const out = new Uint8Array(input.length);
  for (let pos = 0, blk = counter; pos < input.length; pos += 64, blk = (blk + 1) >>> 0) {
    const stream = chacha20Block(key32, blk, nonce12);
    const chunk = Math.min(64, input.length - pos);
    for (let i = 0; i < chunk; i++) out[pos + i] = input[pos + i] ^ stream[i];
  }
  return out;
}

function poly1305Tag(otk32: Uint8Array, aad: Uint8Array, ciphertext: Uint8Array): Uint8Array {
  const rBytes = new Uint8Array(otk32.subarray(0, 16));
  rBytes[3] &= 15; rBytes[7] &= 15; rBytes[11] &= 15; rBytes[15] &= 15;
  rBytes[4] &= 252; rBytes[8] &= 252; rBytes[12] &= 252;
  let r = 0n;
  let s = 0n;
  for (let i = 0; i < 16; i++) {
    r |= BigInt(rBytes[i]) << BigInt(8 * i);
    s |= BigInt(otk32[16 + i]) << BigInt(8 * i);
  }
  const pad16 = (len: number) => (len % 16 === 0 ? 0 : 16 - (len % 16));
  const totalLen = aad.length + pad16(aad.length) + ciphertext.length + pad16(ciphertext.length) + 16;
  const macData = new Uint8Array(totalLen);
  macData.set(aad, 0);
  const ctOffset = aad.length + pad16(aad.length);
  macData.set(ciphertext, ctOffset);
  const lenOffset = ctOffset + ciphertext.length + pad16(ciphertext.length);
  let aadLen = BigInt(aad.length);
  let ctLen = BigInt(ciphertext.length);
  for (let i = 0; i < 8; i++) {
    macData[lenOffset + i] = Number(aadLen & 0xffn);
    aadLen >>= 8n;
    macData[lenOffset + 8 + i] = Number(ctLen & 0xffn);
    ctLen >>= 8n;
  }
  const P = (1n << 130n) - 5n;
  const hiBit = 1n << 128n;
  let acc = 0n;
  for (let pos = 0; pos < macData.length; pos += 16) {
    let n = 0n;
    for (let i = 0; i < 16; i++) n |= BigInt(macData[pos + i]) << BigInt(8 * i);
    acc = ((acc + n + hiBit) * r) % P;
  }
  const tagVal = (acc + s) & ((1n << 128n) - 1n);
  const out = new Uint8Array(16);
  let rem = tagVal;
  for (let i = 0; i < 16; i++) {
    out[i] = Number(rem & 0xffn);
    rem >>= 8n;
  }
  return out;
}

function deriveAnchorKey(anchorJson: string): Uint8Array {
  try {
    const parsed = JSON.parse(anchorJson);
    if (typeof parsed?.aeadKey === 'string' && /^[0-9a-fA-F]{64}$/.test(parsed.aeadKey)) {
      const key = hexToBytes(parsed.aeadKey);
      if (key && key.length === 32) return key;
    }
  } catch {
    // Fall through to deterministic SHA-256 KDF over hardware-protected anchor
  }
  return sha256Bytes(utf8Encode(`financial-twin-anchor-kdf-v2:${anchorJson}`));
}

export function sealEnvelope(anchor: string, plain: string): string {
  const key = deriveAnchorKey(anchor);
  const nonce = randomBytes(12);
  const otk = chacha20Block(key, 0, nonce).subarray(0, 32);
  const plaintextBytes = utf8Encode(plain);
  const ciphertext = chacha20Xor(key, 1, nonce, plaintextBytes);
  const tag = poly1305Tag(otk, utf8Encode(ENVELOPE_AAD), ciphertext);
  return `v2:${bytesToHex(nonce)}:${bytesToHex(ciphertext)}:${bytesToHex(tag)}`;
}

export function openEnvelope(anchor: string, envelope: string): {token: string; expiresAt: number} | null {
  if (!envelope || !envelope.startsWith('v2:')) return null;
  const parts = envelope.split(':');
  if (parts.length !== 4) return null;
  const nonce = hexToBytes(parts[1]);
  const ciphertext = hexToBytes(parts[2]);
  const receivedTag = hexToBytes(parts[3]);
  if (!nonce || nonce.length !== 12 || !ciphertext || !ciphertext.length || !receivedTag || receivedTag.length !== 16) {
    return null;
  }
  const key = deriveAnchorKey(anchor);
  const otk = chacha20Block(key, 0, nonce).subarray(0, 32);
  const expectedTag = poly1305Tag(otk, utf8Encode(ENVELOPE_AAD), ciphertext);
  let diff = 0;
  for (let i = 0; i < 16; i++) diff |= expectedTag[i] ^ receivedTag[i];
  if (diff !== 0) return null;
  const plainBytes = chacha20Xor(key, 1, nonce, ciphertext);
  try {
    return JSON.parse(utf8Decode(plainBytes));
  } catch {
    return null;
  }
}

export function hasUnlockedBiometricAnchor(): boolean {
  return inMemoryAnchor.length > 0;
}

function normalizeBiometricError(error: unknown): Error {
  const message = error instanceof Error ? error.message : String(error || '');
  if (/KeyPermanentlyInvalidated|permanently invalidated|KeyStoreException/i.test(message)) {
    return new Error('Biometria alterada ou sessão indisponível. Entre com sua senha e ative novamente.');
  }
  if (/User canceled the authentication|Authentication canceled|System canceled|negative button/i.test(message)) {
    return new Error('Leitura da digital cancelada. Toque na digital para tentar novamente ou use sua senha.');
  }
  if (/Too many attempts|LOCKOUT/i.test(message)) {
    return new Error('Muitas tentativas no sensor. Aguarde alguns segundos ou entre com sua senha.');
  }
  return error instanceof Error ? error : new Error('Não foi possível validar a biometria.');
}

async function writeProtectedKey(payload: string) {
  try {
    await SecureStore.setItemAsync(KEY, payload, options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error || '');
    if (/KeyPermanentlyInvalidated|permanently invalidated|KeyStoreException|Could not encrypt/i.test(message)) {
      await SecureStore.deleteItemAsync(KEY, options).catch(() => {});
      await SecureStore.setItemAsync(KEY, payload, options);
      return;
    }
    throw normalizeBiometricError(error);
  }
}

export async function biometricAvailable() {
  return SecureStore.canUseBiometricAuthentication() && await LocalAuthentication.hasHardwareAsync() &&
    await LocalAuthentication.isEnrolledAsync() &&
    await LocalAuthentication.getEnrolledLevelAsync() === LocalAuthentication.SecurityLevel.BIOMETRIC_STRONG;
}
export async function biometricEnabled() {
  const value = await SecureStore.getItemAsync(ENABLED);
  if (typeof value === 'string' && value.startsWith('1:')) {
    cachedEnvelope = value.slice(2);
    return true;
  }
  cachedEnvelope = '';
  return value === '1';
}
export async function saveBiometricSession(token: string, expiresAt: number) {
  if (!await biometricAvailable()) throw new Error('Cadastre uma digital ou biometria forte nos ajustes do aparelho.');
  // Android prompts while writing the authenticated key. iOS needs confirmation on creation.
  if (Platform.OS === 'ios') {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: 'Ativar desbloqueio por biometria',
      cancelLabel: 'Cancelar',
      disableDeviceFallback: true,
      fallbackLabel: '',
      biometricsSecurityLevel: 'strong',
    });
    if (!result.success) throw new Error('Biometria não confirmada. Sua senha continua disponível.');
  }
  const aeadKey = bytesToHex(randomBytes(32));
  const payload = JSON.stringify({token, expiresAt, aeadKey});
  await writeProtectedKey(payload);
  await SecureStore.setItemAsync(ENABLED, '1', {keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
  inMemoryAnchor = payload;
  cachedEnvelope = '';
}
export async function updateBiometricSession(token: string, expiresAt: number) {
  const plain = JSON.stringify({token, expiresAt});
  if (inMemoryAnchor) {
    const sealed = sealEnvelope(inMemoryAnchor, plain);
    await SecureStore.setItemAsync(ENABLED, `1:${sealed}`, {keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    cachedEnvelope = sealed;
    return;
  }
  const aeadKey = bytesToHex(randomBytes(32));
  const payload = JSON.stringify({token, expiresAt, aeadKey});
  await writeProtectedKey(payload);
  inMemoryAnchor = payload;
  cachedEnvelope = '';
}
export async function readBiometricSession(): Promise<{token: string; expiresAt: number}> {
  let value: string | null;
  try {
    value = await SecureStore.getItemAsync(KEY, options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error || '');
    if (/KeyPermanentlyInvalidated|permanently invalidated|KeyStoreException/i.test(message)) {
      inMemoryAnchor = '';
      cachedEnvelope = '';
      await SecureStore.deleteItemAsync(KEY, options).catch(() => {});
    }
    throw normalizeBiometricError(error);
  }
  if (!value) {
    inMemoryAnchor = '';
    cachedEnvelope = '';
    throw new Error('Biometria alterada ou sessão indisponível. Entre com sua senha e ative novamente.');
  }
  let saved: any;
  try {
    saved = JSON.parse(value);
  } catch {
    inMemoryAnchor = '';
    cachedEnvelope = '';
    throw new Error('Sessão biométrica inválida ou corrompida. Entre com sua senha.');
  }
  inMemoryAnchor = value;
  if (cachedEnvelope) {
    const refreshed = openEnvelope(value, cachedEnvelope);
    if (!refreshed || typeof refreshed.token !== 'string' || typeof refreshed.expiresAt !== 'number') {
      inMemoryAnchor = '';
      cachedEnvelope = '';
      throw new Error('Sessão biométrica inválida ou corrompida. Entre com sua senha.');
    }
    if (refreshed.expiresAt >= (typeof saved?.expiresAt === 'number' ? saved.expiresAt : 0)) {
      saved = refreshed;
    }
  }
  if (typeof saved.token !== 'string' || typeof saved.expiresAt !== 'number' || saved.expiresAt <= Date.now()) {
    throw new Error('Sessão expirada. Entre com sua senha.');
  }
  return {token: saved.token, expiresAt: saved.expiresAt};
}
export async function clearBiometricSession() {
  inMemoryAnchor = '';
  cachedEnvelope = '';
  await SecureStore.deleteItemAsync(KEY, options);
  await SecureStore.deleteItemAsync(ENABLED);
}
export async function clearSavedPdfCpf() {
  await SecureStore.deleteItemAsync('financial-twin-pdf-cpf').catch(() => {});
}
export async function removeLegacySession() {
  await SecureStore.deleteItemAsync('financial-twin-session');
}
