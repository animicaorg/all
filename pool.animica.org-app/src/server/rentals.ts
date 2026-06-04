// Rental lifecycle state machine + quote / escrow / settlement math.
//
// Mirrors the proven order-state-machine pattern in buy.animica.org's
// orders.ts (atomic, idempotent transitions + audit log + dedupe events).
//
// Fee model — IMPORTANT, no double-count: the marketplace 5% fee is taken on
// the rental PRICE (USD). The pool's own 5% mining fee is taken on the mined
// rewards, which flow to the RENTER. The two are disjoint; this module only
// ever touches the rental price.

import type { Rental, RentalStatus } from "@prisma/client";
import { prisma } from "./db";
import { env } from "./env";
import { D, mul, sub, pctOf, fmtFixed } from "@/lib/money";

const ALLOWED: Record<RentalStatus, RentalStatus[]> = {
  CREATED: ["QUOTED", "EXPIRED", "FAILED", "CANCELLED", "NEEDS_ADMIN_REVIEW"],
  QUOTED: ["WAITING_PAYMENT", "EXPIRED", "FAILED", "CANCELLED", "NEEDS_ADMIN_REVIEW"],
  WAITING_PAYMENT: ["PAYMENT_DETECTED", "PAYMENT_CONFIRMED", "EXPIRED", "FAILED", "CANCELLED", "NEEDS_ADMIN_REVIEW"],
  PAYMENT_DETECTED: ["PAYMENT_CONFIRMED", "FAILED", "NEEDS_ADMIN_REVIEW"],
  PAYMENT_CONFIRMED: ["ACTIVE", "FAILED", "NEEDS_ADMIN_REVIEW"],
  ACTIVE: ["COMPLETING", "REFUND_DUE", "NEEDS_ADMIN_REVIEW"],
  COMPLETING: ["OWNER_PAID", "REFUND_DUE", "FAILED", "NEEDS_ADMIN_REVIEW"],
  OWNER_PAID: ["COMPLETE", "NEEDS_ADMIN_REVIEW"],
  COMPLETE: [],
  REFUND_DUE: ["REFUND_PROCESSING", "OWNER_PAID", "FAILED", "NEEDS_ADMIN_REVIEW"],
  REFUND_PROCESSING: ["REFUNDED", "COMPLETE", "NEEDS_ADMIN_REVIEW"],
  REFUNDED: ["COMPLETE", "NEEDS_ADMIN_REVIEW"],
  EXPIRED: [],
  FAILED: ["NEEDS_ADMIN_REVIEW"],
  CANCELLED: [],
  NEEDS_ADMIN_REVIEW: [
    "WAITING_PAYMENT", "PAYMENT_CONFIRMED", "ACTIVE", "COMPLETING",
    "OWNER_PAID", "COMPLETE", "REFUND_DUE", "REFUND_PROCESSING",
    "REFUNDED", "FAILED", "CANCELLED",
  ],
};

export function canTransition(from: RentalStatus, to: RentalStatus): boolean {
  return ALLOWED[from]?.includes(to) ?? false;
}

export class TransitionError extends Error {
  constructor(public readonly from: RentalStatus, public readonly to: RentalStatus) {
    super(`Illegal rental transition ${from} → ${to}`);
  }
}

export interface RentalPatch {
  npInvoiceId?: string | null;
  npPaymentId?: string | null;
  npPayoutId?: string | null;
  npRefundPayoutId?: string | null;
  npConversionId?: string | null;
  invoiceUrl?: string | null;
  payAmount?: string | null;
  payCurrency?: string | null;
  actuallyPaidUsd?: string | null;
  windowStartAt?: Date | null;
  windowEndAt?: Date | null;
  measuredUptimeSeconds?: number | null;
  refundUsd?: string | null;
  ownerEarnedUsd?: string | null;
  ownerPayoutCurrency?: string | null;
  ownerPayoutAddress?: string | null;
}

/** Atomically transition a rental. Idempotent no-op if already in `to`.
 *  Throws TransitionError on an illegal transition. Re-reads in the tx so
 *  concurrent webhooks + worker ticks never double-apply. */
export async function transition(opts: {
  rentalId: string;
  to: RentalStatus;
  actor?: string;
  patch?: RentalPatch;
  reasonMetadata?: Record<string, unknown>;
}): Promise<Rental> {
  return prisma.$transaction(async (tx) => {
    const current = await tx.rental.findUniqueOrThrow({ where: { id: opts.rentalId } });
    if (current.status === opts.to) return current;
    if (!canTransition(current.status, opts.to)) {
      throw new TransitionError(current.status, opts.to);
    }
    const updated = await tx.rental.update({
      where: { id: opts.rentalId },
      data: { status: opts.to, ...opts.patch },
    });
    await tx.auditLog.create({
      data: {
        action: "rental.transition",
        entityType: "Rental",
        entityId: opts.rentalId,
        actor: opts.actor ?? "system",
        metadata: { from: current.status, to: opts.to, ...opts.reasonMetadata } as object,
      },
    });
    return updated;
  });
}

/** Record a webhook / poll observation. Returns deduplicated=true if the key
 *  was already seen (P2002 unique violation on dedupeKey). */
export async function recordEvent(opts: {
  rentalId: string;
  source: string;
  dedupeKey: string;
  payload: unknown;
}): Promise<{ deduplicated: boolean }> {
  try {
    await prisma.paymentEvent.create({
      data: {
        rentalId: opts.rentalId,
        source: opts.source,
        dedupeKey: opts.dedupeKey,
        payload: (opts.payload ?? {}) as object,
      },
    });
    return { deduplicated: false };
  } catch (err) {
    if (err && typeof err === "object" && "code" in err && (err as { code: string }).code === "P2002") {
      return { deduplicated: true };
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Quote + settlement math (all Decimal, USD strings)
// ---------------------------------------------------------------------------

export interface RentalQuote {
  hours: number;
  pricePerHourUsd: string;
  grossUsd: string;
  marketplaceFeePercent: string;
  marketplaceFeeUsd: string;
  ownerNetUsd: string;
  quoteExpiresAt: Date;
}

export function quoteRental(input: { pricePerHourUsd: string; hours: number }): RentalQuote {
  const feePct = env().MARKETPLACE_FEE_PERCENT;
  const grossUsd = mul(D(input.pricePerHourUsd), input.hours);
  const marketplaceFeeUsd = pctOf(grossUsd, feePct);
  const ownerNetUsd = sub(grossUsd, marketplaceFeeUsd);
  return {
    hours: input.hours,
    pricePerHourUsd: fmtFixed(input.pricePerHourUsd, 2),
    grossUsd: fmtFixed(grossUsd, 2),
    marketplaceFeePercent: feePct,
    marketplaceFeeUsd: fmtFixed(marketplaceFeeUsd, 2),
    ownerNetUsd: fmtFixed(ownerNetUsd, 2),
    quoteExpiresAt: new Date(Date.now() + env().QUOTE_TTL_SECONDS * 1000),
  };
}

export interface Settlement {
  deliveredFraction: string;     // 0..1
  ownerEarnedUsd: string;        // owner's 95% share, scaled by delivery
  platformFeeKeptUsd: string;    // platform 5%, scaled by delivery
  refundUsd: string;             // returned to renter for undelivered time
}

/** Pro-rata settlement tied to MEASURED pool uptime (not wall-clock), so an
 *  offline rig genuinely reduces the owner's earnings and refunds the renter.
 *  Full completion is just the deliveredFraction = 1 case. */
export function settle(input: {
  grossUsd: string;
  ownerNetUsd: string;
  marketplaceFeeUsd: string;
  measuredUptimeSeconds: number;
  hours: number;
}): Settlement {
  const windowSeconds = D(input.hours).mul(3600);
  let fraction = windowSeconds.lte(0)
    ? D(0)
    : D(input.measuredUptimeSeconds).div(windowSeconds);
  if (fraction.lt(0)) fraction = D(0);
  if (fraction.gt(1)) fraction = D(1);

  const ownerEarned = D(input.ownerNetUsd).mul(fraction);
  const platformKept = D(input.marketplaceFeeUsd).mul(fraction);
  const refund = D(input.grossUsd).sub(ownerEarned).sub(platformKept);
  return {
    deliveredFraction: fraction.toFixed(6),
    ownerEarnedUsd: fmtFixed(ownerEarned, 2),
    platformFeeKeptUsd: fmtFixed(platformKept, 2),
    refundUsd: fmtFixed(refund.lt(0) ? D(0) : refund, 2),
  };
}
