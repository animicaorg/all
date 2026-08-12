import { createHmac, timingSafeEqual } from 'node:crypto';
import { config } from './config';

// Short-lived HMAC play tokens for a paid web game's license-gated PLAY surface. The exact
// twin of lib/downloadToken.ts (same secret, same stateless `payload.mac` shape) but bound to
// a LISTING (the bundle lives on Listing.bundleCid, not an AppBuild) and domain-separated so a
// download token can never be replayed as a play token and vice-versa. The token only names
// WHO may play WHICH listing until WHEN — it is not a bearer entitlement: checkPlayEntitlement
// runs at mint (play-token route) AND is re-checked at redemption (play route), so a refund or
// delist between the two still blocks play.

export const PLAY_TOKEN_TTL_SECS = 600; // 10 min

function mac(payload: string): string {
  return createHmac('sha256', config.storeDownloadSecret).update(`anm-store-play|${payload}`).digest('base64url');
}

export function mintPlayToken(accountId: string, listingId: string): { token: string; expiresAt: Date } {
  const exp = Math.floor(Date.now() / 1000) + PLAY_TOKEN_TTL_SECS;
  const payload = `${accountId}.${listingId}.${exp}`;
  return { token: `${payload}.${mac(payload)}`, expiresAt: new Date(exp * 1000) };
}

// Fail-closed verify: wrong shape, bad MAC, or expired => null (never throws on hostile input).
export function verifyPlayToken(token: string | null | undefined): { accountId: string; listingId: string } | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 4) return null;
  const [accountId, listingId, exp, sig] = parts;
  if (!accountId || !listingId || !/^\d{1,12}$/.test(exp)) return null;
  const expected = mac(`${accountId}.${listingId}.${exp}`);
  try {
    if (!timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
  return { accountId, listingId };
}
