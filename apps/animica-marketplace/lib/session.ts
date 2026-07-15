import { createHmac, timingSafeEqual } from 'node:crypto';
import { cookies } from 'next/headers';
import { config } from './config';

// Stateless HMAC-signed session cookie for wallet-login (web UI). API clients use API keys instead.
const COOKIE = 'anm_mkt_session';
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

function sign(payload: string): string {
  return createHmac('sha256', config.sessionSecret).update(payload).digest('base64url');
}

export function issueSession(accountId: string): string {
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE;
  const payload = `${accountId}.${exp}`;
  return `${payload}.${sign(payload)}`;
}

export function verifySession(token: string | undefined): { accountId: string } | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  const [accountId, exp, mac] = parts;
  const payload = `${accountId}.${exp}`;
  const expected = sign(payload);
  try {
    if (!timingSafeEqual(Buffer.from(mac), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
  return { accountId };
}

export function setSessionCookie(accountId: string) {
  cookies().set(COOKIE, issueSession(accountId), {
    httpOnly: true, secure: true, sameSite: 'lax', path: '/', maxAge: MAX_AGE,
  });
}

export function clearSessionCookie() {
  cookies().delete(COOKIE);
}

export function currentAccountId(): string | null {
  const token = cookies().get(COOKIE)?.value;
  return verifySession(token)?.accountId ?? null;
}
