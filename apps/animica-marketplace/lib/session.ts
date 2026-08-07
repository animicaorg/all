import { createHmac, timingSafeEqual } from 'node:crypto';
import { cookies } from 'next/headers';
import { config, type ApiScope } from './config';
import type { ChallengePurpose } from './challenge';

// Stateless HMAC-signed session cookie for wallet-login (web UI). API clients use API keys instead.
//
// Two token shapes share one cookie:
//   legacy  accountId.exp.mac                 -> full-rights session (scopes ['*'])
//   scoped  accountId.exp.purpose.mac         -> purpose-scoped session (v2 challenge login)
// Purpose-scoped sessions exist because the shipped extension still blind-signs: a signature
// over a purpose=store challenge must never mint a session that can publish or withdraw.
// The purpose is INSIDE the MAC'd payload, so it cannot be stripped or swapped client-side.
const COOKIE = 'anm_mkt_session';
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

// Scopes granted per challenge purpose (design: dev-portal auth). Unknown purpose => reject.
export const SESSION_PURPOSE_SCOPES: Record<ChallengePurpose, ApiScope[]> = {
  devportal: ['publish', 'withdraw', 'read', 'workers'],
  store: ['read', 'buy', 'use'],
  subscribe: ['read', 'buy'],
  review: ['read'],
};

export interface SessionInfo {
  accountId: string;
  scopes: string[]; // ['*'] for legacy full sessions, purpose scopes otherwise
  purpose?: ChallengePurpose;
}

function sign(payload: string): string {
  return createHmac('sha256', config.sessionSecret).update(payload).digest('base64url');
}

export function issueSession(accountId: string, purpose?: ChallengePurpose): string {
  if (purpose && !(purpose in SESSION_PURPOSE_SCOPES)) throw new Error(`bad session purpose: ${purpose}`);
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE;
  const payload = purpose ? `${accountId}.${exp}.${purpose}` : `${accountId}.${exp}`;
  return `${payload}.${sign(payload)}`;
}

export function verifySession(token: string | undefined): SessionInfo | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3 && parts.length !== 4) return null;
  const mac = parts[parts.length - 1];
  const payload = parts.slice(0, -1).join('.');
  const expected = sign(payload);
  try {
    if (!timingSafeEqual(Buffer.from(mac), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  const [accountId, exp] = parts;
  if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
  if (parts.length === 3) return { accountId, scopes: ['*'] };
  const purpose = parts[2] as ChallengePurpose;
  const scopes = SESSION_PURPOSE_SCOPES[purpose];
  if (!scopes) return null; // fail-closed: MAC-valid but unknown purpose => no session
  return { accountId, scopes: [...scopes], purpose };
}

// `crossSite` is set ONLY for the cross-origin Game Lab publish login (animica.io -> animica.dev):
// a SameSite=Lax cookie is never sent on a cross-site fetch, so that flow needs SameSite=None to
// carry the session on the subsequent publish calls. Same-origin logins (dev portal, buyers) keep
// SameSite=Lax — their CSRF posture is unchanged. None requires Secure, which is already set.
export function setSessionCookie(accountId: string, purpose?: ChallengePurpose, opts?: { crossSite?: boolean }) {
  cookies().set(COOKIE, issueSession(accountId, purpose), {
    httpOnly: true, secure: true, sameSite: opts?.crossSite ? 'none' : 'lax', path: '/', maxAge: MAX_AGE,
  });
}

export function clearSessionCookie() {
  cookies().delete(COOKIE);
}

export function currentAccountId(): string | null {
  const token = cookies().get(COOKIE)?.value;
  return verifySession(token)?.accountId ?? null;
}
