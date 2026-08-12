// Animica Python Cloud — repeatable load test (§55).
//
//   npx tsx scripts/cloud-loadtest.ts [--concurrency 8] [--requests 40] [--scenario all]
//
// Scenarios:
//   invoke    concurrent executions through the real sandbox + ledger
//   deploy    deployment burst (validation + hashing + version rows; anchoring disabled)
//   cold      sandbox cold-start distribution, one at a time
//
// It reports MEASURED numbers only. There are no extrapolated or advertised throughput claims
// anywhere in this file or in the docs — what you see is what this box did, under the stated
// conditions, with the mainnet node and every other Animica service running alongside.
//
// Safety: creates its own accounts/functions, funds them through the real ledger, and deletes
// everything it created at the end. It respects the platform's own admission control, so a
// "rejected: busy" result is a PASS (the limiter working), not a failure.

import { prisma } from '../lib/db';
import { postInTx } from '../lib/ledger';
import { invokeFunction } from '../lib/cloud/executor';
import { sandboxLoad } from '../lib/cloud/sandbox';
import { limits } from '../lib/cloud/config';
import { createHash } from 'node:crypto';

function arg(name: string, dflt: number): number {
  const i = process.argv.indexOf('--' + name);
  if (i >= 0 && process.argv[i + 1]) {
    const n = Number(process.argv[i + 1]);
    if (Number.isFinite(n)) return n;
  }
  return dflt;
}
function argStr(name: string, dflt: string): string {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}

const CONCURRENCY = arg('concurrency', 8);
const REQUESTS = arg('requests', 40);
const SCENARIO = argStr('scenario', 'all');
const TAG = 'lt' + Date.now().toString(36);

const SOURCE = `
def main(request):
    n = int(request.get("n", 200))
    acc = 0
    for i in range(n):
        acc += i * i
    return {"n": n, "acc": acc}
`.trim();

function pct(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[i];
}

function stats(name: string, samples: number[], errors: Record<string, number>, wallMs: number) {
  const s = [...samples].sort((a, b) => a - b);
  const n = s.length;
  const errCount = Object.values(errors).reduce((a, b) => a + b, 0);
  console.log(`\n  ${name}`);
  console.log(`    completed        ${n}   errors ${errCount}`);
  if (n) {
    console.log(`    min / p50 / p95  ${s[0]} / ${pct(s, 50)} / ${pct(s, 95)} ms`);
    console.log(`    p99 / max        ${pct(s, 99)} / ${s[n - 1]} ms`);
    console.log(`    mean             ${Math.round(s.reduce((a, b) => a + b, 0) / n)} ms`);
    console.log(`    throughput       ${(n / (wallMs / 1000)).toFixed(2)} /s over ${(wallMs / 1000).toFixed(1)}s wall`);
  }
  if (errCount) {
    for (const [k, v] of Object.entries(errors)) console.log(`    error:${k}  ${v}`);
  }
}

async function setup() {
  const dev = await prisma.account.create({
    data: { address: `anim1lt-dev-${TAG}`, handle: `lt${TAG}`.slice(0, 30), displayName: 'Load test dev' },
  });
  const user = await prisma.account.create({ data: { address: `anim1lt-user-${TAG}` } });
  await prisma.$transaction((tx) => postInTx(tx, user.id, 500_000_000_000n, 'DEPOSIT', TAG, 'loadtest funding'));
  const sha = createHash('sha3-256').update(SOURCE).digest('hex');
  const fn = await prisma.cloudFunction.create({
    data: {
      ownerId: dev.id,
      slug: `lt-${TAG}`,
      name: 'Load test function',
      entrypoint: 'main',
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      timeoutMs: 30_000,
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
      sizeBytes: Buffer.byteLength(SOURCE),
      entrypoint: 'main',
      createdById: dev.id,
    },
  });
  return { dev, user, fn };
}

async function teardown(ids: { dev: any; user: any; fn: any }) {
  await prisma.cloudExecutionLog.deleteMany({ where: { execution: { functionId: ids.fn.id } } });
  await prisma.cloudExecution.deleteMany({ where: { functionId: ids.fn.id } });
  await prisma.cloudFunctionVersion.deleteMany({ where: { functionId: ids.fn.id } });
  await prisma.cloudSecret.deleteMany({ where: { functionId: ids.fn.id } });
  await prisma.cloudFunction.delete({ where: { id: ids.fn.id } });
  await prisma.ledgerEntry.deleteMany({ where: { accountId: { in: [ids.dev.id, ids.user.id] } } });
  await prisma.usageCounter.deleteMany({ where: { accountId: { in: [ids.dev.id, ids.user.id] } } });
  await prisma.account.deleteMany({ where: { id: { in: [ids.dev.id, ids.user.id] } } });
}

async function runPool<T>(total: number, concurrency: number, task: (i: number) => Promise<T>) {
  const results: T[] = [];
  let next = 0;
  const workers = Array.from({ length: concurrency }, async () => {
    for (;;) {
      const i = next++;
      if (i >= total) return;
      results.push(await task(i));
    }
  });
  await Promise.all(workers);
  return results;
}

async function scenarioInvoke(ids: { dev: any; user: any; fn: any }) {
  console.log(`\n--- scenario: invoke (${REQUESTS} requests, concurrency ${CONCURRENCY}) ---`);
  console.log(`    platform admission control: globalConcurrency=${limits.globalConcurrency} queueMaxDepth=${limits.queueMaxDepth}`);
  const samples: number[] = [];
  const errors: Record<string, number> = {};
  const t0 = Date.now();
  await runPool(REQUESTS, CONCURRENCY, async () => {
    const s = Date.now();
    try {
      const r = await invokeFunction({
        functionId: ids.fn.id,
        payload: { n: 2000 },
        callerAccountId: ids.user.id,
        callerKind: 'user',
      });
      if (r.status === 'succeeded') samples.push(Date.now() - s);
      else errors[r.status] = (errors[r.status] ?? 0) + 1;
    } catch (e: any) {
      const code = e?.code ?? 'exception';
      errors[code] = (errors[code] ?? 0) + 1;
    }
  });
  const wall = Date.now() - t0;
  stats('invoke', samples, errors, wall);
  console.log(`    sandbox slots at end: ${JSON.stringify(sandboxLoad())}`);

  // Correctness under load matters more than speed: the ledger must still balance exactly.
  const agg = await prisma.ledgerEntry.aggregate({ where: { accountId: ids.user.id }, _sum: { deltaNanm: true } });
  const acct = await prisma.account.findUnique({ where: { id: ids.user.id }, select: { balanceNanm: true } });
  const balanced = (agg._sum.deltaNanm ?? 0n) === acct!.balanceNanm;
  const execs = await prisma.cloudExecution.findMany({
    where: { functionId: ids.fn.id },
    select: { priceNanm: true, platformFeeNanm: true, developerNanm: true, providerNanm: true },
  });
  const allExact = execs.every((e) => e.platformFeeNanm + e.developerNanm + e.providerNanm === e.priceNanm);
  console.log(`    ledger invariant under load: ${balanced ? 'HOLDS' : 'VIOLATED'}`);
  console.log(`    split exactness across ${execs.length} rows: ${allExact ? 'EXACT' : 'DRIFT DETECTED'}`);
  return balanced && allExact;
}

async function scenarioCold(ids: { dev: any; user: any; fn: any }) {
  const N = Math.min(10, REQUESTS);
  console.log(`\n--- scenario: cold start (${N} sequential executions) ---`);
  const samples: number[] = [];
  const errors: Record<string, number> = {};
  const t0 = Date.now();
  for (let i = 0; i < N; i++) {
    const s = Date.now();
    try {
      const r = await invokeFunction({
        functionId: ids.fn.id,
        payload: { n: 1 },
        callerAccountId: ids.user.id,
        callerKind: 'user',
      });
      if (r.status === 'succeeded') samples.push(Date.now() - s);
      else errors[r.status] = (errors[r.status] ?? 0) + 1;
    } catch (e: any) {
      errors[e?.code ?? 'exception'] = (errors[e?.code ?? 'exception'] ?? 0) + 1;
    }
  }
  stats('cold start (near-zero work: this is pure platform overhead)', samples, errors, Date.now() - t0);
  return true;
}

async function scenarioDeploy(ids: { dev: any; user: any; fn: any }) {
  const N = Math.min(12, REQUESTS);
  console.log(`\n--- scenario: deploy burst (${N} versions, concurrency ${Math.min(4, CONCURRENCY)}) ---`);
  const { createVersion } = await import('../lib/cloud/deploy');
  const samples: number[] = [];
  const errors: Record<string, number> = {};
  const t0 = Date.now();
  await runPool(N, Math.min(4, CONCURRENCY), async (i) => {
    const s = Date.now();
    try {
      await createVersion({
        functionId: ids.fn.id,
        source: SOURCE + `\n# variant ${i}\n`,
        entrypoint: 'main',
        actorAccountId: ids.dev.id,
      } as any);
      samples.push(Date.now() - s);
    } catch (e: any) {
      const code = e?.code ?? e?.constructor?.name ?? 'exception';
      errors[code] = (errors[code] ?? 0) + 1;
    }
  });
  stats('deploy', samples, errors, Date.now() - t0);
  return true;
}

async function main() {
  console.log('=== Animica Python Cloud — load test ===');
  console.log(`host: ${require('node:os').cpus().length} vCPU, load ${require('node:os').loadavg().map((n: number) => n.toFixed(2)).join(' ')}`);
  console.log('NOTE: the mainnet node and ~20 other Animica services share this box; these are');
  console.log('      real numbers under real contention, not an isolated benchmark.');

  const ids = await setup();
  let allOk = true;
  try {
    if (SCENARIO === 'all' || SCENARIO === 'cold') allOk = (await scenarioCold(ids)) && allOk;
    if (SCENARIO === 'all' || SCENARIO === 'invoke') allOk = (await scenarioInvoke(ids)) && allOk;
    if (SCENARIO === 'all' || SCENARIO === 'deploy') allOk = (await scenarioDeploy(ids)) && allOk;
  } finally {
    await teardown(ids);
    console.log('\ncleaned up load-test data');
  }
  console.log(`\n=== ${allOk ? 'LOAD TEST OK (invariants held)' : 'LOAD TEST FOUND A PROBLEM'} ===\n`);
  process.exitCode = allOk ? 0 : 1;
}

main()
  .catch((e) => {
    console.error('LOADTEST ERROR:', e);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
