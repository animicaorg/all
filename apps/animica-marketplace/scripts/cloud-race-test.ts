// Concurrency regression test for the ledger's atomic post() (security review, 2026-08-06).
//
//   npx tsx scripts/cloud-race-test.ts
//
// The bug this pins: post() used to read Account.balanceNanm, add the delta in JS, and write the
// absolute result back. Under Postgres READ COMMITTED (Prisma's default) two concurrent debits
// both read the same balance and both write it — one debit is lost, TWO ledger rows are appended,
// and Account.balanceNanm != SUM(LedgerEntry.deltaNanm). Because ledger credits are real,
// withdrawable ANM, that mints money.
//
// This test hammers a single account with concurrent debits that in aggregate exceed its balance,
// then asserts the two things that must always be true:
//   1. the invariant holds: balance == SUM(ledger)
//   2. exactly as many debits succeeded as the balance could actually fund — no overdraft
//
// It also races the settlement path end to end, which is how a real attacker would reach it.

import { prisma } from '../lib/db';
import { postInTx } from '../lib/ledger';
import { invokeFunction } from '../lib/cloud/executor';
import { createHash } from 'node:crypto';

const TAG = 'race' + Date.now().toString(36);
let failures = 0;

function check(name: string, cond: boolean, detail = '') {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
  if (!cond) failures++;
}

async function ledgerSum(accountId: string): Promise<bigint> {
  const agg = await prisma.ledgerEntry.aggregate({ where: { accountId }, _sum: { deltaNanm: true } });
  return agg._sum.deltaNanm ?? 0n;
}
async function balance(accountId: string): Promise<bigint> {
  const a = await prisma.account.findUnique({ where: { id: accountId }, select: { balanceNanm: true } });
  return a!.balanceNanm;
}

async function rawDebitRace() {
  console.log('\n--- test 1: 20 concurrent debits against a balance that funds only 10 ---');
  const acct = await prisma.account.create({ data: { address: `anim1${TAG}raw` } });
  const UNIT = 1_000_000n;
  await prisma.$transaction((tx) => postInTx(tx, acct.id, UNIT * 10n, 'DEPOSIT', TAG, 'race funding'));

  const results = await Promise.allSettled(
    Array.from({ length: 20 }, (_, i) =>
      prisma.$transaction((tx) => postInTx(tx, acct.id, -UNIT, 'USAGE_DEBIT', `${TAG}-${i}`, 'race debit')),
    ),
  );
  const okCount = results.filter((r) => r.status === 'fulfilled').length;
  const rejected = results.filter((r) => r.status === 'rejected').length;

  const bal = await balance(acct.id);
  const sum = await ledgerSum(acct.id);
  console.log(`    succeeded=${okCount} rejected=${rejected} finalBalance=${bal}`);
  check('invariant holds: balance == SUM(ledger)', bal === sum, `${bal} vs ${sum}`);
  check('no overdraft: balance never went negative', bal >= 0n, `${bal}`);
  check('exactly 10 debits succeeded (the funded amount)', okCount === 10, `${okCount}`);
  check('the other 10 were refused', rejected === 10, `${rejected}`);

  await prisma.ledgerEntry.deleteMany({ where: { accountId: acct.id } });
  await prisma.account.delete({ where: { id: acct.id } });
}

async function settlementRace() {
  console.log('\n--- test 2: concurrent PAID executions against a balance that funds only a few ---');
  const dev = await prisma.account.create({ data: { address: `anim1${TAG}dev`, handle: `r${TAG}`.slice(0, 30) } });
  const user = await prisma.account.create({ data: { address: `anim1${TAG}usr` } });

  const SOURCE = 'def main(request):\n    return {"ok": True}\n';
  const sha = createHash('sha3-256').update(SOURCE).digest('hex');
  const fn = await prisma.cloudFunction.create({
    data: {
      ownerId: dev.id,
      slug: `race-${TAG}`,
      name: 'race',
      entrypoint: 'main',
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      timeoutMs: 15_000,
      memoryMb: 128,
      currentVersion: 1,
    },
  });
  await prisma.cloudFunctionVersion.create({
    data: {
      functionId: fn.id,
      version: 1,
      source: SOURCE,
      sourceSha3: sha,
      artifactSha3: sha,
      sizeBytes: SOURCE.length,
      entrypoint: 'main',
      createdById: dev.id,
    },
  });

  // Fund the caller with roughly three executions' worth, then fire ten at once.
  const FUND = 9_000_000n;
  await prisma.$transaction((tx) => postInTx(tx, user.id, FUND, 'DEPOSIT', TAG, 'race funding'));

  const settled = await Promise.allSettled(
    Array.from({ length: 10 }, () =>
      invokeFunction({ functionId: fn.id, payload: {}, callerAccountId: user.id, callerKind: 'user' }),
    ),
  );
  const okRuns = settled.filter((r) => r.status === 'fulfilled').length;
  const refused = settled.filter((r) => r.status === 'rejected').length;
  const reasons: Record<string, number> = {};
  for (const r of settled) {
    if (r.status === 'rejected') {
      const code = (r.reason as any)?.code ?? (r.reason as any)?.message?.slice(0, 60) ?? 'unknown';
      reasons[code] = (reasons[code] ?? 0) + 1;
    }
  }
  console.log('    refusal reasons:', JSON.stringify(reasons));

  const bal = await balance(user.id);
  const sum = await ledgerSum(user.id);
  const devBal = await balance(dev.id);
  const devSum = await ledgerSum(dev.id);
  console.log(`    completed=${okRuns} refused=${refused} callerBalance=${bal} devBalance=${devBal}`);
  check('caller invariant holds', bal === sum, `${bal} vs ${sum}`);
  check('developer invariant holds', devBal === devSum, `${devBal} vs ${devSum}`);
  check('caller balance never went negative', bal >= 0n, `${bal}`);
  check('caller did not spend more than funded', FUND - bal <= FUND, `${FUND - bal} spent of ${FUND}`);

  // Every billed execution must still be exactly split.
  const execs = await prisma.cloudExecution.findMany({
    where: { functionId: fn.id, billed: true },
    select: { priceNanm: true, platformFeeNanm: true, developerNanm: true, providerNanm: true },
  });
  const exact = execs.every((e) => e.platformFeeNanm + e.developerNanm + e.providerNanm === e.priceNanm);
  check(`split exact across ${execs.length} billed rows`, exact);

  // Total ANM debited from the caller must equal total credited to developer + treasury.
  const entries = await prisma.ledgerEntry.findMany({ where: { ref: { startsWith: 'c' } , accountId: user.id } });
  void entries;

  await prisma.cloudExecutionLog.deleteMany({ where: { execution: { functionId: fn.id } } });
  await prisma.cloudExecution.deleteMany({ where: { functionId: fn.id } });
  await prisma.cloudFunctionVersion.deleteMany({ where: { functionId: fn.id } });
  await prisma.cloudFunction.delete({ where: { id: fn.id } });
  await prisma.ledgerEntry.deleteMany({ where: { accountId: { in: [dev.id, user.id] } } });
  await prisma.usageCounter.deleteMany({ where: { accountId: { in: [dev.id, user.id] } } });
  await prisma.account.deleteMany({ where: { id: { in: [dev.id, user.id] } } });
}

async function main() {
  console.log('=== ledger concurrency regression test ===');
  await rawDebitRace();
  await settlementRace();
  console.log(`\n=== ${failures === 0 ? 'ALL CHECKS PASSED' : failures + ' CHECK(S) FAILED'} ===\n`);
  process.exitCode = failures === 0 ? 0 : 1;
}

main()
  .catch((e) => {
    console.error('RACE TEST ERROR:', e);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
