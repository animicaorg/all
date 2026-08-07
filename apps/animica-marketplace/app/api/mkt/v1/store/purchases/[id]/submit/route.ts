import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/purchases/[id]/submit { txid } -> record the broadcast txid for
// the payment watcher. Idempotent: resubmitting the same txid is a no-op success, and a
// different txid may replace an UNVERIFIED one (wallet rebroadcast). This route only
// RECORDS — verification/crediting is exclusively the watcher's job (finality + execution
// proof + baseline reconciliation), never done here.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'buy');
    const body = await req.json();
    const txid = normalizeTxid(body.txid);
    if (!txid) throw new ApiError(400, 'bad_txid', 'txid must be 64 hex chars');

    const purchase = await prisma.purchase.findUnique({
      where: { id: params.id },
      include: { paymentIntent: true },
    });
    if (!purchase || purchase.buyerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'purchase not found');
    const intent = purchase.paymentIntent;
    if (!intent) throw new ApiError(409, 'no_intent', 'purchase has no wallet payment intent');

    // Already settled — idempotent success regardless of the txid sent now.
    if (purchase.status === 'ACTIVE' || intent.verifiedAt) {
      return ok({ purchaseId: purchase.id, status: purchase.status, submittedTxid: intent.submittedTxid, recorded: false });
    }
    if (purchase.status !== 'PENDING_PAYMENT') {
      throw new ApiError(409, 'bad_state', `purchase is ${purchase.status}`);
    }
    if (intent.submittedTxid === txid) {
      return ok({ purchaseId: purchase.id, status: purchase.status, submittedTxid: txid, recorded: false });
    }
    // Single-use intent: past its 30-min expiry nothing may be recorded (the watcher never
    // credits expired intents either — fail-closed on both sides).
    if (intent.expiresAt < new Date()) throw new ApiError(410, 'intent_expired', 'payment intent expired');

    await prisma.storePaymentIntent.update({
      where: { id: intent.id },
      data: { submittedTxid: txid, failReason: null },
    });
    return ok({ purchaseId: purchase.id, status: purchase.status, submittedTxid: txid, recorded: true });
  } catch (e) {
    return err(e);
  }
}

function normalizeTxid(input: unknown): string | null {
  if (typeof input !== 'string') return null;
  const t = (input.startsWith('0x') || input.startsWith('0X') ? input.slice(2) : input).toLowerCase().trim();
  return /^[0-9a-f]{64}$/.test(t) ? t : null;
}
