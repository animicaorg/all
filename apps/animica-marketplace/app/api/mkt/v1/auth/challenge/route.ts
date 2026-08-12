import { NextRequest } from 'next/server';
import {
  issueChallenge,
  issueChallengeV2,
  CHALLENGE_PURPOSES,
  CHALLENGE_AUDIENCE,
  type ChallengePurpose,
} from '@/lib/challenge';
import { isAnimicaAddress } from '@/lib/address';
import { SIGN_MESSAGE_DOMAIN } from '@/lib/config';
import { ok, err, ApiError, withCredentialedCors, credentialedPreflight } from '@/lib/api';

export const dynamic = 'force-dynamic';

// Preflight for the cross-origin Game Lab publish login (animica.io) fetching a devportal challenge.
export async function OPTIONS(req: NextRequest) {
  return credentialedPreflight(req, 'GET, OPTIONS');
}

// GET /api/mkt/v1/auth/challenge?address=anim1...[&purpose=devportal|store|subscribe|review][&<k>=<v>...]
// Returns a challenge string to sign with animica_signMessage.
//   - no `purpose`  -> v1 challenge (unscoped full-session login; existing web-UI connect flow).
//   - with `purpose` -> v2 challenge: purpose-bound, audience-bound, single-use. Extra query
//     params (anything other than address/purpose/aud) are bound INTO the signed string as
//     consent parameters (e.g. subscribe: listing/period/amount) and re-validated at verify.
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const address = sp.get('address')?.trim().toLowerCase() ?? '';
    if (!isAnimicaAddress(address)) throw new ApiError(400, 'bad_address', 'valid anim1 address required');

    const purpose = sp.get('purpose')?.trim();
    if (purpose) {
      if (!(CHALLENGE_PURPOSES as readonly string[]).includes(purpose)) {
        throw new ApiError(400, 'bad_purpose', `purpose must be one of ${CHALLENGE_PURPOSES.join(', ')}`);
      }
      // Bind any additional query params into the signed challenge. `issueChallengeV2` strictly
      // validates key/value shapes and rejects reserved keys, so junk fails closed with 400.
      const extra: Record<string, string> = {};
      for (const [k, v] of sp.entries()) {
        if (k === 'address' || k === 'purpose' || k === 'aud') continue;
        extra[k] = v;
      }
      let challenge: string;
      try {
        challenge = issueChallengeV2(address, purpose as ChallengePurpose, extra);
      } catch (e: any) {
        throw new ApiError(400, 'bad_challenge_params', e?.message ?? 'invalid challenge params');
      }
      return withCredentialedCors(req, ok({ address, purpose, challenge, domain: SIGN_MESSAGE_DOMAIN, aud: CHALLENGE_AUDIENCE }));
    }

    return withCredentialedCors(req, ok({ address, challenge: issueChallenge(address), domain: SIGN_MESSAGE_DOMAIN }));
  } catch (e) {
    return withCredentialedCors(req, err(e));
  }
}
