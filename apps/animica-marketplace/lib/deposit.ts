import { spawn } from 'node:child_process';
import { prisma } from './db';
import { config } from './config';
import { getAddressBalance, getHead } from './chain';
import { creditDepositInTx } from './ledger';

// On-chain deposits. Because the chain has NO memo/data send path, we mint ONE deposit address per
// (account, purpose) via the host CLI and match payments by observed BALANCE DELTA on that address.
// We credit ONLY the delta beyond baseline+alreadyCredited, and ONLY after finality — inclusion is
// NOT execution on this chain (measured: 'confirmed' transfers left recipient balance at 0).
//
// Finality gate (the deposit-side analogue of the store payment watcher's 12-conf rule): the live
// RPC serves only 'latest' state (no historical reads) and a pure balance-delta deposit has no tx
// to hang confirmations on, so finality is enforced as a SUSTAINED OBSERVATION — a newly observed
// uncredited delta is recorded PENDING (amount + the head height it was first seen at), and credited
// only once head - pendingSinceHeight >= finalityConfs with the funds still present. If the delta
// shrinks before maturing (reorg, or the depositor moved funds back out) the window restarts at the
// lower level and the reverted portion is NEVER credited. So a reorg can never mint ledger money.

export function createWalletAddress(label: string): Promise<{ ok: boolean; address?: string; error?: string }> {
  return new Promise((resolve) => {
    const args = ['wallet', 'create', '--label', label, '--alg', 'ml_dsa_65'];
    const env = { ...process.env, ANIMICA_WALLETS_FILE: config.walletsFile };
    const child = spawn(config.cli, args, { env });
    let out = '', errOut = '';
    child.stdout.on('data', (d) => (out += d.toString()));
    child.stderr.on('data', (d) => (errOut += d.toString()));
    child.on('error', (e) => resolve({ ok: false, error: `spawn failed: ${e.message}` }));
    child.on('close', (code) => {
      const m = (out + errOut).match(/anim1[0-9a-z]{20,}/);
      if (code === 0 && m) return resolve({ ok: true, address: m[0] });
      resolve({ ok: false, error: (errOut || out || `exit ${code}`).trim().slice(0, 300) });
    });
  });
}

export async function getOrCreateDepositAddress(accountId: string, purpose = 'topup') {
  const existing = await prisma.depositAddress.findFirst({ where: { accountId, purpose, active: true } });
  if (existing) return existing;
  const label = `mkt-deposit-${accountId.slice(0, 8)}-${purpose.replace(/[^a-z0-9]/gi, '').slice(0, 12)}`;
  const created = await createWalletAddress(label);
  if (!created.ok || !created.address) throw new Error(`deposit address creation failed: ${created.error}`);
  const { nanm, asOfHeight } = await getAddressBalance(created.address).catch(() => ({ nanm: 0n, asOfHeight: 0 }));
  return prisma.depositAddress.create({
    data: { accountId, address: created.address, label, purpose, baselineNanm: nanm, lastSeenHeight: asOfHeight },
  });
}

// Scan one deposit address and credit any newly-arrived, FINALIZED delta. Idempotent and
// reorg-safe (see the finality-gate note in the file header). Safe to call concurrently (the
// on-demand route + the background watcher): the actual credit is a compare-and-swap on the
// DepositAddress pre-image inside one transaction, so a race or crash can never double-credit.
export async function scanDepositAddress(addrId: string): Promise<{ credited: bigint }> {
  const rec = await prisma.depositAddress.findUnique({ where: { id: addrId } });
  if (!rec || !rec.active) return { credited: 0n };

  const head = await getHead();
  const { nanm: observed, asOfHeight } = await getAddressBalance(rec.address);

  // Total delta sitting on-chain that we have NOT yet credited.
  const uncredited = observed - rec.baselineNanm - rec.creditedNanm;

  // Anchor height for the observation. Guard a node that omits as_of_head_height (0): without a
  // real height we cannot age anything, so fall back to head.height; if both are missing we refuse
  // to mature below (fail-closed — never credit without a real confirmation depth).
  const obsHeight = asOfHeight > 0 ? asOfHeight : head.height;

  // Nothing new, or the address was drained back below what we already credited. Clear any stale
  // pending window and record that we looked.
  if (uncredited <= 0n) {
    await prisma.depositAddress.update({
      where: { id: addrId },
      data: { pendingNanm: 0n, pendingSinceHeight: 0, lastSeenHeight: obsHeight },
    });
    return { credited: 0n };
  }

  // (Re)start the finality window on the first sighting of a delta, or whenever the observed delta
  // has SHRUNK below what we were aging (a reorg or a withdraw-back must never mature). We only ever
  // age a level that is currently present on-chain.
  if (rec.pendingNanm <= 0n || uncredited < rec.pendingNanm) {
    await prisma.depositAddress.update({
      where: { id: addrId },
      data: { pendingNanm: uncredited, pendingSinceHeight: obsHeight, lastSeenHeight: obsHeight },
    });
    return { credited: 0n };
  }

  // A pending delta is aging and is still fully present (uncredited >= pending). Mature it only
  // after the finality window has elapsed against a real anchor height.
  const confs = rec.pendingSinceHeight > 0 && head.height > 0 ? head.height - rec.pendingSinceHeight : -1;
  if (confs < config.finalityConfs) {
    await prisma.depositAddress.update({ where: { id: addrId }, data: { lastSeenHeight: obsHeight } });
    return { credited: 0n };
  }

  // Matured. Credit exactly the aged pending amount (any growth beyond it starts a fresh window on
  // the next scan). Ledger credit + creditedNanm high-water bump + Deposit row commit in ONE
  // transaction, gated by a compare-and-swap on the exact pre-image (pendingNanm/pendingSinceHeight/
  // creditedNanm): only one racing caller can flip it, and a crash rolls the whole thing back.
  const creditAmt = rec.pendingNanm;
  let credited = 0n;
  await prisma.$transaction(async (tx) => {
    const claim = await tx.depositAddress.updateMany({
      where: {
        id: addrId,
        pendingNanm: rec.pendingNanm,
        pendingSinceHeight: rec.pendingSinceHeight,
        creditedNanm: rec.creditedNanm,
      },
      data: {
        creditedNanm: rec.creditedNanm + creditAmt,
        pendingNanm: 0n,
        pendingSinceHeight: 0,
        lastSeenHeight: obsHeight,
      },
    });
    if (claim.count !== 1) return; // another scan already credited this window — idempotent no-op.
    await creditDepositInTx(tx, rec.accountId, creditAmt, rec.address, `deposit ${rec.purpose}`);
    await tx.deposit.create({
      data: {
        accountId: rec.accountId,
        depositAddrId: rec.id,
        amountNanm: creditAmt,
        observedHeight: obsHeight,
        confirmations: confs,
        status: 'CREDITED',
      },
    });
    credited = creditAmt;
  });
  return { credited };
}
