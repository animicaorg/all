// End-to-end proof of the Animica Python Cloud money path (spec §62 "definition of done").
//
//   npx tsx scripts/cloud-e2e.ts [--keep]
//
// This is not a mock. It creates two real accounts, funds the buyer through the real append-only
// ledger, publishes a real Python function, executes it in the real hardened sandbox, and then
// asserts — against the database — that:
//
//   * the user was actually debited,
//   * the developer was actually credited 80%,
//   * the treasury actually received the 20% platform fee,
//   * price == platformFee + developer + provider  (exactly),
//   * the LedgerEntry rows for the execution sum to ZERO,
//   * Account.balanceNanm still equals SUM(LedgerEntry.deltaNanm) for every account it touched,
//   * settlement is exactly-once under a duplicate call,
//   * COGS and contribution margin were recorded.
//
// It cleans up after itself unless --keep is passed.

import { prisma } from '../lib/db';
import { postInTx } from '../lib/ledger';
import { invokeFunction } from '../lib/cloud/executor';
import { settleExecution } from '../lib/cloud/settle';
import { activePolicy, costOf } from '../lib/cloud/pricing';
import { nanmToAnm } from '../lib/nanm';

const KEEP = process.argv.includes('--keep');
const TAG = 'e2e-' + Date.now().toString(36);

let failures = 0;
function check(name: string, cond: boolean, detail = '') {
  if (cond) {
    console.log(`  PASS  ${name}${detail ? '  (' + detail + ')' : ''}`);
  } else {
    failures++;
    console.log(`  FAIL  ${name}${detail ? '  (' + detail + ')' : ''}`);
  }
}

const SOURCE = `
import animica

def main(request, ctx):
    n = int(request.get("n", 10))
    total = sum(i * i for i in range(n))
    animica.log(f"summed {n} squares")
    print("stdout from the function")
    return {"n": n, "sum_of_squares": total, "request_id": ctx.request_id}
`.trim();

async function main() {
  console.log(`\n=== Animica Python Cloud — end-to-end money path (${TAG}) ===\n`);
  const policy = await activePolicy();
  console.log(`policy v${policy.version}: platformFee=${policy.platformFeeBps}bps target margin=${policy.targetMarginBps}bps\n`);

  // --- identities ---------------------------------------------------------
  const dev = await prisma.account.create({
    data: { address: `anim1e2edev${TAG}`, handle: `dev${TAG}`.slice(0, 30), displayName: 'E2E Developer' },
  });
  const user = await prisma.account.create({
    data: { address: `anim1e2euser${TAG}`, displayName: 'E2E User' },
  });

  // Fund the buyer the same way a real deposit would: an append-only ledger credit.
  const FUND = 5_000_000_000n; // 5 ANM
  await prisma.$transaction((tx) => postInTx(tx, user.id, FUND, 'DEPOSIT', TAG, 'e2e funding'));
  console.log(`funded buyer with ${nanmToAnm(FUND)} ANM\n`);

  // --- publish ------------------------------------------------------------
  const fn = await prisma.cloudFunction.create({
    data: {
      ownerId: dev.id,
      slug: `sumsq-${TAG}`,
      name: 'Sum of squares',
      description: 'E2E test function',
      entrypoint: 'main',
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      timeoutMs: 15_000,
      memoryMb: 128,
      capabilities: ['PERSIST_STATE'],
      currentVersion: 1,
    },
  });
  const version = await prisma.cloudFunctionVersion.create({
    data: {
      functionId: fn.id,
      version: 1,
      source: SOURCE,
      sourceSha3: require('node:crypto').createHash('sha3-256').update(SOURCE).digest('hex'),
      artifactSha3: require('node:crypto').createHash('sha3-256').update(SOURCE).digest('hex'),
      sizeBytes: Buffer.byteLength(SOURCE),
      entrypoint: 'main',
      createdById: dev.id,
    },
  });
  console.log(`published ${dev.handle}/${fn.slug} v${version.version}\n`);

  // --- balances before ----------------------------------------------------
  const before = await balances([dev.id, user.id]);

  // --- execute ------------------------------------------------------------
  console.log('--- executing in the hardened sandbox ---');
  const res = await invokeFunction({ functionId: fn.id, payload: { n: 100 }, callerAccountId: user.id, callerKind: 'user' });
  console.log(`  status=${res.status} duration=${res.durationMs}ms`);
  console.log(`  result=${JSON.stringify(res.result)}`);
  console.log(`  logs=${res.logs.map((l) => l.message).join(' | ')}`);
  console.log(`  receipt: gross=${res.receipt.grossNanm} fee=${res.receipt.platformFeeNanm} dev=${res.receipt.developerNanm} feeBps=${res.receipt.feeBps}\n`);

  console.log('--- assertions ---');
  check('execution succeeded', res.status === 'succeeded', res.error ?? '');
  check('function returned the right answer', (res.result as any)?.sum_of_squares === 328350);
  check('structured log captured', res.logs.some((l) => l.message.includes('summed 100 squares')));
  check('stdout captured', res.stdout.includes('stdout from the function'));

  const exec = await prisma.cloudExecution.findUnique({ where: { id: res.receipt.executionId } });
  if (!exec) throw new Error('execution row missing');

  const price = exec.priceNanm;
  const sum = exec.platformFeeNanm + exec.developerNanm + exec.providerNanm;
  check('price == platformFee + developer + provider (exact)', sum === price, `${sum} vs ${price}`);
  check('price is non-zero', price > 0n, `${price} nANM`);
  check('platform fee is 20% of price', exec.platformFeeNanm === (price * BigInt(exec.feeBps)) / 10_000n);
  check('developer got the remainder (80%)', exec.developerNanm === price - exec.platformFeeNanm);
  check('COGS recorded', exec.cogsNanm > 0n, `${exec.cogsNanm} nANM`);
  check('contribution = fee - cogs', exec.contributionNanm === exec.platformFeeNanm - exec.cogsNanm);
  check('billed flag set', exec.billed === true);
  check('pricing policy recorded', exec.pricingPolicyId === policy.id);

  const after = await balances([dev.id, user.id]);
  check('user was debited exactly the price', before.get(user.id)! - after.get(user.id)! === price, `${before.get(user.id)! - after.get(user.id)!} nANM`);
  check('developer was credited exactly their share', after.get(dev.id)! - before.get(dev.id)! === exec.developerNanm, `${after.get(dev.id)! - before.get(dev.id)!} nANM`);

  // Ledger entries for this execution must net to zero across all accounts.
  const entries = await prisma.ledgerEntry.findMany({ where: { ref: exec.id } });
  const net = entries.reduce((a, e) => a + e.deltaNanm, 0n);
  check('ledger entries for the execution sum to zero', net === 0n, `${entries.length} entries, net ${net}`);
  check('treasury received the fee', entries.some((e) => e.kind === 'FEE' && e.deltaNanm === exec.platformFeeNanm));

  // The global invariant: cached balance == SUM(ledger) for every touched account.
  for (const id of [dev.id, user.id]) {
    const agg = await prisma.ledgerEntry.aggregate({ where: { accountId: id }, _sum: { deltaNanm: true } });
    const acct = await prisma.account.findUnique({ where: { id }, select: { balanceNanm: true } });
    check(`balance invariant holds for ${id === dev.id ? 'developer' : 'user'}`, (agg._sum.deltaNanm ?? 0n) === acct!.balanceNanm);
  }

  // Exactly-once: a duplicate settlement must be a no-op, not a second charge.
  const dupe = await settleExecution({
    executionId: exec.id,
    callerAccountId: user.id,
    developerAccountId: dev.id,
    priceNanm: price,
    platformFeeNanm: exec.platformFeeNanm,
    developerNanm: exec.developerNanm,
    providerNanm: 0n,
    cogs: costOf({ cpuMs: exec.cpuMs, memoryMbMs: exec.memoryMbMs, aiTokensIn: 0, aiTokensOut: 0, egressBytes: 0 }, policy),
    feeBps: exec.feeBps,
    policy,
  });
  check('duplicate settlement is refused (exactly-once)', dupe.alreadySettled === true && dupe.settled === false);
  const afterDupe = await balances([user.id]);
  check('duplicate settlement did not move money', afterDupe.get(user.id) === after.get(user.id));

  // Insufficient funds must fail closed.
  const pauper = await prisma.account.create({ data: { address: `anim1e2epoor${TAG}` } });
  let refused = false;
  try {
    await invokeFunction({ functionId: fn.id, payload: { n: 5 }, callerAccountId: pauper.id, callerKind: 'user' });
  } catch (e: any) {
    refused = e?.code === 'insufficient_funds';
  }
  check('an unfunded caller is refused before execution', refused);

  console.log(`\n=== ${failures === 0 ? 'ALL CHECKS PASSED' : failures + ' CHECK(S) FAILED'} ===\n`);

  if (!KEEP) {
    await prisma.cloudExecutionLog.deleteMany({ where: { execution: { functionId: fn.id } } });
    await prisma.cloudExecution.deleteMany({ where: { functionId: fn.id } });
    await prisma.cloudFunctionVersion.deleteMany({ where: { functionId: fn.id } });
    await prisma.cloudSecret.deleteMany({ where: { functionId: fn.id } });
    await prisma.cloudFunction.delete({ where: { id: fn.id } });
    await prisma.ledgerEntry.deleteMany({ where: { accountId: { in: [dev.id, user.id, pauper.id] } } });
    await prisma.usageCounter.deleteMany({ where: { accountId: { in: [dev.id, user.id, pauper.id] } } });
    await prisma.account.deleteMany({ where: { id: { in: [dev.id, user.id, pauper.id] } } });
    console.log('cleaned up test data\n');
  } else {
    console.log(`kept: function=${fn.id} dev=${dev.id} user=${user.id}\n`);
  }

  process.exitCode = failures === 0 ? 0 : 1;
}

async function balances(ids: string[]): Promise<Map<string, bigint>> {
  const rows = await prisma.account.findMany({ where: { id: { in: ids } }, select: { id: true, balanceNanm: true } });
  return new Map(rows.map((r) => [r.id, r.balanceNanm]));
}

main()
  .catch((e) => {
    console.error('E2E ERROR:', e);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
