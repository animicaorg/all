import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr } from '@/lib/hire';
import { requireHireAdmin } from '@/lib/hireAuth';
import { suspendSubscription, cancelSubscription, activateSubscription } from '@/lib/paypal';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Admin actions on one order (id = human orderId). Status actions also drive the PayPal
// subscription; a PayPal soft-failure (already in target state) still applies locally and is
// reported back as a warning instead of blocking the operator.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    await requireHireAdmin(req);
    const order = await prisma.servicesOrder.findUnique({ where: { orderId: params.id } });
    if (!order) throw new ApiError(404, 'not_found', 'order not found');

    let body: any = {};
    try { body = await req.json(); } catch {}
    const action = String(body?.action || '');
    let warning: string | null = null;
    const patch: Record<string, unknown> = {};

    const drivePaypal = async (fn: (id: string) => Promise<{ ok: boolean; soft?: boolean; detail?: any }>) => {
      if (!order.paypalSubscriptionId) return;
      try {
        const r = await fn(order.paypalSubscriptionId);
        if (!r.ok) warning = 'PayPal reported the subscription was already in that state.';
      } catch (e) {
        warning = `PayPal call failed: ${(e as Error).message} — local status updated anyway.`;
      }
    };

    switch (action) {
      case 'complete':
        patch.deploymentStatus = 'active';
        break;
      case 'suspend':
        await drivePaypal(suspendSubscription);
        patch.deploymentStatus = 'suspended';
        if (order.paymentStatus === 'active') patch.paymentStatus = 'suspended';
        break;
      case 'cancel':
        await drivePaypal(cancelSubscription);
        patch.deploymentStatus = 'cancelled';
        if (order.paymentStatus !== 'quote') patch.paymentStatus = 'cancelled';
        break;
      case 'reactivate':
        await drivePaypal(activateSubscription);
        patch.deploymentStatus = order.deploymentStatus === 'suspended' ? 'active' : 'pending_setup';
        if (order.paymentStatus !== 'quote') patch.paymentStatus = 'active';
        break;
      case 'note': {
        patch.adminNotes = cleanStr(body?.note, 4000, 'note') ?? null;
        break;
      }
      case 'server_dates': {
        // Operator bookkeeping: manually-entered server start/end (renewal) dates + label.
        const parseDate = (v: unknown, field: string): Date | null => {
          if (v == null || v === '') return null;
          const d = new Date(String(v));
          if (Number.isNaN(d.getTime())) throw new ApiError(400, 'invalid', `bad ${field}`);
          return d;
        };
        patch.serverStartAt = parseDate(body?.serverStartAt, 'serverStartAt');
        patch.serverEndAt = parseDate(body?.serverEndAt, 'serverEndAt');
        patch.serverLabel = cleanStr(body?.serverLabel, 200, 'serverLabel') ?? null;
        break;
      }
      case 'set_status': {
        // Escape hatch for odd states; values validated against the documented vocabulary.
        const ps = cleanStr(body?.paymentStatus, 20, 'paymentStatus');
        const ds = cleanStr(body?.deploymentStatus, 20, 'deploymentStatus');
        const PS = ['draft', 'pending', 'active', 'suspended', 'cancelled', 'failed', 'quote'];
        const DS = ['awaiting_payment', 'pending_setup', 'active', 'suspended', 'cancelled', 'quote_requested'];
        if (ps) { if (!PS.includes(ps)) throw new ApiError(400, 'invalid', 'bad paymentStatus'); patch.paymentStatus = ps; }
        if (ds) { if (!DS.includes(ds)) throw new ApiError(400, 'invalid', 'bad deploymentStatus'); patch.deploymentStatus = ds; }
        break;
      }
      default:
        throw new ApiError(400, 'invalid', 'unknown action');
    }

    const updated = await prisma.servicesOrder.update({ where: { id: order.id }, data: patch });
    return ok({ order: updated, warning });
  } catch (e) {
    return err(e);
  }
}
