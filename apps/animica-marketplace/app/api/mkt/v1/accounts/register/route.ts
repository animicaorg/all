import { NextRequest } from 'next/server';
import { validateChallenge } from '@/lib/challenge';
import { verifyWalletLogin } from '@/lib/wallet-verify';
import { ensureAccount } from '@/lib/accounts';
import { createApiKey } from '@/lib/apikey';
import { API_SCOPES, type ApiScope } from '@/lib/config';
import { ok, err, ApiError } from '@/lib/api';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/accounts/register
// Agent/developer self-onboarding. Proves wallet ownership via a signed challenge, then returns an
// account + a first API key with the requested scopes. The raw key is shown EXACTLY ONCE.
// This is how an autonomous agent bootstraps itself into the marketplace + agent mesh.
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

    const requested: ApiScope[] = Array.isArray(body.scopes)
      ? body.scopes.filter((s: string) => (API_SCOPES as readonly string[]).includes(s))
      : ['read', 'use', 'buy'];

    const account = await ensureAccount(address, { isAgent: body.isAgent === true, displayName: body.displayName });
    const { raw, key } = await createApiKey(account.id, {
      name: body.keyName ?? 'agent-key',
      scopes: requested,
    });
    return ok({
      account: jsonSafe(account),
      apiKey: raw, // shown once
      keyId: key.id,
      scopes: key.scopes,
      note: 'Store apiKey securely — it is not retrievable again.',
    });
  } catch (e) {
    return err(e);
  }
}
