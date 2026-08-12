import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { SubStatus } from '@prisma/client';
import { CLI_ENTITLEMENTS, verifyLicence } from '@/lib/cliLicence';

// POST /api/cli/entitlement   { licence }  ->  what this key may do
//
// Called by `animica chat` (agent_runtime/entitlements.py). Unauthenticated by
// design: the licence key IS the credential, and requiring a session as well
// would mean the CLI could not check an entitlement without a browser login.
//
// Two behaviours worth stating, because the CLI depends on them:
//
//  * A bad or unknown key answers 200 with `active: false`, not 401. The CLI
//    treats "not active" as the free tier and carries on; a 401 would look like a
//    transport failure and send it down the offline-grace path instead.
//  * The limits are returned in the body rather than assumed by the client, so
//    they can be tuned here without shipping a new CLI. The CLI only falls back
//    to its own constants when this call fails outright.
//
// Deliberately not cached: an entitlement whose answer is stale is an entitlement
// that keeps working after a cancellation.
export const dynamic = 'force-dynamic';

// From the SubStatus enum's own comments in prisma/schema.prisma:
//   PENDING      "never granted entitlements"
//   PAST_DUE     "paid limits kept, user warned"
//   GRACE_PERIOD "dunning window running out"  — still paid
//   SUSPENDED    "free limits, Workers paused" — explicitly NOT paid
//   CANCELED     terminal
// So SUSPENDED and PENDING are absent on purpose. Treating SUSPENDED as paid would
// contradict the schema and hand out limits the rest of the platform has revoked.
const PAID_STATUSES: SubStatus[] = [SubStatus.ACTIVE, SubStatus.PAST_DUE, SubStatus.GRACE_PERIOD];

function free(reason: string) {
  return NextResponse.json(
    { active: false, tier: 'free', reason, ...CLI_ENTITLEMENTS.free },
    { headers: { 'cache-control': 'no-store' } },
  );
}

export async function POST(req: NextRequest) {
  let licence: unknown = null;
  try {
    licence = (await req.json())?.licence;
  } catch {
    return free('request body must be JSON with a "licence" field');
  }

  const accountId = verifyLicence(typeof licence === 'string' ? licence : null);
  // Every rejection reads the same on purpose — malformed, wrong signature and
  // unknown account are indistinguishable, so a key cannot be used to probe for
  // valid account ids.
  if (!accountId) return free('licence key is not valid');

  let sub: { status: string; planKey: string; currentPeriodEnd: Date | null;
             graceUntil: Date | null } | null = null;
  try {
    sub = await prisma.planSubscription.findFirst({
      where: { accountId, status: { in: PAID_STATUSES } },
      orderBy: { updatedAt: 'desc' },
      select: { status: true, planKey: true, currentPeriodEnd: true, graceUntil: true },
    });
  } catch (e: unknown) {
    // A database blip must not hand out the paid tier, and must not look like a
    // transport failure either — the CLI has its own grace window for that, keyed
    // on a check that never completed. This one completed and found nothing.
    return NextResponse.json(
      { active: false, tier: 'free', reason: 'entitlement lookup unavailable',
        ...CLI_ENTITLEMENTS.free },
      { status: 503, headers: { 'cache-control': 'no-store' } },
    );
  }

  if (!sub) return free('no active subscription on this account');

  // PAST_DUE keeps working until the grace window closes: a card that failed this
  // morning should not stop someone mid-task, but it should not last forever.
  if (sub.status === SubStatus.PAST_DUE || sub.status === SubStatus.GRACE_PERIOD) {
    const until = sub.graceUntil ?? sub.currentPeriodEnd;
    if (!until || until.getTime() < Date.now()) {
      return free('subscription payment is overdue');
    }
  }

  return NextResponse.json(
    {
      active: true,
      tier: 'pro',                 // the CLI has one paid tier; plan names differ
      plan: sub.planKey,
      reason: `subscription ${sub.status.toLowerCase()}`,
      renews_at: sub.currentPeriodEnd?.toISOString() ?? null,
      ...CLI_ENTITLEMENTS.paid,
    },
    { headers: { 'cache-control': 'no-store' } },
  );
}

// A GET is what someone will try first when debugging. Answer it usefully instead
// of with a bare 405.
export async function GET() {
  return NextResponse.json({
    endpoint: 'POST /api/cli/entitlement',
    body: { licence: 'anmcli_…' },
    get_your_key: 'POST /api/cli/licence while signed in',
    docs: 'https://animica.dev/#cli',
  }, { headers: { 'cache-control': 'no-store' } });
}
