// Credit purchases via NOWPayments.
//
//   buy → create CreditPurchase(pending) + a NOWPayments hosted invoice whose
//   order_id IS the purchase id → user pays in any supported coin → NOWPayments
//   IPN fires → we grant credit EXACTLY ONCE (guarded by an atomic
//   `creditedAt: null` update, so duplicate IPNs are no-ops).
//
// Reuses the existing typed NOWPayments client + the credit ledger.

import { auditLog } from "@/lib/audit";
import { D, gte, lte } from "@/lib/money";
import { grantCredit } from "@/server/credit";
import { prisma } from "@/server/db";
import { env } from "@/server/env";
import { createInvoice } from "@/server/nowpayments";

export const MIN_PURCHASE_USD = "5";
export const MAX_PURCHASE_USD = "10000";

export class PurchaseAmountError extends Error {}

/** Dedicated IPN endpoint for credit purchases (separate from rentals). */
function creditsCallbackUrl(): string {
  return `${env().PUBLIC_BASE_URL}/api/webhooks/nowpayments/credits`;
}

export async function createCreditPurchase(opts: {
  userId: string;
  amountUsd: string;
}): Promise<{ id: string; invoiceUrl: string }> {
  const amount = D(opts.amountUsd);
  if (amount.isNaN() || !gte(amount, MIN_PURCHASE_USD) || !lte(amount, MAX_PURCHASE_USD)) {
    throw new PurchaseAmountError(
      `Amount must be between $${MIN_PURCHASE_USD} and $${MAX_PURCHASE_USD}.`,
    );
  }
  const amountUsd = amount.toString();

  const purchase = await prisma.creditPurchase.create({
    data: { userId: opts.userId, amountUsd, status: "pending" },
  });

  const e = env();
  const invoice = await createInvoice({
    price_amount: Number(amountUsd),
    price_currency: "usd",
    order_id: purchase.id,
    order_description: `Animica AI credits — $${amountUsd}`,
    ipn_callback_url: creditsCallbackUrl(),
    success_url: e.NOWPAYMENTS_SUCCESS_URL,
    cancel_url: e.NOWPAYMENTS_CANCEL_URL,
  });

  await prisma.creditPurchase.update({
    where: { id: purchase.id },
    data: { npInvoiceId: invoice.id, invoiceUrl: invoice.invoice_url },
  });

  await auditLog({
    action: "credit.purchase.created",
    entityType: "CreditPurchase",
    entityId: purchase.id,
    metadata: { amountUsd, npInvoiceId: invoice.id },
  });

  return { id: purchase.id, invoiceUrl: invoice.invoice_url };
}

/**
 * Mark a purchase paid and grant credit, exactly once. Returns whether this
 * call performed the grant (false = already credited / not found = idempotent).
 */
export async function markCreditPurchasePaid(opts: {
  orderId: string;
  paymentId?: string | null;
  payCurrency?: string | null;
  actuallyPaidUsd?: string | null;
}): Promise<{ credited: boolean }> {
  const purchase = await prisma.creditPurchase.findUnique({ where: { id: opts.orderId } });
  if (!purchase) return { credited: false };

  // Atomic claim: only the first finished-IPN flips creditedAt from null.
  const claim = await prisma.creditPurchase.updateMany({
    where: { id: opts.orderId, creditedAt: null },
    data: {
      status: "paid",
      creditedAt: new Date(),
      npPaymentId: opts.paymentId ?? undefined,
      payCurrency: opts.payCurrency ?? undefined,
      actuallyPaidUsd: opts.actuallyPaidUsd ?? undefined,
    },
  });
  if (claim.count === 0) return { credited: false }; // already credited

  await grantCredit({
    userId: purchase.userId,
    amountUsd: purchase.amountUsd,
    source: "purchase",
    nowpaymentsPaymentId: opts.paymentId ?? undefined,
    note: `credit purchase ${purchase.id}`,
  });

  await auditLog({
    action: "credit.purchase.paid",
    entityType: "CreditPurchase",
    entityId: purchase.id,
    metadata: { amountUsd: purchase.amountUsd, paymentId: opts.paymentId },
  });

  return { credited: true };
}

export async function markCreditPurchaseFailed(opts: {
  orderId: string;
  status: "failed" | "expired";
}): Promise<void> {
  // Never override an already-credited purchase.
  await prisma.creditPurchase.updateMany({
    where: { id: opts.orderId, creditedAt: null },
    data: { status: opts.status },
  });
}
