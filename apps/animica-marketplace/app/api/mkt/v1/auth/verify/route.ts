import { NextRequest } from 'next/server';
import {
  validateChallenge,
  consumeChallengeV2,
  CHALLENGE_PURPOSES,
  type ChallengePurpose,
} from '@/lib/challenge';
import { verifyWalletLogin } from '@/lib/wallet-verify';
import { ensureAccount } from '@/lib/accounts';
import { setSessionCookie, SESSION_PURPOSE_SCOPES } from '@/lib/session';
import { ok, err, ApiError, withCredentialedCors, credentialedOrigin, credentialedPreflight } from '@/lib/api';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// Preflight for the cross-origin Game Lab publish login (animica.io). Same-origin dev-portal /
// buyer logins never preflight this route.
export async function OPTIONS(req: NextRequest) {
  return credentialedPreflight(req, 'POST, OPTIONS');
}

// POST /api/mkt/v1/auth/verify
// { address, challenge, signature (0x hex), publicKey (0x hex), purpose? } -> sets session cookie.
//   - v1 challenge (`animica-login|<addr>|<ts>|<mac>`)  -> unscoped full-session (scopes ['*']).
//   - v2 challenge (`animica-login|v2|purpose=...`)      -> purpose-scoped session; the challenge
//     is validated against the claimed purpose + audience and burned single-use, so a signature
//     obtained for one purpose can never be replayed into another session.
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const address = String(body.address ?? '').trim().toLowerCase();
    const challenge = String(body.challenge ?? '');
    // A cross-origin (allow-listed) login is the Game Lab publish flow: the session cookie must be
    // SameSite=None to survive the subsequent cross-site publish fetches. Same-origin stays Lax.
    const crossSite = !!credentialedOrigin(req);

    // ── v2: purpose-bound, single-use, purpose-scoped session ──────────────────
    if (challenge.startsWith('animica-login|v2|')) {
      const purpose = String(body.purpose ?? '');
      if (!(CHALLENGE_PURPOSES as readonly string[]).includes(purpose)) {
        throw new ApiError(400, 'bad_purpose', `purpose must be one of ${CHALLENGE_PURPOSES.join(', ')}`);
      }
      // Verify the wallet signature BEFORE burning, so a bad signature never spends the challenge.
      const v = verifyWalletLogin({
        address,
        message: challenge,
        signatureHex: String(body.signature ?? ''),
        publicKeyHex: String(body.publicKey ?? ''),
      });
      if (!v.ok) throw new ApiError(401, 'bad_signature', v.reason ?? 'signature invalid');
      // Validate purpose/audience/timestamp/mac AND atomically burn (single-use replay defense).
      const c = await consumeChallengeV2(challenge, address, purpose as ChallengePurpose);
      if (!c.ok) throw new ApiError(400, 'bad_challenge', c.reason ?? 'challenge invalid or expired');
      const account = await ensureAccount(address, { displayName: body.displayName });
      setSessionCookie(account.id, purpose as ChallengePurpose, { crossSite });
      return withCredentialedCors(req, ok({
        account: jsonSafe(account),
        purpose,
        scopes: SESSION_PURPOSE_SCOPES[purpose as ChallengePurpose],
      }));
    }

    // ── v1: unscoped full-session login (existing web-UI connect flow) ─────────
    if (!validateChallenge(challenge, address)) throw new ApiError(400, 'bad_challenge', 'challenge invalid or expired');
    const v = verifyWalletLogin({
      address,
      message: challenge,
      signatureHex: String(body.signature ?? ''),
      publicKeyHex: String(body.publicKey ?? ''),
    });
    if (!v.ok) throw new ApiError(401, 'bad_signature', v.reason ?? 'signature invalid');
    const account = await ensureAccount(address, { displayName: body.displayName });
    setSessionCookie(account.id, undefined, { crossSite });
    return withCredentialedCors(req, ok({ account: jsonSafe(account) }));
  } catch (e) {
    return withCredentialedCors(req, err(e));
  }
}
