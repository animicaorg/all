// Animica Python Cloud — execution settlement (§8, §9, §44, §45, §75).
//
// This is where a measured execution becomes money. Three hard rules:
//
//   1. EXACTLY ONCE. CloudExecution.billed is the idempotency anchor: settlement claims the
//      row with a conditional updateMany inside the same transaction that posts the ledger
//      entries. A retry, a double-callback or two racing workers cannot double-charge.
//   2. ALL OR NOTHING. Every ledger post plus the execution's financial fields commit in one
//      prisma.$transaction. There is no window where money moved but the record disagrees.
//   3. EXACT SUM. priceNanm == platformFeeNanm + developerNanm + providerNanm, always, and the
//      posted ledger deltas sum to zero. assertBalanced() proves it before the transaction is
//      allowed to commit, so a pricing bug fails the execution instead of quietly minting ANM.
//
// Funding sources, drawn in this order:
//   a. promotional CloudCredit  (platform-funded; recorded as cogsPromoNanm, never withdrawable)
//   b. the caller's ledger balance (backed 1:1 by finality-verified on-chain deposits)

import { Prisma } from '@prisma/client';
import { prisma } from '../db';
import { postInTx, treasuryAccount, LedgerError } from '../ledger';
import { marginOf, type Policy } from './pricing';

type Tx = Prisma.TransactionClient;

export class SettlementError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export interface SettleInput {
  executionId: string;
  callerAccountId: string | null;
  developerAccountId: string;
  providerAccountId?: string | null;
  priceNanm: bigint;
  platformFeeNanm: bigint;
  developerNanm: bigint;
  providerNanm: bigint;
  cogs: { totalNanm: bigint; computeNanm: bigint; aiNanm: bigint; infraNanm: bigint };
  feeBps: number;
  policy: Policy;
  /** Free-tier executions consume an allowance, not ANM: nothing is posted, but the row is closed. */
  freeTier?: boolean;
}

export interface SettleResult {
  settled: boolean;
  alreadySettled: boolean;
  chargedNanm: bigint;
  creditNanm: bigint;
  platformFeeNanm: bigint;
  developerNanm: bigint;
  providerNanm: bigint;
  contributionNanm: bigint;
  marginBps: number;
}

/** Fail closed if the split does not reconcile — a pricing bug must never reach the ledger. */
function assertBalanced(i: SettleInput) {
  const sum = i.platformFeeNanm + i.developerNanm + i.providerNanm;
  if (sum !== i.priceNanm) {
    throw new SettlementError(
      'UNBALANCED_SPLIT',
      `split ${sum} != price ${i.priceNanm} (platform ${i.platformFeeNanm} + developer ${i.developerNanm} + provider ${i.providerNanm})`,
    );
  }
  if (i.priceNanm < 0n || i.platformFeeNanm < 0n || i.developerNanm < 0n || i.providerNanm < 0n) {
    throw new SettlementError('NEGATIVE_AMOUNT', 'settlement amounts must be non-negative');
  }
  if (i.providerNanm > 0n && !i.providerAccountId) {
    throw new SettlementError('NO_PROVIDER_ACCOUNT', 'provider share allocated with no provider account');
  }
}

/**
 * Draw down promotional credits for an account, oldest-expiring first.
 * Returns how much of `want` the credits could cover. Caller-owned transaction.
 */
async function drawCredits(tx: Tx, accountId: string, want: bigint, now: Date): Promise<bigint> {
  if (want <= 0n) return 0n;
  const grants = await tx.cloudCredit.findMany({
    where: {
      accountId,
      revokedAt: null,
      OR: [{ expiresAt: null }, { expiresAt: { gt: now } }],
    },
    orderBy: [{ expiresAt: 'asc' }, { createdAt: 'asc' }],
  });
  let remaining = want;
  for (const g of grants) {
    if (remaining <= 0n) break;
    const available = g.grantedNanm - g.usedNanm;
    if (available <= 0n) continue;
    const take = available < remaining ? available : remaining;
    // Conditional update: another concurrent execution may have drawn the same grant.
    const claimed = await tx.cloudCredit.updateMany({
      where: { id: g.id, usedNanm: g.usedNanm },
      data: { usedNanm: g.usedNanm + take },
    });
    if (claimed.count === 1) remaining -= take;
  }
  return want - remaining;
}

/**
 * Settle one execution.
 *
 * Ledger postings (net zero across all accounts):
 *   caller     -(price - credit)      USAGE_DEBIT
 *   treasury   -credit                ADJUSTMENT     (platform funds the promotional portion)
 *   developer  +developerNanm         SALE_CREDIT
 *   provider   +providerNanm          SALE_CREDIT
 *   treasury   +platformFeeNanm       FEE
 */
export async function settleExecution(input: SettleInput, now: Date = new Date()): Promise<SettleResult> {
  assertBalanced(input);

  const margin = marginOf(input.platformFeeNanm, input.cogs.totalNanm, input.policy.targetMarginBps);

  return prisma.$transaction(async (tx) => {
    // Exactly-once claim. If another worker already settled this execution, stop here.
    const claim = await tx.cloudExecution.updateMany({
      where: { id: input.executionId, billed: false },
      data: { billed: true },
    });
    if (claim.count !== 1) {
      const row = await tx.cloudExecution.findUnique({ where: { id: input.executionId } });
      return {
        settled: false,
        alreadySettled: true,
        chargedNanm: row?.priceNanm ?? 0n,
        creditNanm: row?.creditNanm ?? 0n,
        platformFeeNanm: row?.platformFeeNanm ?? 0n,
        developerNanm: row?.developerNanm ?? 0n,
        providerNanm: row?.providerNanm ?? 0n,
        contributionNanm: row?.contributionNanm ?? 0n,
        marginBps: 0,
      };
    }

    const ref = input.executionId;
    let creditNanm = 0n;
    let chargedNanm = 0n;

    if (!input.freeTier && input.priceNanm > 0n) {
      if (!input.callerAccountId) {
        throw new SettlementError('NO_PAYER', 'a priced execution needs a caller account');
      }
      creditNanm = await drawCredits(tx, input.callerAccountId, input.priceNanm, now);
      chargedNanm = input.priceNanm - creditNanm;

      const treasury = await treasuryAccount(tx);

      if (chargedNanm > 0n) {
        // Throws LedgerError INSUFFICIENT_FUNDS if the caller cannot pay — the whole
        // transaction rolls back, including the `billed` claim, so a funded retry works.
        await postInTx(tx, input.callerAccountId, -chargedNanm, 'USAGE_DEBIT', ref, 'python cloud execution');
      }
      if (creditNanm > 0n) {
        // The platform funds the credited portion out of the treasury. If the treasury cannot
        // cover it, this fails closed rather than minting ANM out of nothing.
        await postInTx(tx, treasury.id, -creditNanm, 'ADJUSTMENT', ref, 'execution credit (promotional)');
      }
      if (input.developerNanm > 0n) {
        await postInTx(tx, input.developerAccountId, input.developerNanm, 'SALE_CREDIT', ref, 'execution revenue');
      }
      if (input.providerNanm > 0n && input.providerAccountId) {
        await postInTx(tx, input.providerAccountId, input.providerNanm, 'SALE_CREDIT', ref, 'compute provider payout');
      }
      if (input.platformFeeNanm > 0n) {
        await postInTx(tx, treasury.id, input.platformFeeNanm, 'FEE', ref, `platform fee ${input.feeBps}bps`);
      }
    }

    // Promotional spend is a real platform cost: fold it into COGS so margin stays honest.
    const cogsTotal = input.cogs.totalNanm + creditNanm;
    const contributionNanm = input.platformFeeNanm - cogsTotal;

    await tx.cloudExecution.update({
      where: { id: input.executionId },
      data: {
        priceNanm: input.priceNanm,
        platformFeeNanm: input.platformFeeNanm,
        developerNanm: input.developerNanm,
        providerNanm: input.providerNanm,
        feeBps: input.feeBps,
        creditNanm,
        cogsNanm: cogsTotal,
        cogsAiNanm: input.cogs.aiNanm,
        cogsComputeNanm: input.cogs.computeNanm,
        cogsInfraNanm: input.cogs.infraNanm,
        cogsPromoNanm: creditNanm,
        contributionNanm,
        freeTier: Boolean(input.freeTier),
        pricingPolicyId: input.policy.id,
      },
    });

    // Caches, refreshed transactionally. Authoritative values remain the CloudExecution rows.
    await tx.cloudFunction
      .update({
        where: { id: (await tx.cloudExecution.findUnique({ where: { id: input.executionId } }))!.functionId },
        data: { execCount: { increment: 1 }, revenueNanm: { increment: input.developerNanm } },
      })
      .catch(() => {});

    return {
      settled: true,
      alreadySettled: false,
      chargedNanm,
      creditNanm,
      platformFeeNanm: input.platformFeeNanm,
      developerNanm: input.developerNanm,
      providerNanm: input.providerNanm,
      contributionNanm,
      marginBps: margin.marginBps,
    };
  });
}

/**
 * Reserve funds before running (§9: estimate -> verify -> reserve -> execute -> settle -> refund).
 *
 * Rather than move money twice, the reservation is a non-destructive AFFORDABILITY CHECK
 * against the caller's live balance plus available credits, combined with a bound on
 * in-flight spend for that account. That keeps the ledger append-only and free of
 * reversal noise, while still refusing work the caller demonstrably cannot pay for.
 */
export async function checkAffordable(
  callerAccountId: string,
  maxNanm: bigint,
  now: Date = new Date(),
): Promise<{ ok: boolean; balanceNanm: bigint; creditNanm: bigint; inFlightNanm: bigint; availableNanm: bigint }> {
  const [account, credits, inFlight] = await Promise.all([
    prisma.account.findUnique({ where: { id: callerAccountId }, select: { balanceNanm: true } }),
    prisma.cloudCredit.findMany({
      where: { accountId: callerAccountId, revokedAt: null, OR: [{ expiresAt: null }, { expiresAt: { gt: now } }] },
      select: { grantedNanm: true, usedNanm: true },
    }),
    prisma.cloudExecution.aggregate({
      where: { callerAccountId, billed: false, status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
      _sum: { quotedNanm: true },
    }),
  ]);
  const balanceNanm = account?.balanceNanm ?? 0n;
  const creditNanm = credits.reduce((a, c) => a + (c.grantedNanm - c.usedNanm), 0n);
  const inFlightNanm = inFlight._sum.quotedNanm ?? 0n;
  const availableNanm = balanceNanm + creditNanm - inFlightNanm;
  return { ok: availableNanm >= maxNanm, balanceNanm, creditNanm, inFlightNanm, availableNanm };
}

/**
 * Settle an app purchase (one-time or subscription period) with the same exactly-once and
 * exact-sum discipline. feeBps is snapshotted onto the row (§88).
 */
export async function settleAppPurchase(opts: {
  appId: string;
  buyerAccountId: string;
  developerAccountId: string;
  amountNanm: bigint;
  feeBps: number;
  kind: 'one_time' | 'subscription';
  expiresAt?: Date | null;
}) {
  if (opts.amountNanm < 0n) throw new SettlementError('NEGATIVE_AMOUNT', 'amount must be >= 0');
  if (opts.buyerAccountId === opts.developerAccountId) {
    throw new SettlementError('SELF_PURCHASE', 'you already own this app');
  }
  const platformFeeNanm = (opts.amountNanm * BigInt(opts.feeBps)) / 10_000n;
  const developerNanm = opts.amountNanm - platformFeeNanm;

  return prisma.$transaction(async (tx) => {
    const existing = await tx.cloudAppPurchase.findUnique({
      where: { appId_accountId_kind: { appId: opts.appId, accountId: opts.buyerAccountId, kind: opts.kind } },
    });
    if (existing && existing.status === 'ACTIVE' && (!existing.expiresAt || existing.expiresAt > new Date())) {
      return { purchase: existing, alreadyOwned: true, platformFeeNanm: 0n, developerNanm: 0n };
    }

    if (opts.amountNanm > 0n) {
      const treasury = await treasuryAccount(tx);
      await postInTx(tx, opts.buyerAccountId, -opts.amountNanm, 'PURCHASE_DEBIT', opts.appId, 'app purchase');
      if (developerNanm > 0n) {
        await postInTx(tx, opts.developerAccountId, developerNanm, 'SALE_CREDIT', opts.appId, 'app sale');
      }
      if (platformFeeNanm > 0n) {
        await postInTx(tx, treasury.id, platformFeeNanm, 'FEE', opts.appId, `marketplace fee ${opts.feeBps}bps`);
      }
    }

    const purchase = await tx.cloudAppPurchase.upsert({
      where: { appId_accountId_kind: { appId: opts.appId, accountId: opts.buyerAccountId, kind: opts.kind } },
      create: {
        appId: opts.appId,
        accountId: opts.buyerAccountId,
        kind: opts.kind,
        amountNanm: opts.amountNanm,
        platformFeeNanm,
        developerNanm,
        feeBps: opts.feeBps,
        status: 'ACTIVE',
        expiresAt: opts.expiresAt ?? null,
      },
      update: {
        amountNanm: opts.amountNanm,
        platformFeeNanm,
        developerNanm,
        feeBps: opts.feeBps,
        status: 'ACTIVE',
        expiresAt: opts.expiresAt ?? null,
      },
    });
    await tx.cloudApp
      .update({
        where: { id: opts.appId },
        data: { installCount: { increment: 1 }, revenueNanm: { increment: developerNanm } },
      })
      .catch(() => {});

    return { purchase, alreadyOwned: false, platformFeeNanm, developerNanm };
  });
}

export { LedgerError };
