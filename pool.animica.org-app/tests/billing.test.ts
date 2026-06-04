import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { markCreditPurchaseFailed, markCreditPurchasePaid } from "@/server/billing";
import { getBalance } from "@/server/credit";
import { prisma } from "@/server/db";

const created: string[] = [];

async function makePurchase(amountUsd: string) {
  const user = await prisma.user.create({
    data: { email: `buy-${randomUUID()}@test.local` },
  });
  created.push(user.id);
  const purchase = await prisma.creditPurchase.create({
    data: { userId: user.id, amountUsd, status: "pending" },
  });
  return { user, purchase };
}

afterEach(async () => {
  while (created.length) {
    const id = created.pop()!;
    await prisma.user.delete({ where: { id } }).catch(() => {});
  }
});

describe("credit purchase crediting", () => {
  it("grants credit exactly once (idempotent IPN)", async () => {
    const { user, purchase } = await makePurchase("25");

    const first = await markCreditPurchasePaid({ orderId: purchase.id, paymentId: "p1" });
    expect(first.credited).toBe(true);
    expect(await getBalance(user.id)).toBe("25");

    // Duplicate IPN re-delivery → no second grant.
    const second = await markCreditPurchasePaid({ orderId: purchase.id, paymentId: "p1" });
    expect(second.credited).toBe(false);
    expect(await getBalance(user.id)).toBe("25");

    const grants = await prisma.creditLedger.count({
      where: { userId: user.id, source: "purchase" },
    });
    expect(grants).toBe(1);

    const row = await prisma.creditPurchase.findUnique({ where: { id: purchase.id } });
    expect(row?.status).toBe("paid");
    expect(row?.creditedAt).not.toBeNull();
  });

  it("does not fail an already-credited purchase", async () => {
    const { user, purchase } = await makePurchase("10");
    await markCreditPurchasePaid({ orderId: purchase.id });
    await markCreditPurchaseFailed({ orderId: purchase.id, status: "expired" });
    const row = await prisma.creditPurchase.findUnique({ where: { id: purchase.id } });
    expect(row?.status).toBe("paid"); // unchanged
    expect(await getBalance(user.id)).toBe("10");
  });

  it("marks an uncredited purchase failed", async () => {
    const { purchase } = await makePurchase("10");
    await markCreditPurchaseFailed({ orderId: purchase.id, status: "failed" });
    const row = await prisma.creditPurchase.findUnique({ where: { id: purchase.id } });
    expect(row?.status).toBe("failed");
  });
});
