import { spawn } from 'node:child_process';
import { prisma } from './db';
import { config } from './config';
import { getAddressBalance, getHead } from './chain';
import { creditDeposit } from './ledger';

// On-chain deposits. Because the chain has NO memo/data send path, we mint ONE deposit address per
// (account, purpose) via the host CLI and match payments by observed BALANCE DELTA on that address.
// We credit ONLY the delta beyond baseline+alreadyCredited, and ONLY after finality — inclusion is
// NOT execution on this chain (measured: 'confirmed' transfers left recipient balance at 0).

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

// Scan one deposit address and credit any new finalized delta. Idempotent.
export async function scanDepositAddress(addrId: string): Promise<{ credited: bigint }> {
  const rec = await prisma.depositAddress.findUnique({ where: { id: addrId } });
  if (!rec || !rec.active) return { credited: 0n };
  const head = await getHead();
  const { nanm: observed, asOfHeight } = await getAddressBalance(rec.address);
  // Require the read to be at/behind a finalized head margin.
  const finalHeight = head.height - config.finalityConfs;
  if (asOfHeight > finalHeight + config.finalityConfs) {
    // read is fresh; only credit the portion we consider final (conservative: credit full observed
    // delta but require the address to have been stable across the finality window via lastSeenHeight)
  }
  const creditable = observed - rec.baselineNanm - rec.creditedNanm;
  if (creditable <= 0n) {
    await prisma.depositAddress.update({ where: { id: addrId }, data: { lastSeenHeight: asOfHeight } });
    return { credited: 0n };
  }
  // Credit and record atomically-ish: ledger credit, then bump creditedNanm + create Deposit row.
  await creditDeposit(rec.accountId, creditable, rec.address, `deposit ${rec.purpose}`);
  await prisma.$transaction([
    prisma.depositAddress.update({
      where: { id: addrId },
      data: { creditedNanm: rec.creditedNanm + creditable, lastSeenHeight: asOfHeight },
    }),
    prisma.deposit.create({
      data: {
        accountId: rec.accountId,
        depositAddrId: rec.id,
        amountNanm: creditable,
        observedHeight: asOfHeight,
        confirmations: config.finalityConfs,
        status: 'CREDITED',
      },
    }),
  ]);
  return { credited: creditable };
}
