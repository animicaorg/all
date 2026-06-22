/**
 * Secret-at-rest helpers for the Studio web IDE (GitHub PAT storage).
 *
 * AES-256-GCM via node:crypto. The 32-byte key is derived from
 * process.env.STUDIO_SECRET_KEY (scrypt over a fixed salt). If no key is set we
 * fall back to plain base64 with a one-time logged warning so local dev still
 * works — but production MUST set STUDIO_SECRET_KEY.
 *
 * Blob format (AES path): "v1:gcm:<iv_b64>:<tag_b64>:<ct_b64>".
 * Fallback format:        "v0:b64:<b64>".
 */
import crypto from 'node:crypto';

const ENV_KEY = process.env.STUDIO_SECRET_KEY || '';
let KEY = null;
let warned = false;

function key() {
  if (KEY) return KEY;
  if (!ENV_KEY) return null;
  // Deterministic 32-byte key from the env value. Fixed salt is fine here: the
  // env value itself is the secret, and we just need a stable AES key from it.
  KEY = crypto.scryptSync(ENV_KEY, 'animica-studio-ide-v1', 32);
  return KEY;
}

function warnOnce() {
  if (warned) return;
  warned = true;
  console.warn('[ide/secrets] STUDIO_SECRET_KEY is not set — GitHub tokens stored with base64 (NOT encrypted). Set STUDIO_SECRET_KEY in production.');
}

/** Encrypt a utf-8 string -> opaque blob string. */
export function encrypt(str) {
  const plain = String(str ?? '');
  const k = key();
  if (!k) {
    warnOnce();
    return 'v0:b64:' + Buffer.from(plain, 'utf8').toString('base64');
  }
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', k, iv);
  const ct = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return ['v1', 'gcm', iv.toString('base64'), tag.toString('base64'), ct.toString('base64')].join(':');
}

/** Decrypt a blob produced by encrypt(). Returns '' on failure. */
export function decrypt(blob) {
  if (!blob || typeof blob !== 'string') return '';
  if (blob.startsWith('v0:b64:')) {
    try { return Buffer.from(blob.slice(7), 'base64').toString('utf8'); }
    catch { return ''; }
  }
  if (blob.startsWith('v1:gcm:')) {
    const k = key();
    if (!k) return '';
    const [, , ivB64, tagB64, ctB64] = blob.split(':');
    try {
      const iv = Buffer.from(ivB64, 'base64');
      const tag = Buffer.from(tagB64, 'base64');
      const ct = Buffer.from(ctB64, 'base64');
      const decipher = crypto.createDecipheriv('aes-256-gcm', k, iv);
      decipher.setAuthTag(tag);
      return Buffer.concat([decipher.update(ct), decipher.final()]).toString('utf8');
    } catch {
      return '';
    }
  }
  return '';
}
