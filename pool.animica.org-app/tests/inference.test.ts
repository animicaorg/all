import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { mintApiKey } from "@/server/apiAuth";
import { getBalance } from "@/server/credit";
import { prisma } from "@/server/db";
import { serveChat, serveEmbed, PaymentRequiredError } from "@/server/ai/serve";

const created: string[] = [];

async function makeAuthed() {
  const user = await prisma.user.create({
    data: { email: `inf-${randomUUID()}@test.local` },
  });
  created.push(user.id);
  const minted = await mintApiKey(user.id); // first key → starter grant
  const apiKey = await prisma.apiKey.findUniqueOrThrow({ where: { id: minted.id } });
  return { user, apiKey };
}

afterEach(async () => {
  while (created.length) {
    const id = created.pop()!;
    await prisma.user.delete({ where: { id } }).catch(() => {});
  }
});

describe("inference end-to-end (mock provider)", () => {
  it("serves a chat completion, meters it, and debits credit", async () => {
    const authed = await makeAuthed();
    const before = await getBalance(authed.user.id);
    expect(Number(before)).toBeGreaterThan(0); // starter grant

    const res = await serveChat(authed, {
      model: "anm-fast-8b",
      messages: [{ role: "user", content: "ping pong" }],
    });

    expect(res.object).toBe("chat.completion");
    expect(res.choices[0].message.content).toContain("ping pong");
    expect(res.usage.total_tokens).toBe(
      res.usage.prompt_tokens + res.usage.completion_tokens,
    );

    const rows = await prisma.inferenceRequest.findMany({
      where: { userId: authed.user.id },
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("success");
    expect(rows[0].provider).toBe("mock");
    expect(Number(rows[0].grossMarginUsd)).toBeGreaterThan(0);

    const debit = await prisma.creditLedger.findFirst({
      where: { userId: authed.user.id, source: "inference" },
    });
    expect(debit).not.toBeNull();
    expect(Number(debit!.amountUsd)).toBeLessThan(0);

    const after = await getBalance(authed.user.id);
    expect(Number(after)).toBeLessThan(Number(before));
  });

  it("serves embeddings", async () => {
    const authed = await makeAuthed();
    const res = await serveEmbed(authed, { model: "anm-embed", input: ["a", "b", "c"] });
    expect(res.object).toBe("list");
    expect(res.data).toHaveLength(3);
    expect(res.data[0].embedding.length).toBe(16);
  });

  it("rejects with PaymentRequired when balance is exhausted", async () => {
    const authed = await makeAuthed();
    // Drain the starter grant.
    await prisma.creditLedger.create({
      data: {
        userId: authed.user.id,
        amountUsd: `-${await getBalance(authed.user.id)}`,
        balanceAfterUsd: "0",
        source: "adjustment",
      },
    });
    expect(Number(await getBalance(authed.user.id))).toBe(0);

    await expect(
      serveChat(authed, {
        model: "anm-fast-8b",
        messages: [{ role: "user", content: "hi" }],
      }),
    ).rejects.toBeInstanceOf(PaymentRequiredError);
  });
});
