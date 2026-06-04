import { Injectable, BadRequestException, ServiceUnavailableException } from "@nestjs/common";
import { Prisma } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { CreditsService } from "../credits/credits.service";
import { auditLog } from "../common/audit";
import { env } from "../config/env";
import { CREDIT_PACKAGES } from "@animica/shared";
import { createInvoice, getPayment, verifyIpnSignature } from "./nowpayments.client";
import { RentalsService } from "../rentals/rentals.service";
import { PayoutsService } from "../payouts/payouts.service";
import { ReferralService } from "../revenue/referral.service";

@Injectable()
export class PaymentsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly credits: CreditsService,
    private readonly rentals: RentalsService,
    private readonly payouts: PayoutsService,
    private readonly referrals: ReferralService,
  ) {}

  packages() {
    return CREDIT_PACKAGES;
  }

  /** Create a credit purchase + NOWPayments invoice. Returns the pay URL. */
  async checkout(userId: string, opts: { packageId?: string; amountUsd?: number }) {
    let amountUsd = opts.amountUsd ?? 0;
    if (opts.packageId) {
      const pkg = CREDIT_PACKAGES.find((p) => p.id === opts.packageId);
      if (!pkg) throw new BadRequestException("Unknown package");
      amountUsd = pkg.amountUsd;
    }
    if (!Number.isFinite(amountUsd) || amountUsd < 1) {
      throw new BadRequestException("amountUsd must be >= 1");
    }
    if (!env().NOWPAYMENTS_API_KEY) {
      throw new ServiceUnavailableException("Payments not configured");
    }

    const purchase = await this.prisma.creditPurchase.create({
      data: {
        userId,
        amountUsd: new Prisma.Decimal(amountUsd),
        creditsUsd: new Prisma.Decimal(amountUsd), // 1:1 USD→credit at launch
        status: "pending",
      },
    });

    let invoice;
    try {
      invoice = await createInvoice({
        priceAmountUsd: amountUsd,
        orderId: purchase.id,
        orderDescription: `Animica Pool AI credits ($${amountUsd})`,
      });
    } catch (e) {
      await this.prisma.creditPurchase.update({ where: { id: purchase.id }, data: { status: "failed" } });
      throw new ServiceUnavailableException(`Could not create invoice: ${e}`);
    }

    await this.prisma.creditPurchase.update({
      where: { id: purchase.id },
      data: { nowpaymentsInvoiceId: invoice.id },
    });
    await auditLog(this.prisma, { action: "credits.checkout", entityType: "CreditPurchase", entityId: purchase.id, actor: userId, metadata: { amountUsd } });
    return { purchaseId: purchase.id, invoiceUrl: invoice.invoice_url, amountUsd };
  }

  async getPaymentStatus(paymentId: string) {
    return getPayment(paymentId);
  }

  /** IPN handler. Verifies signature, dedupes, and credits on confirmation.
   *  Always idempotent: a purchase is credited at most once. */
  async handleWebhook(body: Record<string, unknown>, signature: string | null) {
    if (!verifyIpnSignature(body, signature)) {
      await auditLog(this.prisma, { action: "nowpayments.webhook.rejected", entityType: "Webhook", metadata: { reason: "bad_signature" } });
      return { ok: false, error: "bad_signature" };
    }
    // Mass-payout IPN (no payment_status; carries a batch/payout id + status).
    const payoutBatchId = String(body.batch_withdrawal_id ?? body.id ?? "");
    if (!body.payment_status && (body.batch_withdrawal_status || (payoutBatchId && body.status))) {
      const dedupeKey = `nppayout:${payoutBatchId}:${String(body.batch_withdrawal_status ?? body.status ?? "")}`;
      try {
        await this.prisma.nowPaymentsEvent.create({ data: { dedupeKey, kind: "payout", refId: payoutBatchId, payload: body as object } });
      } catch (e) {
        if ((e as { code?: string }).code === "P2002") return { ok: true, note: "duplicate" };
        throw e;
      }
      const matched = await this.payouts.handlePayoutIpn(body);
      if (!matched) await this.forwardIpn(body, signature);
      return { ok: true, kind: "payout", matched };
    }

    const orderId = String(body.order_id ?? "");
    const status = String(body.payment_status ?? "");
    const paymentId = String(body.payment_id ?? "");

    // Dedupe this exact event.
    const dedupeKey = `np:${paymentId}:${status}`;
    try {
      await this.prisma.nowPaymentsEvent.create({ data: { dedupeKey, kind: "payment", refId: orderId, payload: body as object } });
    } catch (e) {
      if ((e as { code?: string }).code === "P2002") return { ok: true, note: "duplicate" };
      throw e;
    }

    const purchase = orderId ? await this.prisma.creditPurchase.findUnique({ where: { id: orderId } }) : null;
    if (!purchase) {
      // Not a credit purchase — maybe a crypto compute-rental order.
      const rental = orderId ? await this.prisma.rentalOrder.findUnique({ where: { id: orderId } }) : null;
      if (rental) {
        if (status === "confirmed" || status === "finished") {
          await this.prisma.rentalOrder.update({ where: { id: rental.id }, data: { nowpaymentsPaymentId: paymentId } });
          await this.rentals.activatePaidOrder(rental.id);
        } else if (status === "failed" || status === "expired") {
          await this.prisma.rentalOrder.update({ where: { id: rental.id }, data: { status: status === "expired" ? "cancelled" : "failed" } });
        }
        return { ok: true, kind: "rental" };
      }
      // Not ours — fan out to other apps' webhooks (e.g. buy.animica.org) so a
      // single NOWPayments IPN URL can serve multiple apps on this account.
      await this.forwardIpn(body, signature);
      return { ok: true, note: "forwarded (no local match)" };
    }

    if (status === "confirming") {
      await this.prisma.creditPurchase.update({ where: { id: purchase.id }, data: { status: "confirming", nowpaymentsPaymentId: paymentId } });
    } else if (status === "confirmed" || status === "finished") {
      if (purchase.status !== "completed") {
        await this.prisma.creditPurchase.update({ where: { id: purchase.id }, data: { status: "completed", nowpaymentsPaymentId: paymentId } });
        await this.credits.record(purchase.userId, "purchase", Number(purchase.creditsUsd), { refId: purchase.id, note: "NOWPayments credit purchase" });
        await this.referrals.onPurchase(purchase.userId, Number(purchase.amountUsd)).catch(() => {});
        await auditLog(this.prisma, { action: "credits.purchase.completed", entityType: "CreditPurchase", entityId: purchase.id, actor: purchase.userId, metadata: { creditsUsd: Number(purchase.creditsUsd) } });
      }
    } else if (status === "failed" || status === "expired" || status === "refunded") {
      await this.prisma.creditPurchase.update({ where: { id: purchase.id }, data: { status } });
    }
    return { ok: true };
  }

  /** Fan an unmatched IPN out to other apps' webhooks (same NOWPayments
   *  account → same secret), re-sending the original signature so they verify
   *  it. Fire-and-forget; failures are logged, never thrown. */
  private async forwardIpn(body: Record<string, unknown>, signature: string | null) {
    const urls = env().NOWPAYMENTS_FORWARD_IPN_URLS.split(",").map((u) => u.trim()).filter(Boolean);
    if (urls.length === 0 || !signature) return;
    await Promise.all(
      urls.map((url) =>
        fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json", "x-nowpayments-sig": signature },
          body: JSON.stringify(body),
        }).catch((e) =>
          auditLog(this.prisma, { action: "nowpayments.ipn.forward_failed", entityType: "Webhook", metadata: { url, error: String(e) } }),
        ),
      ),
    );
  }
}
