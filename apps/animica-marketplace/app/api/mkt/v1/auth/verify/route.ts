import { NextRequest } from 'next/server';
import { validateChallenge } from '@/lib/challenge';
import { verifyWalletLogin } from '@/lib/wallet-verify';
import { ensureAccount } from '@/lib/accounts';
import { setSessionCookie } from '@/lib/session';
import { ok, err, ApiError } from '@/lib/api';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/auth/verify
// { address, challenge, signature (0x hex), publicKey (0x hex) } -> sets session cookie.
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const address = String(body.address ?? '').trim().toLowerCase();
    const challenge = String(body.challenge ?? '');
    if (!validateChallenge(challenge, address)) throw new ApiError(400, 'bad_challenge', 'challenge invalid or expired');
    const v = verifyWalletLogin({
      address,
      message: challenge,
      signatureHex: String(body.signature ?? ''),
      publicKeyHex: String(body.publicKey ?? ''),
    });
    if (!v.ok) throw new ApiError(401, 'bad_signature', v.reason ?? 'signature invalid');
    const account = await ensureAccount(address, { displayName: body.displayName });
    setSessionCookie(account.id);
    return ok({ account: jsonSafe(account) });
  } catch (e) {
    return err(e);
  }
}
