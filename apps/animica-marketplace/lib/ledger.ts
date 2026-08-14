import { Prisma } from '@prisma/client';
import { prisma } from './db';
import { bps } from './nanm';
import { config } from './config';

// The append-only ledger. All balance mutations funnel through here inside a DB transaction so
// Account.balanceNanm stays == SUM(LedgerEntry.deltaNanm). We NEVER UPDATE/DELETE ledger rows.

type Tx = Prisma.TransactionClient;

async function post(tx: Tx, accountId: string, deltaNanm: bigint, kind: any, ref?: string, memo?: string) {
  // ATOMIC guarded update — do NOT reintroduce a read-then-absolute-write here.
  //
  // The previous shape (read balanceNanm, compute balanceAfter in JS, write it back) is a
  // lost-update race under Postgres READ COMMITTED, which is what Prisma uses by default: two
  // concurrent debits of the same account both read the same balance and both write it, so the
  // account is debited once while TWO ledger rows are appended. That silently breaks the
  // Account.balanceNanm == SUM(LedgerEntry.deltaNanm) invariant and mints real, withdrawable ANM.
  // It went unnoticed while the only writers were low-concurrency purchase flows; Animica Python
  // Cloud settles on every execution, so it is reachable from the public invoke endpoint.
  //
  // Instead: a single UPDATE whose WHERE clause performs the funds check and whose SET is a
  // relative increment. Postgres takes a row lock for the duration of the transaction, so racing
  // posts serialize on it, and `count !== 1` means the guard rejected the debit.
  const guard =
    deltaNanm < 0n
      ? { id: accountId, balanceNanm: { gte: -deltaNanm } } // debit: must not go negative
      : { id: accountId };
  const res = await tx.account.updateMany({ where: guard, data: { balanceNanm: { increment: deltaNanm } } });
  if (res.count !== 1) {
    const exists = await tx.account.findUnique({ where: { id: accountId }, select: { id: true } });
    if (!exists) throw new Error('account not found');
    throw new LedgerError('INSUFFICIENT_FUNDS', 'insufficient balance');
  }
  // Read back inside the same transaction: our own UPDATE holds the row lock, so this is the
  // true post-state and no concurrent writer can interleave before we append the entry.
  const acct = await tx.account.findUnique({ where: { id: accountId }, select: { balanceNanm: true } });
  const balanceAfter = acct!.balanceNanm;
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

// The ONLY sanctioned way for another module to move balance. Exported (rather than copied)
// so Animica Python Cloud settlement still funnels through the same funds check, the same
// balanceAfter snapshot and the same append-only discipline that keeps
// Account.balanceNanm == SUM(LedgerEntry.deltaNanm) true. Callers MUST already be inside a
// prisma.$transaction — settlement is all-or-nothing by construction.
export async function postInTx(
  tx: Tx,
  accountId: string,
  deltaNanm: bigint,
  kind: LedgerKindName,
  ref?: string,
  memo?: string,
) {
  return post(tx, accountId, deltaNanm, kind, ref, memo);
}

export type LedgerKindName =
  | 'DEPOSIT'
  | 'PURCHASE_DEBIT'
  | 'SALE_CREDIT'
  | 'FEE'
  | 'FORK_ROYALTY'
  | 'USAGE_DEBIT'
  | 'WITHDRAWAL'
  | 'WITHDRAWAL_REFUND'
  | 'REFUND'
  | 'ADJUSTMENT';

// Treasury/foundation resolution, exported for the Python Cloud settlement path so platform
// fees land in exactly the same ledger account the rest of the platform uses.
export async function treasuryAccount(tx: Tx) {
  return ensureTreasury(tx);
}

// Credit a verified on-chain deposit (called only after finality + observed balance delta).
export async function creditDeposit(accountId: string, amountNanm: bigint, ref: string, memo: string) {
  return prisma.$transaction((tx) => post(tx, accountId, amountNanm, 'DEPOSIT', ref, memo));
}

// tx-aware variant: lets the deposit scanner book the ledger credit AND its DepositAddress
// creditedNanm high-water bump (+ the Deposit row) inside ONE caller-owned transaction, so a
// crash mid-credit can never leave a credited ledger entry without its bookkeeping (which would
// re-credit on the next scan). See scanDepositAddress in lib/deposit.ts.
export async function creditDepositInTx(tx: Tx, accountId: string, amountNanm: bigint, ref: string, memo: string) {
  return post(tx, accountId, amountNanm, 'DEPOSIT', ref, memo);
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
  feeBps?: number; // override (store APP/DIGITAL_GOOD sales pass STORE_FEE_BPS=3000); default MKT_FEE_BPS
}

// The split body, callable inside a caller-owned transaction so purchase-row updates and
// license issuance can commit atomically with the money movement (renewal worker, watcher).
export async function settlePurchaseInTx(tx: Tx, s: PurchaseSplit) {
  const treasury = await ensureTreasury(tx);
  const feeBps = s.feeBps ?? config.feeBps;
  if (!Number.isFinite(feeBps) || feeBps < 0 || feeBps > 10_000) throw new LedgerError('BAD_SPLIT', `invalid feeBps ${feeBps}`);
  const fee = bps(s.amountNanm, feeBps);
  let royalty = 0n;
  if (s.forkParentCreatorId && s.forkRoyaltyBps) {
    royalty = bps(s.amountNanm, s.forkRoyaltyBps);
  }
  const creatorShare = s.amountNanm - fee - royalty;
  if (creatorShare < 0n) throw new LedgerError('BAD_SPLIT', 'fee+royalty exceed amount');

  // Debit buyer first — this enforces the funds check.
  await post(tx, s.buyerId, -s.amountNanm, 'PURCHASE_DEBIT', s.listingId, 'purchase');
  await post(tx, s.creatorId, creatorShare, 'SALE_CREDIT', s.listingId, 'sale');
  await post(tx, treasury.id, fee, 'FEE', s.listingId, `fee ${feeBps}bps`);
  if (royalty > 0n && s.forkParentCreatorId) {
    await post(tx, s.forkParentCreatorId, royalty, 'FORK_ROYALTY', s.listingId, 'fork royalty');
  }
  return { fee, royalty, creatorShare };
}

export async function settlePurchaseFromBalance(s: PurchaseSplit) {
  return prisma.$transaction((tx) => settlePurchaseInTx(tx, s));
}

// Settle a VERIFIED on-chain wallet purchase (ANMSTORE1). The caller — the store payment
// watcher — has already proven the buyer's transfer to the DEDICATED store treasury address
// executed with finality (12 confs + memo byte-match + treasury balance-delta coverage).
// This books it: credit the buyer's ledger with the observed on-chain deposit (ref = txid),
// then run the standard split. Net ledger effect: buyer 0, creator +share, treasury +fee —
// every credited nANM is backed 1:1 by real ANM sitting in the store treasury wallet.
// MUST run inside the caller's transaction together with the Purchase→ACTIVE update, the
// StorePaymentIntent.verifiedAt claim, and License issuance (all-or-nothing).
export async function settleStoreWalletPurchase(tx: Tx, s: PurchaseSplit & { txid: string }) {
  await post(tx, s.buyerId, s.amountNanm, 'DEPOSIT', s.txid, 'store wallet purchase (on-chain, verified)');
  return settlePurchaseInTx(tx, s);
}

// Admin refund of a SETTLED store purchase: reverse the split (creator/treasury/royalty give
// back, buyer gets the full amount as ledger balance — withdrawable on-chain), mark the
// purchase REFUNDED and revoke its license, all in ONE transaction so money and entitlement
// state can never diverge. The status claim makes it exactly-once under concurrent calls.
// If the creator has already withdrawn their share, post() fails INSUFFICIENT_FUNDS and the
// whole refund aborts (fail-closed — admin resolves the shortfall out of band).
// Works for both sources: 'balance' (buyer was debited here) and 'wallet' (buyer paid the
// store treasury on-chain; the credited split is reversed and the buyer's refund is backed
// by those on-chain treasury funds).
export async function refundStorePurchase(opts: {
  purchaseId: string;
  buyerId: string;
  creatorId: string;
  amountNanm: bigint;
  feeBps: number; // MUST be the bps the sale settled with (store = STORE_FEE_BPS)
  forkParentCreatorId?: string;
  forkRoyaltyBps?: number;
  memo?: string;
}) {
  return prisma.$transaction(async (tx) => {
    const claimed = await tx.purchase.updateMany({
      where: { id: opts.purchaseId, status: 'ACTIVE' },
      data: { status: 'REFUNDED', autoRenew: false },
    });
    if (claimed.count !== 1) throw new LedgerError('NOT_REFUNDABLE', 'purchase is not ACTIVE');
    const revoked = await tx.license.updateMany({
      where: { purchaseId: opts.purchaseId, revokedAt: null },
      data: { revokedAt: new Date() },
    });
    if (opts.amountNanm <= 0n) return { fee: 0n, royalty: 0n, creatorShare: 0n, licensesRevoked: revoked.count };

    const treasury = await ensureTreasury(tx);
    const fee = bps(opts.amountNanm, opts.feeBps);
    const royalty = opts.forkParentCreatorId && opts.forkRoyaltyBps ? bps(opts.amountNanm, opts.forkRoyaltyBps) : 0n;
    const creatorShare = opts.amountNanm - fee - royalty;
    if (creatorShare < 0n) throw new LedgerError('BAD_SPLIT', 'fee+royalty exceed amount');
    const memo = opts.memo ?? 'store refund';
    await post(tx, opts.creatorId, -creatorShare, 'ADJUSTMENT', opts.purchaseId, `${memo} (sale reversal)`);
    await post(tx, treasury.id, -fee, 'ADJUSTMENT', opts.purchaseId, `${memo} (fee reversal)`);
    if (royalty > 0n && opts.forkParentCreatorId) {
      await post(tx, opts.forkParentCreatorId, -royalty, 'ADJUSTMENT', opts.purchaseId, `${memo} (royalty reversal)`);
    }
    await post(tx, opts.buyerId, opts.amountNanm, 'REFUND', opts.purchaseId, memo);
    return { fee, royalty, creatorShare, licensesRevoked: revoked.count };
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
// The Animica Foundation ledger account (address == config.foundationAddress), created on demand.
async function ensureFoundation(tx: Tx) {
  const addr = config.foundationAddress;
  let acct = await tx.account.findUnique({ where: { address: addr } });
  if (!acct) {
    acct = await tx.account.create({ data: { address: addr, displayName: 'Animica Foundation', role: 'ADMIN' } });
  }
  return acct;
}

// .anm name-registration + renewal fees route to the Foundation (the Animica Internet's revenue
// stream). The debit is backed by the buyer's deposited ANM (deposit finality already enforced),
// so this is safe against the inclusion!=execution / phantom-deposit class — we never trust a raw
// tx here. The Foundation withdraws its accrued balance on-chain.
export async function payNameFee(accountId: string, amountNanm: bigint, ref: string, memo: string) {
  if (amountNanm <= 0n) return;
  return prisma.$transaction(async (tx) => {
    const foundation = await ensureFoundation(tx);
    await post(tx, accountId, -amountNanm, 'PURCHASE_DEBIT', ref, memo);
    await post(tx, foundation.id, amountNanm, 'FEE', ref, memo);
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
