import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { suspendSubscription, activateSubscription, cancelSubscription } from '@/lib/paypal';
import { trackBillingEvent } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const SUB_STATUSES = ['PENDING', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD', 'SUSPENDED', 'CANCELED'];

// PayPal admin actions are advisory: OUR row is the entitlement authority, and a 422
// ("already suspended/cancelled at PayPal") or a transient API failure must never block the
// operator — the DB update proceeds and the problem surfaces as a warning in the response.
// Never silent: the warning travels to the console so a real desync gets noticed.
async function paypalSoft(
  call: () => Promise<{ ok: boolean; soft?: boolean; detail?: any }>,
): Promise<string | null> {
  try {
    const r = await call();
    if (r.ok) return null;
    return `PayPal did not apply the change (likely already in that state): ${JSON.stringify(r.detail ?? {})}`.slice(0, 400);
  } catch (e) {
    return `PayPal call failed: ${e instanceof Error ? e.message : String(e)}`.slice(0, 400);
  }
}

// GET /api/mkt/v1/billing/admin/subscriptions/[id] -> the row-expand detail: full
// subscription row + the last 20 processed webhook deliveries for its PayPal subscription
// (BillingEvent = the replay-guard audit trail) + the account's last 10 Worker runs.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    await requireAdmin(req);
    const row = await prisma.planSubscription.findUnique({
      where: { id: params.id },
      include: { account: { select: { id: true, address: true, displayName: true, createdAt: true } } },
    });
    if (!row) throw new ApiError(404, 'not_found', 'subscription not found');
    const [events, runs] = await Promise.all([
      row.paypalSubscriptionId
        ? prisma.billingEvent.findMany({
            where: { subscriptionId: row.paypalSubscriptionId },
            orderBy: { processedAt: 'desc' },
            take: 20,
            // payloadJson deliberately omitted: raw PayPal bodies are large and the summary
            // line is what the console renders.
            select: { id: true, eventType: true, resourceId: true, summary: true, processedAt: true },
          })
        : Promise.resolve([]),
      prisma.workerRun.findMany({
        where: { accountId: row.accountId },
        orderBy: { createdAt: 'desc' },
        take: 10,
        select: {
          id: true, status: true, trigger: true, startedAt: true, finishedAt: true,
          durationMs: true, error: true, createdAt: true,
          worker: { select: { id: true, name: true } },
        },
      }),
    ]);
    return ok({ subscription: row, events, runs });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/billing/admin/subscriptions/[id] {action, ...}
//   suspend            — suspend at PayPal (soft-fail warn) + row SUSPENDED (free limits)
//   reactivate         — activate at PayPal + row ACTIVE, dunning window cleared
//   cancel             — cancel at PayPal + row CANCELED (terminal) + canceledAt
//   set_status {status}— force a raw status (escape hatch; bypasses PayPal entirely)
//   note {note}        — record an operator note in lastError (empty note clears it)
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const admin = await requireAdmin(req);
    const body = await req.json().catch(() => ({}));
    const action = String(body.action ?? '');
    const row = await prisma.planSubscription.findUnique({ where: { id: params.id } });
    if (!row) throw new ApiError(404, 'not_found', 'subscription not found');

    const who = admin.via === 'token' ? 'admin-token' : admin.accountId!;
    let warning: string | null = null;
    let data: Record<string, unknown>;

    switch (action) {
      case 'suspend':
        if (row.paypalSubscriptionId) {
          warning = await paypalSoft(() =>
            suspendSubscription(row.paypalSubscriptionId!, `Suspended by Animica operator (${who})`));
        }
        data = { status: 'SUSPENDED' };
        break;
      case 'reactivate':
        if (row.paypalSubscriptionId) {
          warning = await paypalSoft(() =>
            activateSubscription(row.paypalSubscriptionId!, `Reactivated by Animica operator (${who})`));
        }
        data = { status: 'ACTIVE', graceUntil: null };
        trackBillingEvent('reactivation', { accountId: row.accountId, planKey: row.planKey, meta: { via: 'admin' } }).catch(() => {});
        break;
      case 'cancel':
        if (row.paypalSubscriptionId) {
          warning = await paypalSoft(() =>
            cancelSubscription(row.paypalSubscriptionId!, `Cancelled by Animica operator (${who})`));
        }
        // Same rule as the self-serve cancel: CANCELED keeps paid access until
        // currentPeriodEnd, so cancelling a SUSPENDED (clawback / abuse hold) row must not
        // hand those entitlements back for the rest of the period.
        data = {
          status: 'CANCELED',
          canceledAt: new Date(),
          ...(row.status === 'SUSPENDED' ? { currentPeriodEnd: new Date() } : {}),
        };
        if (row.status !== 'PENDING') {
          trackBillingEvent('cancel', { accountId: row.accountId, planKey: row.planKey, meta: { via: 'admin' } }).catch(() => {});
        }
        break;
      case 'set_status': {
        const status = String(body.status ?? '');
        if (!SUB_STATUSES.includes(status)) {
          throw new ApiError(400, 'bad_status', `status must be one of ${SUB_STATUSES.join(' | ')}`);
        }
        // Deliberately literal — the escape hatch does exactly what the operator said and
        // nothing else (no PayPal call, no side-writes).
        data = { status };
        break;
      }
      case 'note': {
        const note = typeof body.note === 'string' ? body.note.trim().slice(0, 1000) : '';
        data = { lastError: note ? `${note} — ${who}` : null };
        break;
      }
      default:
        throw new ApiError(400, 'bad_action', 'action must be suspend | reactivate | cancel | set_status | note');
    }

    const subscription = await prisma.planSubscription.update({ where: { id: row.id }, data: data as any });
    return ok({ subscription, ...(warning ? { warning } : {}) });
  } catch (e) {
    return err(e);
  }
}
