import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { prisma } from "@/server/db";
import {
  debitStandalone,
  getBalance,
  grantCredit,
  InsufficientCreditError,
} from "@/server/credit";

const created: string[] = [];

async function makeUser(): Promise<string> {
  const u = await prisma.user.create({ data: { email: `credit-${randomUUID()}@test.local` } });
  created.push(u.id);
  return u.id;
}

afterEach(async () => {
  while (created.length) {
    const id = created.pop()!;
    await prisma.user.delete({ where: { id } }).catch(() => {});
  }
});

describe("credit engine", () => {
  it("grants and reads balance via ledger sum", async () => {
    const userId = await makeUser();
    await grantCredit({ userId, amountUsd: "0.10", source: "grant" });
    await grantCredit({ userId, amountUsd: "0.05", source: "purchase" });
    expect(await getBalance(userId)).toBe("0.15");
  });

  it("concurrent debits never overspend (advisory lock)", async () => {
    const userId = await makeUser();
    await grantCredit({ userId, amountUsd: "0.10", source: "grant" });

    // 4 parallel debits of 0.05 against a 0.10 balance → exactly 2 succeed.
    const results = await Promise.allSettled(
      Array.from({ length: 4 }, () =>
        debitStandalone({ userId, amountUsd: "0.05", inferenceRequestId: randomUUID() }),
      ),
    );

    const ok = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.filter(
      (r) => r.status === "rejected" && r.reason instanceof InsufficientCreditError,
    ).length;

    expect(ok).toBe(2);
    expect(failed).toBe(2);

    const balance = await getBalance(userId);
    expect(Number(balance)).toBe(0);
    expect(Number(balance)).toBeGreaterThanOrEqual(0); // never negative
  });
});
