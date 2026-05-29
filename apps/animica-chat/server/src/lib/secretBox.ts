// Symmetric encryption for OAuth tokens at rest.
//
// Uses AES-256-GCM with a key derived from JWT_SECRET via HKDF-SHA256
// (purpose-separated by an "info" string per consumer). The ciphertext
// layout is base64url("v1." + iv(12) + tag(16) + ciphertext). The "v1."
// prefix gives us a clean migration path later.

import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from 'crypto';
import { env } from '../env';

const VERSION = 'v1.';

function deriveKey(info: string): Buffer {
  const ikm = Buffer.from(env.JWT_SECRET, 'utf8');
  return Buffer.from(hkdfSync('sha256', ikm, Buffer.alloc(0), Buffer.from(info), 32));
}

export function encrypt(plaintext: string, info = 'animica-chat-oauth-v1'): string {
  const key = deriveKey(info);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const ct = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return VERSION + Buffer.concat([iv, tag, ct]).toString('base64url');
}

export function decrypt(cipherText: string, info = 'animica-chat-oauth-v1'): string {
  if (!cipherText.startsWith(VERSION)) {
    throw new Error('unsupported ciphertext version');
  }
  const raw = Buffer.from(cipherText.slice(VERSION.length), 'base64url');
  const iv = raw.subarray(0, 12);
  const tag = raw.subarray(12, 28);
  const ct = raw.subarray(28);
  const key = deriveKey(info);
  const dec = createDecipheriv('aes-256-gcm', key, iv);
  dec.setAuthTag(tag);
  return Buffer.concat([dec.update(ct), dec.final()]).toString('utf8');
}
