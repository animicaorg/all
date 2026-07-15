import { createCipheriv, createDecipheriv, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import { config } from './config';

// AES-256-GCM sealing for social-account tokens at rest. The key comes from ANIMAL_TOKEN_KEY
// (32 bytes as hex/base64) when set; otherwise it is derived from SESSION_SECRET so the app works
// out of the box but is still bound to a per-deploy secret. Sealed values are opaque and are only
// ever unsealed by the localhost internal endpoint the posting engine calls — never in a response
// to a browser session.

function key(): Buffer {
  const raw = process.env.ANIMAL_TOKEN_KEY || '';
  if (raw) {
    // Accept 32-byte hex (64 chars) or base64/base64url.
    if (/^[0-9a-fA-F]{64}$/.test(raw)) return Buffer.from(raw, 'hex');
    const b = Buffer.from(raw, raw.includes('-') || raw.includes('_') ? 'base64url' : 'base64');
    if (b.length === 32) return b;
    // A set-but-malformed key must FAIL LOUDLY — never silently fall through to a derived key the
    // operator didn't intend (they'd believe tokens are protected by their passphrase when they're not).
    throw new Error('ANIMAL_TOKEN_KEY is set but malformed — provide 32 bytes as hex (64 chars) or base64.');
  }
  // No explicit key. Deriving from the session secret is only acceptable if that secret is a real,
  // per-deploy value. Refuse the globally-known dev default (would seal every token under a public
  // constant), and require an explicit key in production.
  if (config.sessionSecret === 'dev-insecure-secret') {
    throw new Error('ANIMAL_TOKEN_KEY (or a real SESSION_SECRET) is required — refusing the insecure default vault key.');
  }
  if (process.env.NODE_ENV === 'production') {
    throw new Error('ANIMAL_TOKEN_KEY must be set in production (32 bytes as hex or base64).');
  }
  return scryptSync(config.sessionSecret, 'animica-animal-vault-v1', 32);
}

export function seal(plaintext: string): string {
  if (!plaintext) return '';
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key(), iv);
  const ct = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `v1.${iv.toString('base64url')}.${tag.toString('base64url')}.${ct.toString('base64url')}`;
}

export function open(sealed: string | null | undefined): string {
  if (!sealed) return '';
  const parts = sealed.split('.');
  if (parts.length !== 4 || parts[0] !== 'v1') return '';
  try {
    const iv = Buffer.from(parts[1], 'base64url');
    const tag = Buffer.from(parts[2], 'base64url');
    const ct = Buffer.from(parts[3], 'base64url');
    const decipher = createDecipheriv('aes-256-gcm', key(), iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ct), decipher.final()]).toString('utf8');
  } catch {
    return '';
  }
}

// Constant-time compare for bearer tokens (avoid timing oracles on the internal endpoint).
export function constEq(a: string, b: string): boolean {
  const ab = Buffer.from(a || '');
  const bb = Buffer.from(b || '');
  if (ab.length !== bb.length) return false;
  try {
    return timingSafeEqual(ab, bb);
  } catch {
    return false;
  }
}
