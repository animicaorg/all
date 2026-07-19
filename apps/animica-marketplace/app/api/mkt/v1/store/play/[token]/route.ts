import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { checkPlayEntitlement } from '@/lib/entitlement';
import { verifyPlayToken } from '@/lib/playToken';
import { getObject } from '@/lib/storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/play/[token] -> serve a paid web game's self-contained HTML bundle so it
// PLAYS in an iframe. The twin of the download/[token] route: the token (10-min HMAC, minted by
// play-token) names WHO plays WHICH listing; entitlement is RE-checked here so a refund/delist
// between mint and redemption still blocks. Served under the same `sandbox allow-scripts ...`
// CSP the public content route uses (opaque origin: no storage/cookies/network — matches Forge's
// play sandbox), but with cache-control: no-store so paid bytes are never cached by a shared
// proxy. FREE games can also flow through here, but they may equally use the public content CID
// route directly; PAID games MUST only ever be served here.

const PLAY_CSP = 'sandbox allow-scripts allow-forms allow-popups allow-modals';

function fail(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message } }, { status });
}

export async function GET(_req: NextRequest, { params }: { params: { token: string } }) {
  try {
    const t = verifyPlayToken(params.token);
    if (!t) return fail(403, 'bad_token', 'invalid or expired play token');

    const listing = await prisma.listing.findUnique({
      where: { id: t.listingId },
      select: {
        id: true,
        ownerId: true,
        status: true,
        bundleCid: true,
        prices: { where: { active: true }, select: { model: true } },
      },
    });
    if (!listing) return fail(404, 'not_found', 'game not found');
    if (listing.status === 'DELISTED') return fail(410, 'not_available', 'game is no longer available');
    if (!listing.bundleCid) return fail(410, 'no_bundle', 'no playable bundle for this listing');

    const ent = await checkPlayEntitlement(t.accountId, listing);
    if (!ent.entitled) return fail(403, 'not_entitled', 'entitlement no longer active');

    const obj = await getObject(listing.bundleCid);
    if (!obj) return fail(410, 'gone', 'bundle content no longer available');

    // Force text/html for the play surface regardless of the stored mime, so the bundle always
    // renders as a game; the sandbox CSP opaque-origins it exactly like the Forge/content sandbox.
    return new NextResponse(new Uint8Array(obj.bytes), {
      status: 200,
      headers: {
        'content-type': 'text/html; charset=utf-8',
        'content-security-policy': PLAY_CSP,
        'x-content-type-options': 'nosniff',
        'cache-control': 'no-store',
        'referrer-policy': 'no-referrer',
      },
    });
  } catch {
    return fail(500, 'internal', 'play failed');
  }
}
