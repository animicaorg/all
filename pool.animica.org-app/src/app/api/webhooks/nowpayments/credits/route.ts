// POST /app/api/webhooks/nowpayments/credits — IPN for credit purchases.
//
// Separate from the rental IPN: maps order_id → CreditPurchase and grants
// credit when the payment finishes. Credit is granted exactly once (the
// billing layer guards with an atomic creditedAt claim), so duplicate IPN
// re-deliveries are safe. Always returns 200 so NOWPayments doesn't retry on
// our internal errors.

import { NextResponse } from "next/server";
import { auditLog } from "@/lib/audit";
import {
  markCreditPurchaseFailed,
  markCreditPurchasePaid,
} from "@/server/billing";
import { verifyIpnSignature } from "@/server/nowpayments";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const sig = req.headers.get("x-nowpayments-sig");
  const raw = await req.text();
  if (!verifyIpnSignature(raw, sig)) {
    await auditLog({
      action: "webhook.nowpayments.credits.rejected",
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
  const status = String(payload.payment_status ?? "");
  if (!orderId) {
    return NextResponse.json({ ok: true, note: "no order_id" });
  }

  try {
    if (status === "finished" || status === "confirmed") {
      const { credited } = await markCreditPurchasePaid({
        orderId,
        paymentId: (payload.payment_id as string | undefined) ?? null,
        payCurrency: (payload.pay_currency as string | undefined) ?? null,
        actuallyPaidUsd: String(payload.price_amount ?? ""),
      });
      return NextResponse.json({ ok: true, credited });
    }
    if (status === "failed" || status === "expired") {
      await markCreditPurchaseFailed({ orderId, status });
    }
    // waiting / confirming / partially_paid → leave pending.
    return NextResponse.json({ ok: true, status });
  } catch (err) {
    await auditLog({
      action: "webhook.nowpayments.credits.error",
      entityType: "CreditPurchase",
      entityId: orderId,
      metadata: { error: String(err), status },
    });
    return NextResponse.json({ ok: true, note: "error_logged" });
  }
}
