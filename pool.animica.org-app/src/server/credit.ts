// Credit engine — append-only ledger with atomic, overspend-proof debits.
//
// Balance is the SUM of all signed `amountUsd` rows for a user (a snapshot is
// also written to `balanceAfterUsd` for display/audit). Every mutation runs
// inside a transaction holding a per-user advisory lock
// (`pg_advisory_xact_lock(hashtext(userId))`), so two concurrent inference
// requests can never both pass the balance check and overspend.
//
// Money is a Decimal string throughout (src/lib/money.ts).

import { Prisma, type CreditSource } from "@prisma/client";
import { add, gte, sub } from "@/lib/money";
import { prisma } from "@/server/db";

export class InsufficientCreditError extends Error {
  constructor(
    public readonly balanceUsd: string,
    public readonly requiredUsd: string,
  ) {
    super(`insufficient credit: balance ${balanceUsd} < required ${requiredUsd}`);
    this.name = "InsufficientCreditError";
  }
}

type Tx = Prisma.TransactionClient;

/** Serialize all credit mutations for one user. xact-scoped; auto-released. */
async function lockUser(tx: Tx, userId: string): Promise<void> {
  await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${userId}))`;
}

/** Authoritative balance = SUM of signed ledger amounts (Decimal-safe). */
async function sumBalance(client: Tx, userId: string): Promise<string> {
  const rows = await client.$queryRaw<{ balance: string }[]>`
    SELECT COALESCE(SUM("amountUsd"::numeric), 0)::text AS balance
    FROM "CreditLedger" WHERE "userId" = ${userId}`;
  return rows[0]?.balance ?? "0";
}

export async function getBalance(userId: string): Promise<string> {
  return sumBalance(prisma as unknown as Tx, userId);
}

/** Add credit (purchase / grant / refund / admin adjustment). Returns new balance. */
export async function grantCredit(opts: {
  userId: string;
  amountUsd: string;
  source: CreditSource;
  note?: string;
  nowpaymentsPaymentId?: string;
}): Promise<string> {
  return prisma.$transaction(async (tx) => {
    await lockUser(tx, opts.userId);
    const balance = await sumBalance(tx, opts.userId);
    const after = add(balance, opts.amountUsd);
    await tx.creditLedger.create({
      data: {
        userId: opts.userId,
        amountUsd: opts.amountUsd,
        balanceAfterUsd: after,
        source: opts.source,
        note: opts.note,
        nowpaymentsPaymentId: opts.nowpaymentsPaymentId,
      },
    });
    return after;
  });
}

/**
 * Debit `amountUsd` (a positive cost) for an inference call. MUST run inside
 * the caller's transaction so the InferenceRequest row and the debit commit
 * atomically. Throws InsufficientCreditError (before writing) if the balance
 * can't cover it. Returns the new balance.
 */
export async function debitForInference(
  tx: Tx,
  opts: { userId: string; amountUsd: string; inferenceRequestId: string },
): Promise<string> {
  await lockUser(tx, opts.userId);
  const balance = await sumBalance(tx, opts.userId);
  if (!gte(balance, opts.amountUsd)) {
    throw new InsufficientCreditError(balance, opts.amountUsd);
  }
  const after = sub(balance, opts.amountUsd);
  await tx.creditLedger.create({
    data: {
      userId: opts.userId,
      amountUsd: sub("0", opts.amountUsd), // negative
      balanceAfterUsd: after,
      source: "inference",
      inferenceRequestId: opts.inferenceRequestId,
    },
  });
  return after;
}

/** Convenience wrapper for callers without an existing transaction (tests). */
export function debitStandalone(opts: {
  userId: string;
  amountUsd: string;
  inferenceRequestId: string;
}): Promise<string> {
  return prisma.$transaction((tx) => debitForInference(tx, opts), { timeout: 15000 });
}
