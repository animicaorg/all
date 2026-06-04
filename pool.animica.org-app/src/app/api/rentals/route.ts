// POST /app/api/rentals  — create a rental and a NOWPayments escrow invoice.
//
// Funds are escrowed in NOWPayments custody (invoice WITHOUT payout_address),
// then released to the owner (95%) on completion or refunded pro-rata if the
// rig goes offline. The pool redirect is only engaged once payment confirms
// (handled by the settlement worker), so an unpaid rental never diverts a rig.
import { NextResponse } from "next/server";
import { z } from "zod";
import { requireUser } from "@/server/auth";
import { prisma } from "@/server/db";
import { env } from "@/server/env";
import { createInvoice } from "@/server/nowpayments";
import { quoteRental, transition } from "@/server/rentals";
import { auditLog } from "@/lib/audit";
import { isAnim1, isMonero, type Coins } from "@/lib/rig";

export const dynamic = "force-dynamic";

const Body = z.object({
  rigId: z.string(),
  hours: z.number().int().positive().max(24 * 30),
  coins: z.enum(["ANM", "XMR", "BOTH"]),
  anmMode: z.enum(["pps", "solo"]).optional(),
  renterAnmAddress: z.string().optional(),
  renterXmrAddress: z.string().optional(),
  // Optional: where to send a pro-rata refund if the rig underdelivers.
  renterRefundAddress: z.string().optional(),
  renterRefundCurrency: z.string().optional(),
});

const PENDING: ("CREATED" | "QUOTED" | "WAITING_PAYMENT" | "PAYMENT_DETECTED" | "PAYMENT_CONFIRMED" | "ACTIVE" | "COMPLETING")[] = [
  "CREATED", "QUOTED", "WAITING_PAYMENT", "PAYMENT_DETECTED", "PAYMENT_CONFIRMED", "ACTIVE", "COMPLETING",
];

function coinAllowed(requested: Coins, supported: string): boolean {
  if (supported === "BOTH") return true;
  return requested === supported;
}

export async function POST(req: Request) {
  let user;
  try {
    user = await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  let body: z.infer<typeof Body>;
  try {
    body = Body.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid rental request" }, { status: 400 });
  }

  const rig = await prisma.rig.findUnique({ where: { rigId: body.rigId } });
  if (!rig || rig.status !== "ACTIVE") {
    return NextResponse.json({ error: "rig not available" }, { status: 404 });
  }
  if (!coinAllowed(body.coins, rig.supportedCoins)) {
    return NextResponse.json({ error: `this rig only supports ${rig.supportedCoins}` }, { status: 400 });
  }
  const wantsAnm = body.coins === "ANM" || body.coins === "BOTH";
  const wantsXmr = body.coins === "XMR" || body.coins === "BOTH";
  // Every renter supplies an anim1 address — it is their pool credit handle
  // for ANM, and the registry key the rig's XMR proceeds are routed through.
  if (!body.renterAnmAddress || !isAnim1(body.renterAnmAddress)) {
    return NextResponse.json({ error: "renterAnmAddress (anim1…) is required" }, { status: 400 });
  }
  if (wantsAnm && !body.anmMode) {
    return NextResponse.json({ error: "anmMode (pps|solo) required for ANM" }, { status: 400 });
  }
  if (wantsXmr && (!body.renterXmrAddress || !isMonero(body.renterXmrAddress))) {
    return NextResponse.json({ error: "renterXmrAddress (Monero) required for XMR" }, { status: 400 });
  }

  // Double-booking guard: one live rental per rig at a time.
  const busy = await prisma.rental.findFirst({
    where: { rigId: rig.id, status: { in: PENDING } },
  });
  if (busy) {
    return NextResponse.json({ error: "this rig is already booked" }, { status: 409 });
  }

  const quote = quoteRental({ pricePerHourUsd: rig.pricePerHourUsd, hours: body.hours });

  const rental = await prisma.rental.create({
    data: {
      rigId: rig.id,
      renterUserId: user.id,
      hours: body.hours,
      pricePerHourUsd: quote.pricePerHourUsd,
      grossUsd: quote.grossUsd,
      marketplaceFeePercent: quote.marketplaceFeePercent,
      marketplaceFeeUsd: quote.marketplaceFeeUsd,
      ownerNetUsd: quote.ownerNetUsd,
      coins: body.coins,
      anmMode: wantsAnm ? body.anmMode : null,
      renterAnmAddress: body.renterAnmAddress,
      renterXmrAddress: wantsXmr ? body.renterXmrAddress : null,
      ownerPayoutCurrency: rig.payoutCurrency,
      ownerPayoutAddress: rig.payoutAddress,
      quoteExpiresAt: quote.quoteExpiresAt,
      status: "CREATED",
      metadata:
        body.renterRefundAddress && body.renterRefundCurrency
          ? {
              renterRefundAddress: body.renterRefundAddress,
              renterRefundCurrency: body.renterRefundCurrency.toLowerCase(),
            }
          : undefined,
    },
  });
  await transition({ rentalId: rental.id, to: "QUOTED", actor: user.email });

  // Escrow invoice — no payout_address, so funds sit in custody.
  let invoice;
  try {
    invoice = await createInvoice({
      price_amount: Number(quote.grossUsd),
      price_currency: "usd",
      order_id: rental.id,
      order_description: `Rig rental ${rig.workerName} · ${body.hours}h · ${body.coins}`,
      ipn_callback_url: env().NOWPAYMENTS_PUBLIC_CALLBACK_URL,
      success_url: env().NOWPAYMENTS_SUCCESS_URL,
      cancel_url: env().NOWPAYMENTS_CANCEL_URL,
    });
  } catch (err) {
    await transition({
      rentalId: rental.id,
      to: "FAILED",
      reasonMetadata: { reason: "invoice_create_failed", error: String(err) },
    });
    return NextResponse.json({ error: "could not create payment invoice" }, { status: 502 });
  }

  const updated = await transition({
    rentalId: rental.id,
    to: "WAITING_PAYMENT",
    actor: user.email,
    patch: { npInvoiceId: invoice.id, invoiceUrl: invoice.invoice_url },
  });
  await auditLog({
    action: "rental.created",
    entityType: "Rental",
    entityId: rental.id,
    actor: user.email,
    metadata: { rigId: rig.rigId, hours: body.hours, coins: body.coins, grossUsd: quote.grossUsd },
  });
  return NextResponse.json({ rentalId: updated.id, invoiceUrl: updated.invoiceUrl, quote });
}
