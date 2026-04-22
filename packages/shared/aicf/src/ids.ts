import crypto from 'node:crypto';

export function createId(prefix: string): string {
  const id = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID().replace(/-/g, '')
    : crypto.randomBytes(16).toString('hex');
  return `${prefix}_${id.slice(0, 24)}`;
}

export function hashSecret(secret: string): string {
  return crypto.createHash('sha256').update(secret).digest('hex');
}

export function hashPassword(password: string, salt: string): string {
  return crypto.pbkdf2Sync(password, salt, 120_000, 32, 'sha256').toString('hex');
}

export function randomSecret(prefix: string): string {
  return `${prefix}_${crypto.randomBytes(32).toString('hex')}`;
}

export function nowIso(): string {
  return new Date().toISOString();
}
