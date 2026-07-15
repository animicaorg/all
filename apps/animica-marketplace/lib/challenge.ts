import { createHmac, timingSafeEqual } from 'node:crypto';
import { config } from './config';

// Stateless wallet-login challenge: no DB/Redis needed. The challenge embeds the claimed address +
// a timestamp + an HMAC. At verify time we re-derive the HMAC and check freshness. The wallet signs
// the WHOLE challenge string via animica_signMessage; the server then ml_dsa_65-verifies it.

const TTL_MS = 5 * 60 * 1000;

export function issueChallenge(address: string): string {
  const ts = Date.now();
  const payload = `animica-login|${address}|${ts}`;
  const mac = createHmac('sha256', config.sessionSecret).update(payload).digest('base64url');
  return `${payload}|${mac}`;
}

export function validateChallenge(challenge: string, address: string): boolean {
  const parts = challenge.split('|');
  if (parts.length !== 4) return false;
  const [tag, addr, ts, mac] = parts;
  if (tag !== 'animica-login' || addr !== address) return false;
  if (Date.now() - Number(ts) > TTL_MS) return false;
  const expected = createHmac('sha256', config.sessionSecret).update(`${tag}|${addr}|${ts}`).digest('base64url');
  try {
    return timingSafeEqual(Buffer.from(mac), Buffer.from(expected));
  } catch {
    return false;
  }
}
