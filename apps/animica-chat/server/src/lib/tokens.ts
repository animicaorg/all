// Token helpers: opaque random tokens for sessions + magic links,
// stored as SHA-256 hashes server-side so a DB leak can't be replayed.

import { createHash, randomBytes } from 'crypto';

export function newOpaqueToken(byteLength = 32): string {
  return randomBytes(byteLength).toString('base64url');
}

export function hashToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

export function hashIp(ip: string | undefined): string | null {
  if (!ip) return null;
  return createHash('sha256').update(ip).digest('hex').slice(0, 32);
}
