import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { cancelSubscription } from '@/lib/paypal';
import { CURRENT_STATES } from '@/lib/planConfig';
import { trackBillingEvent } from '@/lib/plan';
import { sendMailSafe } from '@/lib/hireMail';
import { HIRE_NOTIFY_EMAIL } from '@/lib/hire';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Cancel the account's live subscription. Access continues until currentPeriodEnd (the
// already-paid period); nothing the user built is ever deleted — resources over the free
// limits are shelved (PLAN_LIMIT / SUSPENDED) by the enforcement pass, not destroyed.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in first');
    requireScope(ctx, 'buy');

    const rows = await prisma.planSubscription.findMany({
      where: { accountId: ctx.accountId, status: { in: [...(CURRENT_STATES as any), 'PENDING'] } },
      orderBy: { updatedAt: 'desc' },
    });
    if (rows.length === 0) throw new ApiError(404, 'not_found', 'no active subscription');

    let canceled = 0;
    const warnings: string[] = [];
    for (const row of rows) {
      let payPalError: string | null = null;
      if (row.paypalSubscriptionId) {
        // A swallowed failure here means PayPal keeps charging a subscription we show as
        // cancelled. 422 is the soft "already in that state" case; anything else is recorded
        // on the row and emailed to the operator so the billing desk can reconcile.
        try {
          await cancelSubscription(row.paypalSubscriptionId, 'Cancelled by the subscriber on animica.dev');
        } catch (e) {
          payPalError = (e as Error).message.slice(0, 400);
          warnings.push('PayPal did not confirm the cancellation — our support team has been notified.');
          sendMailSafe({
            to: HIRE_NOTIFY_EMAIL,
            subject: `Animica subscriptions: PayPal cancel FAILED for ${row.paypalSubscriptionId}`,
            text: `A subscriber cancelled on animica.dev but the PayPal cancel call failed — they may still be charged.\n\nAccount: ${ctx.accountId}\nPlan: ${row.planKey}\nSubscription: ${row.paypalSubscriptionId}\nError: ${payPalError}\n\nCancel it manually in PayPal, then check https://animica.dev/admin/billing`,
          }).catch(() => {});
        }
      }
      await prisma.planSubscription.update({
        where: { id: row.id },
        data: {
          status: 'CANCELED',
          canceledAt: new Date(),
          // A SUSPENDED row is a clawback (refund/chargeback/dispute) or an operator hold.
          // CANCELED keeps paid access until currentPeriodEnd, so carrying that date over
          // would hand back the very entitlements the suspension revoked — end it now.
          ...(row.status === 'SUSPENDED' ? { currentPeriodEnd: new Date() } : {}),
          ...(payPalError ? { lastError: `paypal cancel failed: ${payPalError}` } : {}),
        },
      });
      canceled++;
      if (row.status !== 'PENDING') {
        trackBillingEvent('cancel', { accountId: ctx.accountId, planKey: row.planKey, meta: { via: 'self-serve' } }).catch(() => {});
      }
    }
    return ok({ canceled, ...(warnings.length ? { warning: warnings[0] } : {}) });
  } catch (e) {
    return err(e);
  }
}
