import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { verifyWebhookSignatureFor, SUBS_PAYPAL_WEBHOOK_ID, getSubscription } from '@/lib/paypal';
import { assertSubscriptionMoneyCents, computeSubPatch, planRank, safetyCaps, salePaymentFromResource } from '@/lib/planConfig';
import { trackBillingEvent } from '@/lib/plan';
import { sendMailSafe } from '@/lib/hireMail';
import { HIRE_NOTIFY_EMAIL } from '@/lib/hire';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// PayPal webhook for the platform subscription tiers. Same fail-closed posture as the hire
// webhook (503 while SUBS_PAYPAL_WEBHOOK_ID is unset — PayPal retries; bad signature => 400)
// PLUS real event-id replay protection: the BillingEvent insert (PayPal event id as PK) and
// the subscription patch commit in ONE transaction, so a duplicate delivery either loses the
// insert race (P2002 => acked as duplicate) or sees the row already present. A processing
// crash before the transaction returns 500 => PayPal redelivers => reprocessed cleanly.
export async function POST(req: NextRequest) {
  let event: any = null;
  try {
    event = await req.json();
  } catch {
    return NextResponse.json({ error: 'bad json' }, { status: 400 });
  }

  if (!SUBS_PAYPAL_WEBHOOK_ID) {
    return NextResponse.json({ error: 'webhook not configured' }, { status: 503 });
  }
  const sig = await verifyWebhookSignatureFor(SUBS_PAYPAL_WEBHOOK_ID, req.headers, event);
  if (!sig.valid) {
    console.warn('[billing:webhook] rejected delivery:', sig.error || 'signature invalid');
    return NextResponse.json({ error: 'signature verification failed' }, { status: 400 });
  }

  const eventId = String(event?.id || '');
  const type = String(event?.event_type || '');
  const resource = event?.resource ?? {};
  if (!eventId) return NextResponse.json({ error: 'missing event id' }, { status: 400 });

  // Fast replay short-circuit (the transactional insert below is the real guard).
  const seen = await prisma.billingEvent.findUnique({ where: { id: eventId } }).catch(() => null);
  if (seen) return NextResponse.json({ ok: true, duplicate: true });

  try {
    const resolved = await resolveRow(resource);
    if (!resolved) {
      // Not ours / unprovable claim — ack so PayPal stops retrying. (Hire events arrive on
      // the hire webhook registration, never here.)
      return NextResponse.json({ ok: true, ignored: true });
    }
    const { row, subId } = resolved;

    const patch: Record<string, unknown> = {
      lastWebhookAt: new Date(),
      lastWebhookType: type,
    };
    if (!row.paypalSubscriptionId && subId) patch.paypalSubscriptionId = subId;

    const computed = computeSubPatch(type, row.status, new Date(), safetyCaps().graceDays);
    if (computed === null && type !== 'BILLING.SUBSCRIPTION.CANCELLED') {
      // Terminal row or unknown-but-subscribed event: record delivery, change no STATE.
      // A completed sale still gets its BillingPayment row — CANCELED is terminal for
      // entitlements, but a trailing PayPal charge is real money that the revenue audit
      // (and any refund conversation) must be able to see.
      const noopOps: any[] = [
        prisma.billingEvent.create({
          data: { id: eventId, eventType: type, resourceId: String(resource?.id || ''), subscriptionId: subId, accountId: row.accountId, summary: 'noop (terminal/unknown)' },
        }),
        prisma.planSubscription.update({ where: { id: row.id }, data: patch }),
      ];
      if (type === 'PAYMENT.SALE.COMPLETED') {
        const terminalSale = salePaymentFromResource(resource);
        if (terminalSale) {
          noopOps.push(
            prisma.billingPayment.createMany({
              data: [
                {
                  accountId: row.accountId,
                  subscriptionId: row.id,
                  planKey: row.planKey,
                  paypalCaptureId: terminalSale.paypalCaptureId,
                  paypalSubscriptionId: subId || null,
                  amountCents: terminalSale.amountCents,
                  currency: terminalSale.currency,
                  status: 'COMPLETED',
                  kind: 'subscription',
                  payerEmail: row.payerEmail,
                  occurredAt: terminalSale.occurredAt,
                  rawJson: JSON.stringify(resource ?? {}),
                },
              ],
              skipDuplicates: true,
            }),
          );
        }
      }
      try {
        await prisma.$transaction(noopOps);
      } catch (e: any) {
        if (e?.code === 'P2002') return NextResponse.json({ ok: true, duplicate: true });
        throw e;
      }
      if (type === 'PAYMENT.SALE.COMPLETED') {
        // A charge on a terminal row usually means the PayPal-side cancel failed or lagged
        // — the subscriber is being billed for a plan we no longer grant. Page the operator.
        sendMailSafe({
          to: HIRE_NOTIFY_EMAIL,
          subject: `Animica subscriptions: charge on a ${row.status} subscription (${row.accountId})`,
          text: `PayPal completed a sale against a ${row.status} subscription row.\n\nAccount: ${row.accountId}\nPlan: ${row.planKey}\nSubscription: ${subId}\nPayer: ${row.payerEmail || 'unknown'}\n\nVerify the PayPal-side cancel and refund if needed: https://animica.dev/admin/billing`,
        }).catch(() => {});
      }
      return NextResponse.json({ ok: true, noop: true });
    }

    if (computed?.status) patch.status = computed.status;
    if (computed && 'graceUntil' in computed) patch.graceUntil = computed.graceUntil;
    if (computed?.canceledAt) patch.canceledAt = computed.canceledAt;
    if (computed?.failedPayments === 'increment') patch.failedPayments = { increment: 1 };
    if (computed?.failedPayments === 'reset') patch.failedPayments = 0;
    if (computed?.note) patch.lastError = computed.note;
    if (computed?.status === 'ACTIVE') patch.lastError = null;

    // Event-specific enrichment.
    if (type === 'BILLING.SUBSCRIPTION.ACTIVATED' || type === 'BILLING.SUBSCRIPTION.UPDATED') {
      const email = resource?.subscriber?.email_address;
      if (typeof email === 'string' && email) patch.payerEmail = email.slice(0, 200);
      const nbt = resource?.billing_info?.next_billing_time;
      if (nbt) patch.currentPeriodEnd = new Date(nbt);
      // Plan may have changed via a revision confirmed while the browser died: sync it —
      // but only to a plan WE minted, with the money re-asserted.
      const planId = String(resource?.plan_id || '');
      if (planId && planId !== row.paypalPlanId) {
        const planRow = await prisma.plan.findFirst({ where: { paypalPlanId: planId, active: true } });
        if (planRow) {
          const sub = await getSubscription(subId).catch(() => null);
          const money = sub ? assertSubscriptionMoneyCents(sub, planRow.priceUsdCents) : { ok: false as const, reason: 'unfetchable' };
          // Same no-proration rule as /billing/change and /billing/confirm: a revision that
          // RAISES the tier is only adopted once a payment at the new price has landed —
          // PayPal revisions charge nothing until the next billing cycle.
          const raisesTier = planRank(planRow.key) > planRank(row.planKey);
          const paidCents = Math.round(Number(sub?.billing_info?.last_payment?.amount?.value ?? 0) * 100);
          const paidForTier = Number.isFinite(paidCents) && paidCents >= planRow.priceUsdCents;
          if (money.ok && (!raisesTier || paidForTier)) {
            patch.planKey = planRow.key;
            patch.priceUsdCents = planRow.priceUsdCents;
            patch.paypalPlanId = planRow.paypalPlanId;
          } else if (money.ok && raisesTier) {
            patch.lastError = `plan revision to ${planRow.key} not adopted: no payment at the new price yet`;
          }
        }
      }
    }
    // The auditable USD revenue record (BillingPayment) for a verified, completed sale, and
    // an amount anomaly flag for the operator. Both are decided here, written in the
    // transaction below.
    let payment: ReturnType<typeof salePaymentFromResource> = null;
    let amountMismatch: string | null = null;
    if (type === 'PAYMENT.SALE.COMPLETED') {
      patch.lastPaymentAt = new Date();
      const total = Number(resource?.amount?.total);
      if (Number.isFinite(total) && total > 0) patch.lastPaymentAmountCents = Math.round(total * 100);
      // Advance the paid-through date (SALE events don't carry billing_info).
      const sub = await getSubscription(subId).catch(() => null);
      const nbt = sub?.billing_info?.next_billing_time;
      if (nbt) patch.currentPeriodEnd = new Date(nbt);
      const email = sub?.subscriber?.email_address;
      if (typeof email === 'string' && email && !row.payerEmail) patch.payerEmail = email.slice(0, 200);

      payment = salePaymentFromResource(resource);
      // Server-side money check for the REVENUE record: the expected amount is the ROW's
      // contracted price (grandfathered subscribers keep their minted plan's price after a
      // catalog change — the current Plan row's price would false-alarm on every one of
      // them; the row price itself was verified against a Plan WE minted at confirm time).
      // The sale is still recorded verbatim either way — the audit trail must reflect what
      // PayPal actually charged — but an anomaly pages the operator.
      if (payment && (payment.currency !== 'USD' || payment.amountCents !== row.priceUsdCents)) {
        amountMismatch = `sale ${payment.paypalCaptureId}: charged ${payment.amountCents} ${payment.currency}, contracted ${row.priceUsdCents} USD`;
      }
    }
    if (type === 'BILLING.SUBSCRIPTION.PAYMENT.FAILED') {
      patch.lastError = 'subscription payment failed';
    }

    // Atomic: dedup insert + patch (+ the revenue row). A concurrent duplicate delivery loses
    // the insert (P2002). The BillingPayment write uses createMany+skipDuplicates so a SECOND
    // PayPal event referencing the SAME sale (redelivery under a fresh event id) can never
    // double-count revenue AND never aborts the transaction.
    const ops: any[] = [
      prisma.billingEvent.create({
        data: {
          id: eventId,
          eventType: type,
          resourceId: String(resource?.id || ''),
          subscriptionId: subId,
          accountId: row.accountId,
          summary: computed?.status ? `-> ${computed.status}` : 'meta',
        },
      }),
      prisma.planSubscription.update({ where: { id: row.id }, data: patch }),
    ];
    if (payment) {
      ops.push(
        prisma.billingPayment.createMany({
          data: [
            {
              accountId: row.accountId,
              subscriptionId: row.id,
              planKey: row.planKey,
              paypalCaptureId: payment.paypalCaptureId,
              paypalSubscriptionId: subId || null,
              amountCents: payment.amountCents,
              currency: payment.currency,
              status: 'COMPLETED',
              kind: 'subscription',
              payerEmail: row.payerEmail ?? (typeof patch.payerEmail === 'string' ? patch.payerEmail : null),
              occurredAt: payment.occurredAt,
              rawJson: JSON.stringify(resource ?? {}),
            },
          ],
          skipDuplicates: true,
        }),
      );
    }
    // Clawbacks flip the recorded sale's status so revenue reports never count refunded
    // money. PAYMENT.SALE.REFUNDED/REVERSED resources reference the original sale in
    // sale_id; updateMany is a no-op when we never recorded that sale.
    if (type === 'PAYMENT.SALE.REFUNDED' || type === 'PAYMENT.SALE.REVERSED') {
      const saleId = String(resource?.sale_id || '');
      if (saleId) {
        ops.push(
          prisma.billingPayment.updateMany({
            where: { paypalCaptureId: saleId },
            data: { status: 'REFUNDED' },
          }),
        );
      }
    }
    try {
      await prisma.$transaction(ops);
    } catch (e: any) {
      if (e?.code === 'P2002') return NextResponse.json({ ok: true, duplicate: true });
      throw e;
    }

    // Post-commit side effects (fire-and-forget — never fail the delivery).
    if (amountMismatch) {
      sendMailSafe({
        to: HIRE_NOTIFY_EMAIL,
        subject: `Animica subscriptions: SALE AMOUNT MISMATCH on ${row.planKey} (${row.accountId})`,
        text: `A verified PayPal sale does not match the subscription's contracted price.\n\n${amountMismatch}\n\nAccount: ${row.accountId}\nSubscription: ${subId}\nPayer: ${row.payerEmail || 'unknown'}\n\nThe sale was recorded as-is in BillingPayment. Review: https://animica.dev/admin/billing`,
      }).catch(() => {});
    }
    if (computed?.needsAdminEmail) {
      sendMailSafe({
        to: HIRE_NOTIFY_EMAIL,
        subject: `Animica subscriptions: ${type} on ${row.planKey} (${row.accountId})`,
        text: `PayPal reports ${type}.\n\nAccount: ${row.accountId}\nPlan: ${row.planKey}\nSubscription: ${subId}\nPayer: ${row.payerEmail || 'unknown'}\n\nAdmin: https://animica.dev/admin/billing`,
      }).catch(() => {});
    }
    if (type === 'BILLING.SUBSCRIPTION.PAYMENT.FAILED') {
      trackBillingEvent('payment_failed', { accountId: row.accountId, planKey: row.planKey }).catch(() => {});
    }
    if (type === 'PAYMENT.SALE.COMPLETED' && (row.status === 'PAST_DUE' || row.status === 'GRACE_PERIOD' || row.status === 'SUSPENDED')) {
      trackBillingEvent('reactivation', { accountId: row.accountId, planKey: row.planKey }).catch(() => {});
    }
    if (type === 'BILLING.SUBSCRIPTION.CANCELLED' && row.status !== 'CANCELED') {
      trackBillingEvent('cancel', { accountId: row.accountId, planKey: row.planKey, meta: { via: 'webhook' } }).catch(() => {});
    }

    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error('[billing:webhook] processing error:', (e as Error).message);
    // 500 => PayPal retries; safe because the patch commits atomically with the dedup insert.
    return NextResponse.json({ error: 'processing failed' }, { status: 500 });
  }
}

// Strict row resolution (hire pattern): a stored paypalSubscriptionId match is trusted (we
// wrote it after full verification). A custom_id claim is untrusted — the referenced
// subscription is re-fetched and must PROVE it belongs to that row (custom_id == row.id,
// plan is one of ours, money matches) and must not belong to any other row.
async function resolveRow(resource: any) {
  const rawId = typeof resource?.id === 'string' ? resource.id : '';
  // PAYMENT.SALE.* events carry the subscription id in billing_agreement_id, not resource.id.
  const subId: string = rawId.startsWith('I-') ? rawId : String(resource?.billing_agreement_id || '');
  if (subId) {
    const bySub = await prisma.planSubscription.findUnique({ where: { paypalSubscriptionId: subId } });
    if (bySub) return { row: bySub, subId };
  }
  const customId: string = String(resource?.custom_id || resource?.custom || '');
  if (!customId || !subId) return null;
  const byCustom = await prisma.planSubscription.findUnique({ where: { id: customId } });
  if (!byCustom) return null;

  const sub = await getSubscription(subId).catch(() => null);
  if (!sub) return null;
  if ((sub.custom_id || '') !== byCustom.id) return null;
  const planRow = await prisma.plan.findFirst({ where: { paypalPlanId: String(sub.plan_id || ''), active: true } });
  if (!planRow) return null;
  const money = assertSubscriptionMoneyCents(sub, planRow.priceUsdCents);
  if (!money.ok) {
    console.warn(`[billing:webhook] rejecting ${byCustom.id}: ${money.reason}`);
    return null;
  }
  const owner = await prisma.planSubscription.findUnique({ where: { paypalSubscriptionId: subId } });
  if (owner && owner.id !== byCustom.id) return null;
  return { row: byCustom, subId };
}
