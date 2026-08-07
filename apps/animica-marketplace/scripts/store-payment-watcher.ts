import { prisma } from '../lib/db';
import { config } from '../lib/config';
import { getAddressBalance, getHead, getTransaction } from '../lib/chain';
import { settleStoreWalletPurchase } from '../lib/ledger';
import { parseStoreMemoHex } from '../lib/storeMemo';
import { licenseLeafHash } from '../lib/license';
import {
  acquireAdvisoryLock, loadState, makeLogger, normalizeHex, parseFlags, sameAddress, saveState, toNanm,
} from './store-worker-util';

// Store payment watcher — verifies + credits wallet-paid ANMSTORE1 purchases.
//
// Truth model (measured, not assumed — see lib/chain.ts): inclusion != execution on this
// chain (receipts carry status:null, headers commit zero stateRoot), so a tx being in a
// block proves NOTHING about value movement. A purchase is credited ONLY when ALL hold:
//   (a) tx.getTransactionByHash: to == the DEDICATED store treasury, value >= amountNanm,
//       data bytes EXACTLY == the intent's ANMSTORE1 memo (byte-for-byte, plus a strict
//       fail-closed re-parse cross-checked field-by-field against the intent), sender ==
//       the buyer's wallet address;
//   (b) finality: head - blockNumber >= TX_FINALITY_CONFIRMATIONS (12);
//   (c) sender balance-delta execution proof: the buyer's balance, snapshotted by THIS
//       watcher before the tx was included, must have dropped by >= value after finality.
//       DEVIATION FROM DESIGN (forced by the node): the design called for
//       state.getAddressBalance(buyer, as_of=height) historical reads, but the live RPC
//       has no historical state (state-commitment SHADOW — rpc/methods/state.py only
//       serves 'latest'). So the watcher captures its own pre-inclusion baseline: on the
//       first sighting of a submitted, not-yet-included tx it records the buyer's balance
//       (var/store-workers state). PROVEN => credit; FAILED (baseline exists, no debit
//       observed) => NEVER credit, flag admin; UNAVAILABLE (first seen after inclusion)
//       => allowed only when STORE_REQUIRE_SENDER_PROOF != '1' (default), leaning on (d);
//   (d) treasury running-baseline reconciliation — the hard value-conservation gate:
//       observed confirmed treasury balance - baseline - SUM(already-verified intents)
//       must cover the amount being credited. The treasury address is used ONLY for
//       ANMSTORE1 purchases, so any shortfall means a claimed payment did not execute
//       (phantom-deposit fraud, memory: animica_phantom_deposit_718) => crediting HALTS
//       (sticky until an operator clears state.halted) and admin is flagged. A phantom tx
//       can therefore never mint ledger money; at worst it pauses the store.
// On pass, in ONE DB transaction: intent.verifiedAt claimed (exactly-once), Purchase =>
// ACTIVE, ledger split 70/30 (STORE_FEE_BPS) via settleStoreWalletPurchase, License row
// issued (leafHash feeds the next ANMLIC1 anchor batch).
//
// Ops: systemd timer deploy/systemd/animica-store-payment-watcher.* (flock + pg advisory
// lock; FILES ONLY — the orchestrator installs/enables). Gated OFF by default:
// STORE_PAYMENTS_ENABLED=1 arms it; anything else = observe-only dry run. --dry-run / DRY_RUN=1
// forces observe-only regardless.

const WORKER = 'store-payment-watcher';
const log = makeLogger(WORKER);

const ENABLED = process.env.STORE_PAYMENTS_ENABLED === '1';
const REQUIRE_SENDER_PROOF = process.env.STORE_REQUIRE_SENDER_PROOF === '1';
// Submitted txid not found on chain after this grace => hard-fail the intent.
const TX_NOT_FOUND_GRACE_MS = Number(process.env.STORE_TX_NOT_FOUND_GRACE_SECS ?? 7200) * 1000;
const BATCH = Number(process.env.STORE_WATCHER_BATCH ?? 50);

interface SenderBaseline { balanceNanm: string; height: number; at: string }
interface WatcherState {
  treasuryBaselineNanm: string | null; // observed treasury balance before ANY store credit
  halted: { at: string; reason: string } | null; // sticky credit halt — operator clears (STORE_RESET_HALT=1 run)
  senders: Record<string, SenderBaseline>; // purchaseId -> pre-inclusion buyer balance snapshot
  adminFlags: { at: string; event: string; detail: string }[];
}

const state = loadState<WatcherState>(WORKER, {
  treasuryBaselineNanm: null,
  halted: null,
  senders: {},
  adminFlags: [],
});

function flagAdmin(event: string, detail: string) {
  state.adminFlags.push({ at: new Date().toISOString(), event, detail });
  state.adminFlags = state.adminFlags.slice(-200);
  log('critical', event, { detail, adminAttention: true });
}

function storeFeeBpsFor(listingType: string): number {
  if (listingType === 'APP' || listingType === 'DIGITAL_GOOD') return config.storeFeeBps;
  return config.feeBps;
}

async function latestApprovedBuild(listingId: string) {
  const stable = await prisma.appBuild.findFirst({
    where: { listingId, status: 'APPROVED', channel: 'stable' },
    orderBy: { versionCode: 'desc' },
  });
  if (stable) return stable;
  return prisma.appBuild.findFirst({
    where: { listingId, status: 'APPROVED' },
    orderBy: { versionCode: 'desc' },
  });
}

// Cancel intents that expired without a submitted tx (single-use, 30-min TTL).
async function expireStaleIntents(dryRun: boolean) {
  const stale = await prisma.storePaymentIntent.findMany({
    where: { submittedTxid: null, verifiedAt: null, failReason: null, expiresAt: { lt: new Date() } },
    select: { id: true, purchaseId: true },
    take: 200,
  });
  for (const it of stale) {
    if (dryRun) {
      log('info', 'would_expire_intent', { intentId: it.id, purchaseId: it.purchaseId });
      continue;
    }
    await prisma.$transaction([
      prisma.storePaymentIntent.update({ where: { id: it.id }, data: { failReason: 'expired (no tx submitted within TTL)' } }),
      prisma.purchase.updateMany({ where: { id: it.purchaseId, status: 'PENDING_PAYMENT' }, data: { status: 'CANCELLED' } }),
    ]);
    log('info', 'intent_expired', { intentId: it.id, purchaseId: it.purchaseId });
  }
  return stale.length;
}

type Verdict =
  | { kind: 'credit'; blockNumber: number; valueNanm: bigint; senderProof: 'proven' | 'unavailable' }
  | { kind: 'retry'; reason: string }
  | { kind: 'fail'; reason: string };

// Steps (a)+(b)+(c) for one intent. Fail-closed: any anomaly is 'fail' (permanent) or
// 'retry' (transient), never a credit.
async function verifyIntent(intent: any, headHeight: number): Promise<Verdict> {
  const purchase = intent.purchase;
  const buyerAddress: string = purchase.buyer.address;
  const txid: string = intent.submittedTxid;

  const tx = await getTransaction(txid);
  if (!tx) {
    // Capture the sender baseline even before the tx is visible — the earlier the better.
    await captureSenderBaseline(purchase.id, buyerAddress, null);
    const age = Date.now() - new Date(intent.createdAt).getTime();
    if (new Date() > new Date(intent.expiresAt) && age > TX_NOT_FOUND_GRACE_MS) {
      return { kind: 'fail', reason: 'submitted tx not found on chain after grace' };
    }
    return { kind: 'retry', reason: 'tx not found yet' };
  }

  // (a) recipient, sender, value, memo — every check fail-closed.
  if (!sameAddress(tx.to, intent.payTo)) return { kind: 'fail', reason: 'tx recipient is not the store treasury' };
  if (!sameAddress(tx.from, buyerAddress)) return { kind: 'fail', reason: 'tx sender is not the buyer' };
  const value = toNanm(tx.value);
  if (value === null) return { kind: 'fail', reason: 'unparseable tx value' };
  if (value < BigInt(intent.amountNanm)) return { kind: 'fail', reason: `tx value ${value} < required ${intent.amountNanm}` };

  const txData = normalizeHex(tx.data);
  const wantMemo = normalizeHex(intent.memoHex);
  if (!txData || !wantMemo) return { kind: 'fail', reason: 'missing/unparseable tx data or intent memo' };
  if (txData !== wantMemo) return { kind: 'fail', reason: 'tx data != expected ANMSTORE1 memo bytes' };
  const parsed = parseStoreMemoHex(txData);
  if (!parsed.ok) return { kind: 'fail', reason: `memo failed strict parse: ${parsed.reason}` };
  const memo = parsed.memo;
  if (
    memo.pid !== purchase.id ||
    memo.l !== purchase.listingId ||
    memo.b !== buyerAddress ||
    memo.n !== intent.nonce ||
    memo.a !== BigInt(intent.amountNanm)
  ) {
    return { kind: 'fail', reason: 'memo fields do not match the intent' };
  }

  // (b) finality.
  const blockNumber = tx.blockNumber != null ? Number(tx.blockNumber) : (tx.block_number != null ? Number(tx.block_number) : null);
  if (blockNumber === null) {
    await captureSenderBaseline(purchase.id, buyerAddress, null);
    return { kind: 'retry', reason: 'tx not yet included' };
  }
  const confs = headHeight - blockNumber;
  if (confs < config.finalityConfs) {
    // If we're seeing it pre-inclusion-read but it is already included, a baseline captured
    // NOW would be post-execution — captureSenderBaseline refuses in that case.
    await captureSenderBaseline(purchase.id, buyerAddress, blockNumber);
    return { kind: 'retry', reason: `waiting finality (${confs}/${config.finalityConfs})` };
  }

  // (c) sender balance-delta execution proof (see header for the design deviation).
  const baseline = state.senders[purchase.id];
  if (baseline && baseline.height < blockNumber) {
    const now = await getAddressBalance(buyerAddress);
    const delta = BigInt(baseline.balanceNanm) - now.nanm;
    if (delta >= value) {
      return { kind: 'credit', blockNumber, valueNanm: value, senderProof: 'proven' };
    }
    // Incoming funds during the window can mask the debit (false negative), but crediting
    // anyway would defeat the proof — refuse, flag, let the operator decide.
    flagAdmin('sender_proof_failed', `purchase ${purchase.id} tx ${txid}: baseline ${baseline.balanceNanm}@${baseline.height} - current ${now.nanm} = delta ${delta} < value ${value}`);
    return { kind: 'retry', reason: 'sender balance-delta execution proof failed (flagged for admin)' };
  }
  if (REQUIRE_SENDER_PROOF) {
    flagAdmin('sender_proof_unavailable', `purchase ${purchase.id} tx ${txid}: no pre-inclusion baseline and STORE_REQUIRE_SENDER_PROOF=1`);
    return { kind: 'retry', reason: 'sender proof unavailable (strict mode)' };
  }
  return { kind: 'credit', blockNumber, valueNanm: value, senderProof: 'unavailable' };
}

// Record the buyer's balance ONLY while the tx is provably not yet included (blockNumber
// null / tx unseen). A snapshot taken at-or-after inclusion could be post-execution and
// would fake a "no debit" failure — never take one.
async function captureSenderBaseline(purchaseId: string, buyerAddress: string, blockNumber: number | null) {
  if (state.senders[purchaseId] || blockNumber !== null) return;
  try {
    const b = await getAddressBalance(buyerAddress);
    state.senders[purchaseId] = { balanceNanm: b.nanm.toString(), height: b.asOfHeight, at: new Date().toISOString() };
    log('debug', 'sender_baseline_captured', { purchaseId, buyerAddress, balanceNanm: b.nanm, height: b.asOfHeight });
  } catch (e: any) {
    log('warn', 'sender_baseline_read_failed', { purchaseId, error: String(e?.message ?? e) });
  }
}

// (d) Treasury running-baseline reconciliation. creditedSum is recomputed from the DB every
// time (survives state-file loss); the baseline persists in the state file and is
// initialized once — from STORE_TREASURY_BASELINE_NANM if set, else from the observed
// balance minus everything already verified (a too-HIGH baseline under-credits = safe).
async function treasuryCoverage(requiredMinHeight: number): Promise<{ availableNanm: bigint } | { retry: string }> {
  const observed = await getAddressBalance(config.storeTreasuryAddress);
  if (observed.asOfHeight < requiredMinHeight) return { retry: `treasury balance read at ${observed.asOfHeight} < required ${requiredMinHeight}` };
  const agg = await prisma.storePaymentIntent.aggregate({
    _sum: { amountNanm: true },
    where: { verifiedAt: { not: null } },
  });
  const creditedSum = agg._sum.amountNanm ?? 0n;
  if (state.treasuryBaselineNanm === null) {
    const envBase = process.env.STORE_TREASURY_BASELINE_NANM;
    let base: bigint;
    if (envBase != null && /^[0-9]+$/.test(envBase)) {
      base = BigInt(envBase);
    } else {
      base = observed.nanm - creditedSum;
      if (base < 0n) base = 0n;
    }
    state.treasuryBaselineNanm = base.toString();
    log('warn', 'treasury_baseline_initialized', { baselineNanm: base, observedNanm: observed.nanm, creditedSum, source: envBase != null ? 'env' : 'observed-minus-credited' });
  }
  return { availableNanm: observed.nanm - BigInt(state.treasuryBaselineNanm) - creditedSum };
}

async function creditPurchase(intent: any, txid: string): Promise<void> {
  const purchase = intent.purchase;
  const listing = purchase.listing;
  const price = purchase.price;
  const feeBps = storeFeeBpsFor(listing.type);
  if (!Number.isFinite(feeBps) || feeBps < 0 || feeBps > 10_000) {
    throw new Error(`STORE_FEE_BPS not configured (got ${feeBps}) — refusing to settle (fail-closed)`);
  }
  const isSub = purchase.priceModel === 'SUBSCRIPTION';
  const expiresAt = isSub ? new Date(Date.now() + price.periodDays * 86400_000) : null;
  const build = listing.type === 'APP' ? await latestApprovedBuild(listing.id) : null;

  await prisma.$transaction(async (tx) => {
    // Exactly-once claims — a concurrent run (or crash-retry) can never double-credit.
    const claimedIntent = await tx.storePaymentIntent.updateMany({
      where: { id: intent.id, verifiedAt: null },
      data: { verifiedAt: new Date(), failReason: null },
    });
    if (claimedIntent.count !== 1) throw new Error('intent already claimed');
    const claimedPurchase = await tx.purchase.updateMany({
      where: { id: purchase.id, status: 'PENDING_PAYMENT' },
      data: { status: 'ACTIVE', txid, expiresAt },
    });
    if (claimedPurchase.count !== 1) throw new Error('purchase not in PENDING_PAYMENT');

    await settleStoreWalletPurchase(tx, {
      buyerId: purchase.buyerId,
      creatorId: listing.ownerId,
      amountNanm: BigInt(intent.amountNanm),
      listingId: listing.id,
      forkParentCreatorId: listing.forkOf?.ownerId,
      forkRoyaltyBps: listing.forkOfId ? listing.forkRoyaltyBps : undefined,
      feeBps,
      txid,
    });

    // License row: leafHash needs the row id, so create-then-set within the same tx.
    const lic = await tx.license.create({
      data: {
        purchaseId: purchase.id,
        buyerAddress: purchase.buyer.address,
        listingId: listing.id,
        buildId: build?.id ?? null,
        buildSha3: build?.sha3 ?? null,
        certSha256: build?.certSha256 ?? null,
        kind: isSub ? 'SUBSCRIPTION' : 'PERPETUAL',
        expiresAt,
        leafHash: '',
      },
    });
    const leafHash = licenseLeafHash({
      licenseId: lic.id,
      purchaseTxid: txid,
      buyerAddress: purchase.buyer.address,
      listingId: listing.id,
      buildId: build?.id ?? null,
      buildSha3: build?.sha3 ?? null,
      certSha256: build?.certSha256 ?? null,
      kind: lic.kind,
      issuedAt: lic.issuedAt,
      expiresAt,
    });
    await tx.license.update({ where: { id: lic.id }, data: { leafHash } });
    await tx.listing.update({ where: { id: listing.id }, data: { usersCount: { increment: 1 } } });
  });
}

async function main() {
  const { dryRun: flagDryRun } = parseFlags();
  const dryRun = flagDryRun || !ENABLED;

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }
  if (!config.storeTreasuryAddress) {
    log('error', 'store_treasury_unconfigured', { detail: 'STORE_TREASURY_ADDRESS unset — nothing to verify against (fail-closed)' });
    return;
  }
  if (process.env.STORE_RESET_HALT === '1' && state.halted) {
    log('warn', 'halt_cleared_by_operator', { was: state.halted });
    state.halted = null;
    saveState(WORKER, state);
  }
  log('info', 'run_start', { dryRun, enabled: ENABLED, requireSenderProof: REQUIRE_SENDER_PROOF, halted: state.halted });

  const expired = await expireStaleIntents(dryRun);

  const head = await getHead();
  const intents = await prisma.storePaymentIntent.findMany({
    where: { submittedTxid: { not: null }, verifiedAt: null, purchase: { status: 'PENDING_PAYMENT' } },
    include: {
      purchase: {
        include: {
          buyer: { select: { id: true, address: true } },
          listing: { include: { forkOf: { select: { ownerId: true } } } },
          price: true,
        },
      },
    },
    orderBy: { createdAt: 'asc' },
    take: BATCH,
  });

  let credited = 0, failed = 0, retried = 0;
  for (const intent of intents) {
    const purchaseId = intent.purchaseId;
    const txid = intent.submittedTxid as string;
    try {
      if (!sameAddress(intent.payTo, config.storeTreasuryAddress)) {
        // Intent minted against a different treasury than the one we reconcile — never credit.
        flagAdmin('intent_payto_mismatch', `intent ${intent.id} payTo ${intent.payTo} != STORE_TREASURY_ADDRESS`);
        failed += 1;
        continue;
      }
      const verdict = await verifyIntent(intent, head.height);
      if (verdict.kind === 'retry') {
        retried += 1;
        log('debug', 'intent_retry', { purchaseId, txid, reason: verdict.reason });
        continue;
      }
      if (verdict.kind === 'fail') {
        failed += 1;
        log('warn', 'intent_failed', { purchaseId, txid, reason: verdict.reason });
        if (!dryRun) {
          await prisma.$transaction([
            prisma.storePaymentIntent.update({ where: { id: intent.id }, data: { failReason: verdict.reason.slice(0, 500) } }),
            prisma.purchase.updateMany({ where: { id: purchaseId, status: 'PENDING_PAYMENT' }, data: { status: 'CANCELLED' } }),
          ]);
        }
        continue;
      }

      // (d) value-conservation gate — runs even in dry-run so shortfalls surface early.
      if (state.halted) {
        log('error', 'crediting_halted', { purchaseId, txid, halted: state.halted });
        break;
      }
      const cover = await treasuryCoverage(verdict.blockNumber + config.finalityConfs);
      if ('retry' in cover) {
        retried += 1;
        log('debug', 'intent_retry', { purchaseId, txid, reason: cover.retry });
        continue;
      }
      if (cover.availableNanm < BigInt(intent.amountNanm)) {
        state.halted = {
          at: new Date().toISOString(),
          reason: `treasury shortfall: available ${cover.availableNanm} nANM < required ${intent.amountNanm} (purchase ${purchaseId}, tx ${txid})`,
        };
        flagAdmin('treasury_shortfall_halt', state.halted.reason);
        break; // HALT all crediting — sticky until an operator clears it.
      }

      if (dryRun) {
        log('info', 'would_credit', { purchaseId, txid, amountNanm: intent.amountNanm, senderProof: verdict.senderProof, availableNanm: cover.availableNanm });
        continue;
      }
      await creditPurchase(intent, txid);
      delete state.senders[purchaseId];
      credited += 1;
      log('info', 'purchase_credited', { purchaseId, txid, amountNanm: intent.amountNanm, senderProof: verdict.senderProof });
    } catch (e: any) {
      log('error', 'intent_error', { purchaseId, txid, error: String(e?.message ?? e) });
    }
  }

  // Prune sender baselines for purchases no longer pending (>7 days old as a backstop).
  const cutoff = Date.now() - 7 * 86400_000;
  for (const [pid, b] of Object.entries(state.senders)) {
    if (new Date(b.at).getTime() < cutoff) delete state.senders[pid];
  }
  saveState(WORKER, state);
  log('info', 'run_done', { pending: intents.length, credited, failed, retried, expired, halted: state.halted != null });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
