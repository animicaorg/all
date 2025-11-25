/**
 * Encrypted vault for the wallet keyring.
 *
 * - Payload (seed + minimal metadata) is encrypted with AES-GCM-256.
 * - Encryption key is derived from user password via:
 *     KDF0 = PBKDF2-HMAC-SHA3-512(password, salt_pbkdf, iters, dkLen=64)
 *     AESK = HKDF-SHA3-256(ikm=KDF0, salt="animica/v1/vault", info="aes-gcm-256", L=32)
 * - Optional "session PIN" allows quick unlock while the browser session lives.
 *   We derive a PIN key and store a *separately encrypted* copy of the decrypted payload
 *   inside chrome.storage.session. Clearing session or calling clearSessionPin() removes it.
 *
 * SECURITY NOTES
 * - The password-encrypted envelope (VaultEnvelope) lives in persistent storage (local).
 * - The PIN-wrapped copy (if enabled) lives in session storage ONLY. A PIN is low entropy,
 *   so we strongly recommend keeping PIN disabled on shared/untrusted machines.
 */

import { hkdfSha3_256, hmacSha3_512, sha3_256 } from '../pq/hkdf';

const VAULT_VERSION = 1 as const;

// ------------------------------- Types --------------------------------

export interface VaultPayload {
  /** 32 bytes master seed; base64-encoded for JSON */
  seedB64: string;
  /** Optional metadata for future features (derivation paths, labels) */
  meta?: {
    createdAt?: number;
    hint?: string;
  };
}

export interface VaultKdfParams {
  name: 'PBKDF2-SHA3-512';
  iterations: number;
  saltHex: string; // 16 or 32 bytes hex
}

export interface VaultHkdfParams {
  saltHex: string; // domain salt for vault key derivation
  info: 'aes-gcm-256';
}

export interface VaultCipher {
  name: 'AES-GCM';
  ivHex: string; // 12 bytes hex
}

export interface VaultEnvelope {
  version: number;
  kdf: VaultKdfParams;
  hkdf: VaultHkdfParams;
  cipher: VaultCipher;
  ciphertextB64: string;
  createdAt: number;
  updatedAt: number;
}

export type EncryptedVault = VaultEnvelope;

/** Session PIN wrap (stored in chrome.storage.session only) */
export interface PinWrapRecord {
  version: number;
  cipher: VaultCipher;
  pinSaltHex: string; // 16 bytes hex used to derive PIN key
  payloadB64: string; // entire decrypted payload JSON, encrypted under PIN
  createdAt: number;
}

const PIN_WRAP_STORAGE_KEY = 'animica:vault:pinwrap';

// ------------------------------- Small utils --------------------------------

const te = new TextEncoder();
const td = new TextDecoder();

function toHex(b: Uint8Array): string {
  return Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}
function fromHex(h: string): Uint8Array {
  const s = h.startsWith('0x') ? h.slice(2) : h;
  if (s.length % 2) throw new Error('hex length must be even');
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  return out;
}
function b64u(b: Uint8Array): string {
  // Standard base64 (URL-safe not strictly required here)
  return btoa(String.fromCharCode(...b));
}
function ub64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function randomBytes(n: number): Uint8Array {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b;
}

// ------------------------------- PBKDF2 (SHA3-512) --------------------------------

/**
 * PBKDF2 per RFC 8018 with HMAC-SHA3-512.
 */
async function pbkdf2Sha3_512(
  password: Uint8Array,
  salt: Uint8Array,
  iterations: number,
  dkLen: number,
): Promise<Uint8Array> {
  if (iterations < 1) throw new Error('iterations must be >= 1');
  const hLen = 64;
  const l = Math.ceil(dkLen / hLen);
  const r = dkLen - (l - 1) * hLen;

  const T = new Uint8Array(l * hLen);
  for (let i = 1; i <= l; i++) {
    const block = new Uint8Array(salt.length + 4);
    block.set(salt, 0);
    block[block.length - 4] = (i >>> 24) & 0xff;
    block[block.length - 3] = (i >>> 16) & 0xff;
    block[block.length - 2] = (i >>> 8) & 0xff;
    block[block.length - 1] = i & 0xff;

    let Ui = await hmacSha3_512(password, block);
    const Ti = new Uint8Array(Ui);
    for (let c = 2; c <= iterations; c++) {
      Ui = await hmacSha3_512(password, Ui);
      for (let j = 0; j < hLen; j++) Ti[j] ^= Ui[j];
    }
    T.set(Ti, (i - 1) * hLen);
  }
  return T.slice(0, (l - 1) * hLen + r);
}

// ------------------------------- AES-GCM helpers --------------------------------

async function importAesGcmKey(raw: Uint8Array): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function aesGcmEncrypt(key: CryptoKey, iv: Uint8Array, plaintext: Uint8Array): Promise<Uint8Array> {
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, plaintext);
  return new Uint8Array(ct);
}

async function aesGcmDecrypt(key: CryptoKey, iv: Uint8Array, ciphertext: Uint8Array): Promise<Uint8Array> {
  const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ciphertext);
  return new Uint8Array(pt);
}

// ------------------------------- Derivation --------------------------------

interface DeriveResult {
  aesKey: CryptoKey;
  hkdfSaltHex: string;
}

async function deriveVaultKey(
  password: string,
  kdfIters: number,
  saltHex?: string,
  hkdfSaltHex?: string,
): Promise<{ aesKey: CryptoKey; kdf: VaultKdfParams; hkdf: VaultHkdfParams }> {
  const pbkdfSalt = saltHex ? fromHex(saltHex) : randomBytes(16);
  const kdf0 = await pbkdf2Sha3_512(te.encode(password.normalize('NFKD')), pbkdfSalt, kdfIters, 64);
  const hkdfSalt = hkdfSaltHex ? fromHex(hkdfSaltHex) : te.encode('animica/v1/vault');
  const aesRaw = await hkdfSha3_256({
    ikm: kdf0,
    salt: hkdfSalt,
    info: te.encode('aes-gcm-256'),
    length: 32,
  });
  kdf0.fill(0);
  const aesKey = await importAesGcmKey(aesRaw);
  aesRaw.fill(0);

  return {
    aesKey,
    kdf: { name: 'PBKDF2-SHA3-512', iterations: kdfIters, saltHex: toHex(pbkdfSalt) },
    hkdf: { saltHex: toHex(hkdfSalt), info: 'aes-gcm-256' },
  };
}

// ------------------------------- Public API --------------------------------

/**
 * Create a fresh vault envelope from a payload and password.
 * @param payload VaultPayload (seedB64 + meta)
 * @param password user password (UTF-8 string)
 * @param kdfIterations default 200_000 (tunable)
 */
export async function createVault(
  payload: VaultPayload,
  password: string,
  kdfIterations = 200_000,
): Promise<VaultEnvelope> {
  const { aesKey, kdf, hkdf } = await deriveVaultKey(password, kdfIterations);
  const iv = randomBytes(12);
  const plaintext = te.encode(JSON.stringify(payload));
  const ct = await aesGcmEncrypt(aesKey, iv, plaintext);
  const now = Date.now();

  return {
    version: VAULT_VERSION,
    kdf,
    hkdf,
    cipher: { name: 'AES-GCM', ivHex: toHex(iv) },
    ciphertextB64: b64u(ct),
    createdAt: now,
    updatedAt: now,
  };
}

// Compatibility shims for legacy keyring callers
export async function encryptVault(payload: Uint8Array, password: string): Promise<VaultEnvelope> {
  const text = td.decode(payload);
  const obj = JSON.parse(text) as { seed: number[]; mnemonic?: string | null };
  const vp: VaultPayload = { seedB64: Buffer.from(obj.seed).toString('base64'), meta: {} };
  if (obj.mnemonic) (vp.meta as any).mnemonic = obj.mnemonic;
  return createVault(vp, password);
}

export async function decryptVault(envelope: VaultEnvelope, password: string): Promise<Uint8Array> {
  const payload = await openVault(envelope, password);
  const seed = Buffer.from(payload.seedB64, 'base64');
  const obj = { v: 1, seed: Array.from(seed.values()), mnemonic: (payload as any).meta?.mnemonic ?? null };
  return te.encode(JSON.stringify(obj));
}

/**
 * Decrypt a vault envelope with the user's password.
 */
export async function openVault(envelope: VaultEnvelope, password: string): Promise<VaultPayload> {
  if (envelope.version !== VAULT_VERSION) throw new Error('Unsupported vault version');
  const { aesKey } = await deriveVaultKey(
    password,
    envelope.kdf.iterations,
    envelope.kdf.saltHex,
    envelope.hkdf.saltHex,
  );
  const iv = fromHex(envelope.cipher.ivHex);
  const pt = await aesGcmDecrypt(aesKey, iv, ub64(envelope.ciphertextB64));
  return JSON.parse(td.decode(pt));
}

/**
 * Re-encrypt the vault with a new password.
 */
export async function changeVaultPassword(
  envelope: VaultEnvelope,
  oldPassword: string,
  newPassword: string,
  newKdfIterations = envelope.kdf.iterations,
): Promise<VaultEnvelope> {
  const payload = await openVault(envelope, oldPassword);
  const updated = await createVault(payload, newPassword, newKdfIterations);
  updated.createdAt = envelope.createdAt;
  return updated;
}

// ------------------------------- Session PIN wrap --------------------------------

/**
 * Set a session PIN by encrypting the DECRYPTED payload and storing it in chrome.storage.session.
 * The PIN key derivation:
 *   pinKey = HKDF-SHA3-256(ikm = sha3_256("animica/v1/pin|" + pin), salt = pinSalt, info="pin-wrap", L=32)
 * The pinSalt is random 16 bytes and is stored alongside the record.
 */
export async function setSessionPin(payload: VaultPayload, pin: string): Promise<void> {
  const pinSalt = randomBytes(16);
  const ikm = sha3_256(te.encode('animica/v1/pin|' + pin));
  const pinKeyRaw = await hkdfSha3_256({
    ikm,
    salt: pinSalt,
    info: te.encode('pin-wrap'),
    length: 32,
  });
  const key = await importAesGcmKey(pinKeyRaw);
  pinKeyRaw.fill(0);

  const iv = randomBytes(12);
  const pt = te.encode(JSON.stringify(payload));
  const ct = await aesGcmEncrypt(key, iv, pt);

  const record: PinWrapRecord = {
    version: VAULT_VERSION,
    cipher: { name: 'AES-GCM', ivHex: toHex(iv) },
    pinSaltHex: toHex(pinSalt),
    payloadB64: b64u(ct),
    createdAt: Date.now(),
  };

  if (chrome?.storage?.session) {
    await chrome.storage.session.set({ [PIN_WRAP_STORAGE_KEY]: record });
  } else {
    // Fallback (MV3 SW should have session storage; in tests we keep a global)
    (globalThis as any)[PIN_WRAP_STORAGE_KEY] = record;
  }
}

/**
 * Attempt quick unlock using the session PIN.
 * Returns the decrypted VaultPayload if a valid pin-wrap exists.
 */
export async function unlockWithSessionPin(pin: string): Promise<VaultPayload | null> {
  let rec: PinWrapRecord | undefined;
  if (chrome?.storage?.session) {
    const got = await chrome.storage.session.get(PIN_WRAP_STORAGE_KEY);
    rec = got?.[PIN_WRAP_STORAGE_KEY] as PinWrapRecord | undefined;
  } else {
    rec = (globalThis as any)[PIN_WRAP_STORAGE_KEY] as PinWrapRecord | undefined;
  }
  if (!rec) return null;
  if (rec.version !== VAULT_VERSION) return null;

  const pinSalt = fromHex(rec.pinSaltHex);
  const ikm = sha3_256(te.encode('animica/v1/pin|' + pin));
  const pinKeyRaw = await hkdfSha3_256({
    ikm,
    salt: pinSalt,
    info: te.encode('pin-wrap'),
    length: 32,
  });
  const key = await importAesGcmKey(pinKeyRaw);
  pinKeyRaw.fill(0);

  try {
    const iv = fromHex(rec.cipher.ivHex);
    const ct = ub64(rec.payloadB64);
    const pt = await aesGcmDecrypt(key, iv, ct);
    return JSON.parse(td.decode(pt));
  } catch {
    return null;
  }
}

/** Remove any session PIN-wrapped payload from session storage. */
export async function clearSessionPin(): Promise<void> {
  if (chrome?.storage?.session) {
    await chrome.storage.session.remove(PIN_WRAP_STORAGE_KEY);
  } else {
    delete (globalThis as any)[PIN_WRAP_STORAGE_KEY];
  }
}

// ------------------------------- Type guards --------------------------------

export function isVaultEnvelope(x: any): x is VaultEnvelope {
  return (
    x &&
    typeof x === 'object' &&
    typeof x.version === 'number' &&
    x.kdf?.name === 'PBKDF2-SHA3-512' &&
    typeof x.kdf?.iterations === 'number' &&
    typeof x.kdf?.saltHex === 'string' &&
    x.hkdf?.info === 'aes-gcm-256' &&
    typeof x.hkdf?.saltHex === 'string' &&
    x.cipher?.name === 'AES-GCM' &&
    typeof x.cipher?.ivHex === 'string' &&
    typeof x.ciphertextB64 === 'string'
  );
}
