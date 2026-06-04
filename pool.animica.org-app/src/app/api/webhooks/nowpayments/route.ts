// POST /app/api/webhooks/nowpayments — IPN endpoint.
//
// Verifies HMAC-SHA512 over the raw body, de-dupes by signature, maps to the
// Rental (by order_id for pay-in, or by payout id for owner-payout / refund),
// and advances the rental state machine. Always returns 200 so NOWPayments
// doesn't retry on our internal errors; the worker can reconcile from events.
import { NextResponse } from "next/server";
import { prisma } from "@/server/db";
import { verifyIpnSignature } from "@/server/nowpayments";
import { recordEvent, transition } from "@/server/rentals";
import { auditLog } from "@/lib/audit";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const sig = req.headers.get("x-nowpayments-sig");
  const raw = await req.text();
  if (!verifyIpnSignature(raw, sig)) {
    await auditLog({
      action: "webhook.nowpayments.rejected",
      entityType: "Webhook",
      metadata: { reason: "bad_signature" },
    });
    return NextResponse.json({ error: "bad_signature" }, { status: 401 });
  }

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }

  const orderId = (payload.order_id as string | undefined) ?? null;
  const payoutId =
    (payload.payout_id as string | undefined) ??
    (payload.batch_withdrawal_id as string | undefined) ??
    null;

  let rental = null;
  if (orderId) rental = await prisma.rental.findUnique({ where: { id: orderId } });
  if (!rental && payoutId) {
    rental = await prisma.rental.findFirst({
      where: { OR: [{ npPayoutId: payoutId }, { npRefundPayoutId: payoutId }] },
    });
  }
  if (!rental) {
    await auditLog({ action: "webhook.nowpayments.no_match", entityType: "Webhook", metadata: payload });
    return NextResponse.json({ ok: true, note: "no matching rental" });
  }

  const dedupeKey = `np:${sig}`;
  const { deduplicated } = await recordEvent({
    rentalId: rental.id,
    source: "nowpayments_ipn",
    dedupeKey,
    payload,
  });
  if (deduplicated) return NextResponse.json({ ok: true, note: "duplicate" });

  const paymentStatus = String(payload.payment_status ?? "");
  const payoutStatus = String(payload.batch_withdrawal_status ?? payload.status ?? "");
  const isRefund = payoutId != null && rental.npRefundPayoutId === payoutId;

  try {
    if (paymentStatus) {
      // ----- Pay-in (renter → escrow) -----
      if (paymentStatus === "confirming") {
        await transition({
          rentalId: rental.id,
          to: "PAYMENT_DETECTED",
          patch: { npPaymentId: payload.payment_id as string | undefined },
        });
      } else if (paymentStatus === "confirmed" || paymentStatus === "finished") {
        const actual = String(
          payload.actually_paid ?? payload.actually_paid_amount ?? payload.pay_amount ?? "0",
        );
        await transition({
          rentalId: rental.id,
          to: "PAYMENT_CONFIRMED",
          patch: {
            npPaymentId: payload.payment_id as string | undefined,
            payAmount: String(payload.pay_amount ?? ""),
            payCurrency: String(payload.pay_currency ?? ""),
            actuallyPaidUsd: String(payload.price_amount ?? rental.grossUsd),
          },
          reasonMetadata: { actuallyPaid: actual },
        });
        // The worker activates the rental (engages the pool redirect).
      } else if (paymentStatus === "failed" || paymentStatus === "expired") {
        await transition({
          rentalId: rental.id,
          to: paymentStatus === "expired" ? "EXPIRED" : "FAILED",
          reasonMetadata: { reason: `nowpayments.${paymentStatus}` },
        });
      }
    } else if (payoutStatus) {
      // ----- Payout (owner release) or refund (renter) -----
      const txid = (payload.txid ?? payload.hash ?? null) as string | null;
      if (payoutStatus === "PROCESSING" || payoutStatus === "SENDING" || payoutStatus === "WAITING") {
        // in-flight — no-op
      } else if (payoutStatus === "FINISHED") {
        if (isRefund) {
          await transition({ rentalId: rental.id, to: "REFUNDED", reasonMetadata: { txid } });
          await transition({ rentalId: rental.id, to: "COMPLETE" });
        } else {
          await transition({ rentalId: rental.id, to: "COMPLETE", reasonMetadata: { txid } });
        }
      } else if (payoutStatus === "FAILED" || payoutStatus === "REJECTED") {
        await transition({
          rentalId: rental.id,
          to: "NEEDS_ADMIN_REVIEW",
          reasonMetadata: { reason: `nowpayments.payout.${payoutStatus}`, isRefund },
        });
      }
    }
  } catch (err) {
    await auditLog({
      action: "webhook.nowpayments.transition_error",
      entityType: "Rental",
      entityId: rental.id,
      metadata: { error: String(err), payload },
    });
  }
  return NextResponse.json({ ok: true });
}
