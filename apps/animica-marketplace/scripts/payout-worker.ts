import { promises as fs } from 'node:fs';
import { prisma } from '../lib/db';
import { config } from '../lib/config';
import { getAddressBalance, getHead, getTransaction } from '../lib/chain';
import { refundWithdrawal } from '../lib/ledger';
import { sendAnmNanm } from '../lib/send';
import { NANM_PER_ANM } from '../lib/config';
import { acquireAdvisoryLock, makeLogger, parseFlags, sleep, toNanm } from './store-worker-util';

// Payout worker — makes creator withdrawals real: Withdrawal REQUESTED -> SENDING -> SENT
// -> CONFIRMED. Funds were already HELD at request time (holdWithdrawal ledger debit,
// app/api/mkt/v1/withdrawals). This was declared in package.json (worker:payouts) since
// 8.0.0 but never built; pattern mirrors /root/animica/animica-net/scripts/pay-payouts.ts
// (PAYOUT_ENABLED gate + per-tx/per-day caps + confirm poll) with the marketplace's own
// send path (lib/send.ts — absolute CLI path, treasury label wallet).
//
// Safety rails (all fail-closed):
//   - OFF unless PAYOUT_ENABLED=1 (existing config.payoutEnabled) — default = dry-run;
//   - per-tx cap PAYOUT_MAX_PER_TX_ANM: over-cap requests FAIL + ledger refund (they can
//     never send; leaving them queued would wedge the queue head forever);
//   - rolling 24h cap PAYOUT_MAX_PER_DAY_ANM counted over SENDING/SENT/CONFIRMED sentAt —
//     remaining rows simply wait for the window;
//   - refuses to send when the treasury's observed on-chain balance can't cover
//     amount + fee headroom;
//   - SENDING is claimed (updateMany) BEFORE the CLI spawns, with sentAt set, so a crashed
//     run still counts against the day cap and can never double-send: stuck SENDING rows
//     are NEVER auto-retried (the tx may have broadcast) — they are flagged for operator;
//   - CONFIRMED only at inclusion + TX_FINALITY_CONFIRMATIONS depth. Receipts carry no
//     execution status on this chain, so inclusion+finality is the strongest automatic
//     signal for our OWN CLI-signed sends; the ledger hold already happened at request.
//
// Ops: deploy/systemd/animica-store-payouts.* (flock + advisory lock; FILES ONLY —
// orchestrator installs/enables). --dry-run / DRY_RUN=1 forces observe-only.

const WORKER = 'store-payouts';
const log = makeLogger(WORKER);

const FEE_HEADROOM_NANM = BigInt(process.env.PAYOUT_FEE_HEADROOM_NANM ?? String(NANM_PER_ANM)); // 1 ANM
const BATCH = Number(process.env.PAYOUT_BATCH ?? 25);
const STALE_SENT_HOURS = Number(process.env.PAYOUT_STALE_SENT_HOURS ?? 24);

// Confirm SENT rows at inclusion + finality depth.
async function confirmSent(dryRun: boolean): Promise<number> {
  const sent = await prisma.withdrawal.findMany({ where: { status: 'SENT', txid: { not: null } }, take: 100 });
  if (!sent.length) return 0;
  const head = await getHead();
  let confirmed = 0;
  for (const w of sent) {
    const tx = await getTransaction(w.txid as string);
    const blockNumber = tx?.blockNumber != null ? Number(tx.blockNumber) : null;
    if (blockNumber === null) {
      const ageH = (Date.now() - new Date(w.sentAt ?? w.createdAt).getTime()) / 3600_000;
      if (ageH > STALE_SENT_HOURS) {
        log('critical', 'sent_tx_not_included', { withdrawalId: w.id, txid: w.txid, ageHours: Math.round(ageH), detail: 'operator: inspect tx; may need re-send after verifying it never landed', adminAttention: true });
      }
      continue;
    }
    if (head.height - blockNumber < config.finalityConfs) continue;
    if (dryRun) {
      log('info', 'would_confirm', { withdrawalId: w.id, txid: w.txid, blockHeight: blockNumber });
      continue;
    }
    await prisma.withdrawal.updateMany({
      where: { id: w.id, status: 'SENT' },
      data: { status: 'CONFIRMED', confirmedHeight: blockNumber, confirmedAt: new Date() },
    });
    confirmed += 1;
    log('info', 'withdrawal_confirmed', { withdrawalId: w.id, txid: w.txid, blockHeight: blockNumber });
  }
  return confirmed;
}

// Stuck SENDING rows mean a run died mid-send. The tx MAY have broadcast — never auto-retry.
async function flagStuckSending(): Promise<void> {
  const stuck = await prisma.withdrawal.findMany({
    where: { status: 'SENDING', sentAt: { lt: new Date(Date.now() - 30 * 60_000) } },
    select: { id: true, amountNanm: true, toAddress: true, sentAt: true },
  });
  for (const w of stuck) {
    log('critical', 'stuck_sending', { withdrawalId: w.id, amountNanm: w.amountNanm, toAddress: w.toAddress, sentAt: w.sentAt, detail: 'crashed mid-send; operator must check the chain before resolving (never auto-retried)', adminAttention: true });
  }
}

async function sentLast24hNanm(): Promise<bigint> {
  const agg = await prisma.withdrawal.aggregate({
    _sum: { amountNanm: true },
    where: { status: { in: ['SENDING', 'SENT', 'CONFIRMED'] }, sentAt: { gte: new Date(Date.now() - 86400_000) } },
  });
  return agg._sum.amountNanm ?? 0n;
}

/**
 * Why the treasury wallet cannot be used, or null when it is fine.
 *
 * Reads the wallets file the CLI is given (ANIMICA_WALLETS_FILE) and looks for the
 * label `--from` will be passed. Also cross-checks MKT_TREASURY_ADDRESS when set: a
 * label and an address that disagree means the balance guard is watching a different
 * wallet than the one being spent, which is worse than no guard because it reads as
 * protection.
 */
async function treasuryWalletMissing(): Promise<string | null> {
  const label = config.treasuryLabel;
  if (!label) return 'MKT_TREASURY_LABEL is empty';
  let raw: string;
  try {
    raw = await fs.readFile(config.walletsFile, 'utf8');
  } catch (e: any) {
    return `cannot read wallets file ${config.walletsFile}: ${String(e?.message ?? e)}`;
  }
  let parsed: any;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return `wallets file ${config.walletsFile} is not valid JSON`;
  }
  const bag = parsed?.wallets ?? parsed;
  const entries: Array<[string, any]> = Array.isArray(bag)
    ? bag.map((w: any) => [w?.label, w])
    : Object.entries(bag ?? {});
  const hit = entries.find(([k, w]) => (w?.label ?? k) === label);
  if (!hit) {
    const known = entries.map(([k, w]) => w?.label ?? k).filter(Boolean).slice(0, 12);
    return `no wallet labelled "${label}" in ${config.walletsFile}`
      + (known.length ? ` (found: ${known.join(', ')})` : '');
  }
  const addr = hit[1]?.address;
  if (!addr) return `wallet "${label}" has no address`;
  if (config.treasuryAddress && config.treasuryAddress !== addr) {
    return `MKT_TREASURY_ADDRESS (${config.treasuryAddress.slice(0, 16)}…) does not match `
      + `wallet "${label}" (${String(addr).slice(0, 16)}…); the balance guard would watch `
      + 'the wrong wallet';
  }
  return null;
}


async function main() {
  const { dryRun: flagDryRun } = parseFlags();
  const dryRun = flagDryRun || !config.payoutEnabled;

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }
  log('info', 'run_start', { dryRun, enabled: config.payoutEnabled, maxPerTxAnm: config.payoutMaxPerTxAnm, maxPerDayAnm: config.payoutMaxPerDayAnm });

  const confirmed = await confirmSent(dryRun);
  await flagStuckSending();

  const maxPerTxNanm = config.payoutMaxPerTxAnm * NANM_PER_ANM;
  const maxPerDayNanm = config.payoutMaxPerDayAnm * NANM_PER_ANM;
  let dayUsed = await sentLast24hNanm();

  // Treasury on-chain balance (the hot wallet lib/send.ts signs from).
  let treasuryBalance: bigint | null = null;
  if (config.treasuryAddress) {
    try {
      treasuryBalance = toNanm((await getAddressBalance(config.treasuryAddress)).nanm);
    } catch (e: any) {
      log('warn', 'treasury_balance_read_failed', { error: String(e?.message ?? e) });
    }
  } else {
    log('warn', 'treasury_address_unset', { detail: 'MKT_TREASURY_ADDRESS unset — skipping on-chain balance guard' });
  }

  // PRE-FLIGHT: does the wallet we sign from actually exist?
  //
  // `sendAnmNanm` spawns `animica tx send --from <MKT_TREASURY_LABEL>`, and SENDING
  // is claimed BEFORE that spawn so a crash can never double-send. The cost of that
  // ordering is that a send which cannot even start still burns the row: it lands in
  // SENDING, and stuck SENDING rows are deliberately never auto-retried because the
  // tx may have broadcast. A label that resolves to nothing would therefore strand
  // the first real withdrawal permanently, with the creator's balance already
  // debited — and it would do it silently, because the worker's own logs would show
  // a normal claim followed by a CLI error.
  //
  // Found exactly that on arming: MKT_TREASURY_LABEL was 'animica-marketplace' and
  // no such wallet existed in the wallets file. Checking here turns a stranded
  // withdrawal into a loud no-op.
  if (!dryRun) {
    const missing = await treasuryWalletMissing();
    if (missing) {
      log('error', 'treasury_wallet_unusable', {
        label: config.treasuryLabel,
        detail: missing,
        action: 'no withdrawal was claimed; fix the wallet label or address and re-run',
      });
      log('info', 'run_done', { queued: 0, sent: 0, failed: 0, deferred: 0,
                                confirmed, dayUsedNanm: dayUsed.toString(), aborted: true });
      return;
    }
  }

  const queue = await prisma.withdrawal.findMany({
    where: { status: 'REQUESTED' },
    orderBy: { createdAt: 'asc' },
    take: BATCH,
  });

  let sentCount = 0, failedCount = 0, deferred = 0;
  for (const w of queue) {
    const amount = BigInt(w.amountNanm);
    try {
      if (amount > maxPerTxNanm) {
        // Can never send under the cap — fail + refund the hold instead of wedging the queue.
        log('warn', 'over_per_tx_cap', { withdrawalId: w.id, amountNanm: amount, capNanm: maxPerTxNanm });
        if (!dryRun) {
          const claimed = await prisma.withdrawal.updateMany({
            where: { id: w.id, status: 'REQUESTED' },
            data: { status: 'FAILED', error: `exceeds per-tx cap ${config.payoutMaxPerTxAnm} ANM` },
          });
          if (claimed.count === 1) await refundWithdrawal(w.accountId, amount, w.id);
        }
        failedCount += 1;
        continue;
      }
      if (dayUsed + amount > maxPerDayNanm) {
        deferred += 1;
        log('info', 'daily_cap_deferred', { withdrawalId: w.id, amountNanm: amount, dayUsedNanm: dayUsed, capNanm: maxPerDayNanm });
        continue;
      }
      if (treasuryBalance !== null && treasuryBalance < amount + FEE_HEADROOM_NANM) {
        deferred += 1;
        log('error', 'treasury_balance_insufficient', { withdrawalId: w.id, amountNanm: amount, treasuryBalanceNanm: treasuryBalance });
        continue;
      }
      if (dryRun) {
        log('info', 'would_send', { withdrawalId: w.id, toAddress: w.toAddress, amountNanm: amount });
        dayUsed += amount;
        continue;
      }

      // Claim BEFORE spawning the CLI — crash after this point leaves an operator-visible
      // SENDING row and never a silent double-send.
      const claimed = await prisma.withdrawal.updateMany({
        where: { id: w.id, status: 'REQUESTED' },
        data: { status: 'SENDING', sentAt: new Date() },
      });
      if (claimed.count !== 1) continue;
      dayUsed += amount;

      const res = await sendAnmNanm(w.toAddress, amount);
      if (res.ok && res.txid) {
        await prisma.withdrawal.updateMany({ where: { id: w.id, status: 'SENDING' }, data: { status: 'SENT', txid: res.txid } });
        if (treasuryBalance !== null) treasuryBalance -= amount;
        sentCount += 1;
        log('info', 'withdrawal_sent', { withdrawalId: w.id, toAddress: w.toAddress, amountNanm: amount, txid: res.txid });
      } else if (res.ok) {
        // CLI exited 0 but printed no txid — the tx may still have broadcast. Operator case.
        log('critical', 'sent_without_txid', { withdrawalId: w.id, detail: 'CLI ok but no txid parsed; left SENDING for operator', adminAttention: true });
      } else if ((res.error ?? '').startsWith('spawn failed')) {
        // The CLI never even started — provably no broadcast: safe to fail + refund the hold.
        await prisma.withdrawal.updateMany({
          where: { id: w.id, status: 'SENDING' },
          data: { status: 'FAILED', error: (res.error ?? 'send failed').slice(0, 500), sentAt: null },
        });
        await refundWithdrawal(w.accountId, amount, w.id);
        dayUsed -= amount;
        failedCount += 1;
        log('error', 'withdrawal_failed', { withdrawalId: w.id, error: res.error });
      } else {
        // Nonzero CLI exit does NOT prove the tx never broadcast (e.g. RPC hiccup after
        // submit). Refunding here could double-pay — leave SENDING for the operator.
        log('critical', 'send_uncertain', { withdrawalId: w.id, error: res.error, detail: 'CLI failed after start; tx MAY have broadcast — left SENDING for operator (check chain, then FAIL+refund or mark SENT)', adminAttention: true });
        failedCount += 1;
      }
      await sleep(500); // let the CLI's nonce bookkeeping breathe between sends
    } catch (e: any) {
      log('error', 'withdrawal_error', { withdrawalId: w.id, error: String(e?.message ?? e) });
    }
  }
  log('info', 'run_done', { queued: queue.length, sent: sentCount, failed: failedCount, deferred, confirmed, dayUsedNanm: dayUsed });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
