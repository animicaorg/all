import { createHmac, timingSafeEqual } from 'node:crypto';
import { config } from './config';

// Short-lived HMAC download tokens for the store's entitlement-gated APK/digital-good
// downloads. The token only names WHO may fetch WHICH build and until WHEN — it is not a
// bearer entitlement by itself: entitlement is checked at mint (download-token route) AND
// re-checked at redemption (download route), so a refund/revocation between the two still
// blocks the download. Same stateless `payload.mac` shape as lib/session.ts.

export const DOWNLOAD_TOKEN_TTL_SECS = 600; // 10 min

function mac(payload: string): string {
  return createHmac('sha256', config.storeDownloadSecret).update(`anm-store-dl|${payload}`).digest('base64url');
}

export function mintDownloadToken(accountId: string, buildId: string): { token: string; expiresAt: Date } {
  const exp = Math.floor(Date.now() / 1000) + DOWNLOAD_TOKEN_TTL_SECS;
  const payload = `${accountId}.${buildId}.${exp}`;
  return { token: `${payload}.${mac(payload)}`, expiresAt: new Date(exp * 1000) };
}

// Fail-closed verify: wrong shape, bad MAC, or expired => null (never throws on hostile input).
export function verifyDownloadToken(token: string | null | undefined): { accountId: string; buildId: string } | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 4) return null;
  const [accountId, buildId, exp, sig] = parts;
  if (!accountId || !buildId || !/^\d{1,12}$/.test(exp)) return null;
  const expected = mac(`${accountId}.${buildId}.${exp}`);
  try {
    if (!timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
  return { accountId, buildId };
}
