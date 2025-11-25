/**
 * Encrypted keystore (WebCrypto AES-GCM) for @animica/sdk.
 *
 * - Derives an AES-256 key from a password via HKDF(SHA3-256)
 * - Encrypts arbitrary secret bytes (e.g., private key seed)
 * - Uses AES-GCM with 96-bit IV and 128-bit tag
 * - Portable between browser and Node (Node >= 18 has global webcrypto)
 *
 * The resulting JSON is deterministic given (password, salt, iv, secret).
 */

import { hkdf } from '@noble/hashes/hkdf'
import { sha3_256 } from '@noble/hashes/sha3'
import {
  utf8ToBytes,
  bytesToHex,
  hexToBytes,
  bytesToBase64url,
  base64urlToBytes,
} from '../utils/bytes'

/** Current keystore format version. */
export const KEYSTORE_VERSION = 1 as const

/** Metadata describing the cryptographic choices. */
export interface KeystoreCrypto {
  cipher: 'AES-GCM'
  kdf: 'HKDF-SHA3-256'
  /** Hex-encoded, 32-byte salt used for HKDF. */
  salt: string
  /** Hex-encoded, 12-byte IV used for AES-GCM. */
  iv: string
  /** AES-GCM tag length in bits. Default: 128. */
  tagLength: 128
}

/** JSON document persisted to disk/storage. */
export interface KeystoreDocument {
  version: typeof KEYSTORE_VERSION
  crypto: KeystoreCrypto
  /**
   * Base64url-encoded ciphertext (includes the authentication tag at the end,
   * as returned by SubtleCrypto). No padding, URL-safe alphabet.
   */
  ciphertext: string
  /** Optional application note. Not authenticated/enforced; avoid secrets. */
  note?: string
}

export type KeystoreLike = KeystoreDocument | string

export interface CreateOptions {
  /** Provide your own salt (32 bytes). Default: cryptographically random. */
  salt?: Uint8Array
  /** Provide your own IV (12 bytes). Default: cryptographically random. */
  iv?: Uint8Array
  /** Additional associated data bound by AES-GCM (not stored). */
  associatedData?: Uint8Array
  /** Optional note to store alongside (NOT secret). */
  note?: string
}

export interface OpenOptions {
  /** Additional associated data that was used during encryption. */
  associatedData?: Uint8Array
}

/**
 * Create a new encrypted keystore document from secret bytes.
 *
 * @param secret - bytes to encrypt (e.g., 32-byte seed)
 * @param password - user password/passphrase (NFKD normalized)
 * @param opts - salts/IVs/associated data overrides
 */
export async function createKeystore(
  secret: Uint8Array,
  password: string,
  opts: CreateOptions = {}
): Promise<KeystoreDocument> {
  assertBytes('secret', secret)
  const salt = opts.salt ?? randomBytes(32)
  const iv = opts.iv ?? randomBytes(12)
  const tagLength = 128 as const

  const key = await deriveAesKey(password, salt)

  const cipherText = await aesGcmEncrypt(key, iv, secret, opts.associatedData, tagLength)

  const doc: KeystoreDocument = {
    version: KEYSTORE_VERSION,
    crypto: {
      cipher: 'AES-GCM',
      kdf: 'HKDF-SHA3-256',
      salt: bytesToHex(salt),
      iv: bytesToHex(iv),
      tagLength
    },
    ciphertext: bytesToBase64url(cipherText),
    note: opts.note
  }

  // Best-effort wipe of sensitive buffers
  wipe(secret)
  wipe(salt)
  wipe(iv)
  wipe(cipherText)

  return doc
}

/**
 * Decrypt a keystore document with a password and return the plaintext bytes.
 */
export async function openKeystore(
  docLike: KeystoreLike,
  password: string,
  opts: OpenOptions = {}
): Promise<Uint8Array> {
  const doc = typeof docLike === 'string' ? parseKeystore(docLike) : docLike
  validateKeystore(doc)

  const salt = hexToBytes(doc.crypto.salt)
  const iv = hexToBytes(doc.crypto.iv)
  const ciphertext = base64urlToBytes(doc.ciphertext)
  const key = await deriveAesKey(password, salt)

  try {
    const pt = await aesGcmDecrypt(
      key,
      iv,
      ciphertext,
      opts.associatedData,
      doc.crypto.tagLength
    )
    return pt
  } catch (err) {
    // Unify error shape for callers (bad password, tampered doc, wrong AAD)
    throw new Error('Failed to decrypt keystore (bad password or corrupted data)')
  } finally {
    wipe(salt)
    wipe(iv)
    wipe(ciphertext)
  }
}

/**
 * Change the password by decrypting and re-encrypting with a new one.
 * Optionally rotates salt/iv automatically.
 */
export async function changePassword(
  docLike: KeystoreLike,
  oldPassword: string,
  newPassword: string,
  opts?: { associatedData?: Uint8Array }
): Promise<KeystoreDocument> {
  const secret = await openKeystore(docLike, oldPassword, { associatedData: opts?.associatedData })
  try {
    return await createKeystore(secret, newPassword, { associatedData: opts?.associatedData })
  } finally {
    wipe(secret)
  }
}

/** Serialize keystore to a pretty JSON string. */
export function stringifyKeystore(doc: KeystoreDocument): string {
  return JSON.stringify(doc, null, 2)
}

/** Parse keystore JSON string. */
export function parseKeystore(json: string): KeystoreDocument {
  let obj: unknown
  try {
    obj = JSON.parse(json)
  } catch {
    throw new Error('Invalid keystore JSON')
  }
  validateKeystore(obj as any)
  return obj as KeystoreDocument
}

/** Type guard and structural checks. Throws on errors. */
export function validateKeystore(doc: KeystoreDocument): asserts doc is KeystoreDocument {
  if (!doc || typeof doc !== 'object') throw new Error('Keystore must be an object')
  if ((doc as any).version !== KEYSTORE_VERSION) throw new Error('Unsupported keystore version')
  const c = (doc as any).crypto
  if (!c || typeof c !== 'object') throw new Error('Keystore.crypto missing')
  if (c.cipher !== 'AES-GCM') throw new Error('Unsupported cipher')
  if (c.kdf !== 'HKDF-SHA3-256') throw new Error('Unsupported KDF')
  if (typeof c.salt !== 'string' || !/^[0-9a-fA-F]+$/.test(c.salt) || c.salt.length !== 64) {
    throw new Error('crypto.salt must be 32-byte hex')
  }
  if (typeof c.iv !== 'string' || !/^[0-9a-fA-F]+$/.test(c.iv) || c.iv.length !== 24) {
    throw new Error('crypto.iv must be 12-byte hex')
  }
  if (c.tagLength !== 128) throw new Error('Unsupported tag length')
  if (typeof (doc as any).ciphertext !== 'string') throw new Error('ciphertext must be a string')
}

// ──────────────────────────────────────────────────────────────────────────────
// Internals
// ──────────────────────────────────────────────────────────────────────────────

function normalizeNFKD(s: string): string {
  try { return s.normalize('NFKD') } catch { return s }
}

async function deriveAesKey(password: string, salt: Uint8Array): Promise<CryptoKey> {
  const info = utf8ToBytes('omni-sdk keystore v1')
  const ikm = utf8ToBytes(normalizeNFKD(password))
  const keyBytes = hkdf(sha3_256, ikm, salt, info, 32) // 32 bytes for AES-256

  try {
    const key = await subtle().importKey(
      'raw',
      keyBytes,
      { name: 'AES-GCM', length: 256 },
      false, // not extractable
      ['encrypt', 'decrypt']
    )
    wipe(keyBytes)
    wipe(ikm)
    return key
  } catch (e) {
    wipe(keyBytes)
    wipe(ikm)
    throw new Error('WebCrypto importKey(AES-GCM) failed: ' + String(e))
  }
}

async function aesGcmEncrypt(
  key: CryptoKey,
  iv: Uint8Array,
  plaintext: Uint8Array,
  aad?: Uint8Array,
  tagLength: number = 128
): Promise<Uint8Array> {
  const algo: AesGcmParams = { name: 'AES-GCM', iv, tagLength, additionalData: aad }
  const ct = new Uint8Array(await subtle().encrypt(algo, key, plaintext))
  return ct
}

async function aesGcmDecrypt(
  key: CryptoKey,
  iv: Uint8Array,
  ciphertext: Uint8Array,
  aad?: Uint8Array,
  tagLength: number = 128
): Promise<Uint8Array> {
  const algo: AesGcmParams = { name: 'AES-GCM', iv, tagLength, additionalData: aad }
  const pt = new Uint8Array(await subtle().decrypt(algo, key, ciphertext))
  return pt
}

function subtle(): SubtleCrypto {
  // Browser: window.crypto.subtle
  // Node >= 18: globalThis.crypto.subtle
  const c: any = (globalThis as any).crypto
  if (!c || !c.subtle) {
    throw new Error('WebCrypto unavailable: globalThis.crypto.subtle is required')
  }
  return c.subtle as SubtleCrypto
}

function randomBytes(len: number): Uint8Array {
  const out = new Uint8Array(len)
  const c: any = (globalThis as any).crypto
  if (!c || typeof c.getRandomValues !== 'function') {
    throw new Error('Secure RNG unavailable: globalThis.crypto.getRandomValues missing')
  }
  c.getRandomValues(out)
  return out
}

function assertBytes(name: string, x: unknown): asserts x is Uint8Array {
  if (!(x instanceof Uint8Array)) {
    throw new Error(`${name} must be Uint8Array`)
  }
}

function wipe(a: Uint8Array | undefined | null) {
  if (!a) return
  try { a.fill(0) } catch { /* ignore */ }
}

export default {
  createKeystore,
  openKeystore,
  changePassword,
  stringifyKeystore,
  parseKeystore,
  validateKeystore,
  KEYSTORE_VERSION,
}
