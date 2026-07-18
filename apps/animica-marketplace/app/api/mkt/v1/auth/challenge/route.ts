import { NextRequest } from 'next/server';
import { issueChallenge } from '@/lib/challenge';
import { isAnimicaAddress } from '@/lib/address';
import { ok, err, ApiError } from '@/lib/api';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/auth/challenge?address=anim1...
// Returns a challenge string to sign with animica_signMessage.
export async function GET(req: NextRequest) {
  try {
    const address = req.nextUrl.searchParams.get('address')?.trim().toLowerCase() ?? '';
    if (!isAnimicaAddress(address)) throw new ApiError(400, 'bad_address', 'valid anim1 address required');
    return ok({ address, challenge: issueChallenge(address), domain: 'animica:signMessage:' });
  } catch (e) {
    return err(e);
  }
}
