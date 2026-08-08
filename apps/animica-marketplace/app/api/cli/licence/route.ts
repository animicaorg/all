import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { SubStatus } from '@prisma/client';
import { authenticate, err, ApiError } from '@/lib/api';
import { maskLicence, mintLicence } from '@/lib/cliLicence';

// POST /api/cli/licence  ->  the CLI licence key for the signed-in account
//
// Authenticated, unlike /entitlement: this MINTS a credential, so it needs to know
// who is asking. /entitlement only reads one, and the key it is handed is the
// credential.
//
// The key is deterministic per account, so calling this twice returns the same
// string rather than invalidating the copy already pasted into someone's CLI.
// There is nothing stored, so there is nothing to rotate — and nothing that keeps
// working after a subscription ends, because entitlement is checked live.
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in first');

    const sub = await prisma.planSubscription.findFirst({
      where: { accountId: ctx.accountId, status: { in: [SubStatus.ACTIVE, SubStatus.PAST_DUE, SubStatus.GRACE_PERIOD] } },
      orderBy: { updatedAt: 'desc' },
      select: { planKey: true, status: true },
    });

    // A key is issued either way. Withholding it from free accounts would mean a
    // new subscriber has to come back here after paying, and the key itself grants
    // nothing — /entitlement decides what it unlocks, every time it is used.
    const licence = mintLicence(ctx.accountId);
    return NextResponse.json({
      licence,
      masked: maskLicence(licence),
      tier: sub ? 'pro' : 'free',
      plan: sub?.planKey ?? null,
      install: 'animica chat  →  /licence ' + licence,
      note: sub
        ? 'Paste this into `animica chat` with /licence <key>.'
        : 'This key works now and turns into Pro the moment a plan is active — '
          + 'no need to come back for a new one.',
    }, { headers: { 'cache-control': 'no-store' } });
  } catch (e) {
    return err(e);
  }
}

export async function GET(req: NextRequest) {
  // Same thing on GET: someone debugging in a browser is already authenticated by
  // cookie, and refusing them a 405 helps nobody.
  return POST(req);
}
