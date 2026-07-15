import { Prisma } from '@prisma/client';
import { prisma } from './db';
import { bps } from './nanm';
import { config } from './config';

// The append-only ledger. All balance mutations funnel through here inside a DB transaction so
// Account.balanceNanm stays == SUM(LedgerEntry.deltaNanm). We NEVER UPDATE/DELETE ledger rows.

type Tx = Prisma.TransactionClient;

async function post(tx: Tx, accountId: string, deltaNanm: bigint, kind: any, ref?: string, memo?: string) {
  const acct = await tx.account.findUnique({ where: { id: accountId }, select: { balanceNanm: true } });
  if (!acct) throw new Error('account not found');
  const balanceAfter = acct.balanceNanm + deltaNanm;
  if (balanceAfter < 0n) throw new LedgerError('INSUFFICIENT_FUNDS', 'insufficient balance');
  await tx.account.update({ where: { id: accountId }, data: { balanceNanm: balanceAfter } });
  await tx.ledgerEntry.create({
    data: { accountId, deltaNanm, kind, ref: ref ?? null, memo: memo ?? null, balanceAfter },
  });
  return balanceAfter;
}

export class LedgerError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

// Credit a verified on-chain deposit (called only after finality + observed balance delta).
export async function creditDeposit(accountId: string, amountNanm: bigint, ref: string, memo: string) {
  return prisma.$transaction((tx) => post(tx, accountId, amountNanm, 'DEPOSIT', ref, memo));
}

// Atomic purchase: debit buyer, credit creator (minus fee), credit treasury fee, optional fork royalty.
// Treasury is represented by the account whose address == config.treasuryAddress (created on demand).
export interface PurchaseSplit {
  buyerId: string;
  creatorId: string;
  amountNanm: bigint;
  listingId: string;
  forkParentCreatorId?: string;
  forkRoyaltyBps?: number;
}

export async function settlePurchaseFromBalance(s: PurchaseSplit) {
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    const fee = bps(s.amountNanm, config.feeBps);
    let royalty = 0n;
    if (s.forkParentCreatorId && s.forkRoyaltyBps) {
      royalty = bps(s.amountNanm, s.forkRoyaltyBps);
    }
    const creatorShare = s.amountNanm - fee - royalty;
    if (creatorShare < 0n) throw new LedgerError('BAD_SPLIT', 'fee+royalty exceed amount');

    // Debit buyer first — this enforces the funds check.
    await post(tx, s.buyerId, -s.amountNanm, 'PURCHASE_DEBIT', s.listingId, 'purchase');
    await post(tx, s.creatorId, creatorShare, 'SALE_CREDIT', s.listingId, 'sale');
    await post(tx, treasury.id, fee, 'FEE', s.listingId, `fee ${config.feeBps}bps`);
    if (royalty > 0n && s.forkParentCreatorId) {
      await post(tx, s.forkParentCreatorId, royalty, 'FORK_ROYALTY', s.listingId, 'fork royalty');
    }
    return { fee, royalty, creatorShare };
  });
}

// Metered usage debit (per-call / per-token). Buyer pays; creator + treasury split (same 80/20).
export async function debitUsage(buyerId: string, creatorId: string, amountNanm: bigint, listingId: string, usageEventId: string) {
  if (amountNanm <= 0n) return { fee: 0n, creatorShare: 0n };
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    const fee = bps(amountNanm, config.feeBps);
    const creatorShare = amountNanm - fee;
    await post(tx, buyerId, -amountNanm, 'USAGE_DEBIT', usageEventId, 'usage');
    await post(tx, creatorId, creatorShare, 'SALE_CREDIT', usageEventId, 'usage sale');
    await post(tx, treasury.id, fee, 'FEE', usageEventId, 'usage fee');
    return { fee, creatorShare };
  });
}

// Settle an accepted agent-to-agent offer: from -> to, minus marketplace fee.
export async function settleAgentOffer(fromId: string, toId: string, amountNanm: bigint, ref: string) {
  if (amountNanm <= 0n) return { fee: 0n, netToPayee: 0n };
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    const fee = bps(amountNanm, config.feeBps);
    const netToPayee = amountNanm - fee;
    await post(tx, fromId, -amountNanm, 'PURCHASE_DEBIT', ref, 'agent offer');
    await post(tx, toId, netToPayee, 'SALE_CREDIT', ref, 'agent offer');
    await post(tx, treasury.id, fee, 'FEE', ref, 'agent offer fee');
    return { fee, netToPayee };
  });
}

// Hold funds for a withdrawal request (debit now; refund via WITHDRAWAL_REFUND if the send fails).
export async function holdWithdrawal(accountId: string, amountNanm: bigint, withdrawalId: string) {
  return prisma.$transaction((tx) => post(tx, accountId, -amountNanm, 'WITHDRAWAL', withdrawalId, 'withdrawal hold'));
}

export async function refundWithdrawal(accountId: string, amountNanm: bigint, withdrawalId: string) {
  return prisma.$transaction((tx) => post(tx, accountId, amountNanm, 'WITHDRAWAL_REFUND', withdrawalId, 'withdrawal refund'));
}

// Pay a .anm registration/renewal fee: debit the registrant, credit the marketplace treasury.
export async function payNameFee(accountId: string, amountNanm: bigint, ref: string, memo: string) {
  if (amountNanm <= 0n) return;
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    await post(tx, accountId, -amountNanm, 'PURCHASE_DEBIT', ref, memo);
    await post(tx, treasury.id, amountNanm, 'FEE', ref, memo);
  });
}

// --- Agent-task escrow ---------------------------------------------------
// Escrow is held by the treasury sub-account between OPEN and RELEASE/REFUND. The hirer's funds
// leave their balance at OPEN (so they can't double-spend), and only move to the worker at RELEASE.

export async function escrowHold(hirerAccountId: string, amountNanm: bigint, taskRef: string) {
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    await post(tx, hirerAccountId, -amountNanm, 'PURCHASE_DEBIT', taskRef, 'escrow hold');
    await post(tx, treasury.id, amountNanm, 'ADJUSTMENT', taskRef, 'escrow in');
  });
}

export async function escrowRelease(workerAccountId: string, amountNanm: bigint, taskRef: string) {
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    const fee = bps(amountNanm, config.feeBps);
    const net = amountNanm - fee;
    // Escrow leaves treasury; worker gets net; fee stays in treasury (net-out the escrow, keep fee).
    await post(tx, treasury.id, -net, 'ADJUSTMENT', taskRef, 'escrow out');
    await post(tx, workerAccountId, net, 'SALE_CREDIT', taskRef, 'task payout');
    // fee remains in treasury implicitly (we only moved `net` out of the held `amount`).
    return { fee, net };
  });
}

export async function escrowRefund(hirerAccountId: string, amountNanm: bigint, taskRef: string) {
  return prisma.$transaction(async (tx) => {
    const treasury = await ensureTreasury(tx);
    await post(tx, treasury.id, -amountNanm, 'ADJUSTMENT', taskRef, 'escrow refund out');
    await post(tx, hirerAccountId, amountNanm, 'REFUND', taskRef, 'escrow refund');
  });
}

async function ensureTreasury(tx: Tx) {
  const addr = config.treasuryAddress || 'anim1marketplace-treasury-unset';
  let acct = await tx.account.findUnique({ where: { address: addr } });
  if (!acct) {
    acct = await tx.account.create({ data: { address: addr, displayName: 'Animica Marketplace Treasury', role: 'ADMIN' } });
  }
  return acct;
}
