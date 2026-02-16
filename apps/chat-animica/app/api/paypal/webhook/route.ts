import { NextRequest, NextResponse } from "next/server";
import { parseSubscriptionWebhook, verifyWebhookSignature } from "@/src/server/paypal/client";
import { prisma } from "@/src/server/db/prisma";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const headers = Object.fromEntries(req.headers.entries());
  const verified = await verifyWebhookSignature(headers, body);
  if (!verified) return NextResponse.json({ error: "Invalid PayPal signature" }, { status: 400 });

  const parsed = parseSubscriptionWebhook(body);
  if (!parsed) return NextResponse.json({ ok: true, ignored: true });

  await prisma.subscription.upsert({
    where: { subscriptionId: parsed.subscriptionId },
    create: {
      userId: "unknown",
      subscriptionId: parsed.subscriptionId,
      status: parsed.status,
      nextBillingTime: parsed.nextBillingTime ? new Date(parsed.nextBillingTime) : null,
      payerEmail: parsed.payerEmail,
      payerId: parsed.payerId
    },
    update: {
      status: parsed.status,
      nextBillingTime: parsed.nextBillingTime ? new Date(parsed.nextBillingTime) : null,
      payerEmail: parsed.payerEmail,
      payerId: parsed.payerId
    }
  });

  return NextResponse.json({ ok: true });
}
