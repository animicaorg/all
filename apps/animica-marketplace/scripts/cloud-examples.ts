// Deploy and execute the documented Animica Python Cloud examples (spec §50, §53).
//
//   npx tsx scripts/cloud-examples.ts [--cleanup]
//
// This is not a mock. It deploys the six example functions in examples/** into the LIVE
// database under a designated `examples` account, through the REAL deployment pipeline
// (validate -> immutable version snapshot -> DA blob -> on-chain DEPLOY-tx anchor -> ACTIVE),
// then EXECUTES each one in the real hardened sandbox and prints the observed result, logs
// and the exact metered cost from the execution receipt.
//
// Idempotent: re-running skips deploys whose source/entrypoint/packages are unchanged (the
// canonical artifact hash decides), tops the demo caller up only when needed, and never
// creates duplicate schedules. `--cleanup` removes everything it created (functions, versions,
// deployments, executions, schedules, the two demo accounts and their ledger entries).
// Platform-fee ledger entries earned by the treasury from the demo runs are kept — deleting
// treasury history would break the balance == SUM(ledger) invariant.
//
// The examples account is granted Pro entitlements via the Founding Developer program (a real,
// time-boxed grant — the in-model mechanism for a non-PayPal Pro grant) with the fee benefit
// expired, so its sales settle at the STANDARD platform fee the docs describe.

import { prisma } from '../lib/db'; // FIRST import: loads .env before lib/config evaluates
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { postInTx } from '../lib/ledger';
import { formatAnm } from '../lib/nanm';
import { invokeFunction, type InvokeResult } from '../lib/cloud/executor';
import { createVersion, computeArtifact } from '../lib/cloud/deploy';
import { NANM } from '../lib/cloud/config';

// Bound the per-deploy wait for on-chain anchor inclusion (confirmation tracking continues in
// the background either way). Respects an operator-set value.
process.env.CLOUD_ANCHOR_INCLUSION_WAIT_MS ||= '20000';

const CLEANUP = process.argv.includes('--cleanup');

// Fixed identities so re-runs find the same rows. These are internal platform ledger
// accounts (like every marketplace account) — deliberately not real chain wallets.
const DEV_ADDRESS = 'anim1examplesdev0cloud0demo0acct0000';
const DEV_HANDLE = 'examples';
const USER_ADDRESS = 'anim1examplesuser0cloud0demo0acct000';
const FUND_NANM = 3n * NANM; // top the demo caller up to 3 ANM when it runs low
const LOW_WATER_NANM = 1n * NANM;

interface ExampleSpec {
  dir: string;
  slug: string;
  name: string;
  description: string;
  timeoutMs: number;
  memoryMb: number;
  capabilities: string[];
  packages: string[];
  perCallNanm: bigint;
  caller: 'anon' | 'user' | 'owner';
  callerKind: 'user' | 'schedule' | 'agent';
  payload: (devSegment: string) => unknown;
  maxSpendNanm?: bigint;
  schedule?: { intervalMinutes: number };
  runTwice?: boolean;
}

const EXAMPLES: ExampleSpec[] = [
  {
    dir: 'hello-api',
    slug: 'hello-api',
    name: 'Hello API',
    description: 'The minimal request/response function — the whole runtime ABI in one file.',
    timeoutMs: 10_000,
    memoryMb: 128,
    capabilities: [],
    packages: [],
    perCallNanm: 0n,
    caller: 'anon',
    callerKind: 'user',
    payload: () => ({ name: 'Animica' }),
  },
  {
    dir: 'ai-summarizer',
    slug: 'ai-summarizer',
    name: 'AI Summarizer',
    description: 'Summarize text with animica.ai.infer (AI_INFERENCE), with an honest extractive fallback.',
    timeoutMs: 90_000,
    memoryMb: 256,
    capabilities: ['AI_INFERENCE'],
    packages: [],
    perCallNanm: 0n,
    caller: 'user',
    callerKind: 'user',
    payload: () => ({
      text:
        'Animica Python Cloud lets developers deploy Python functions that are anchored ' +
        'on-chain and executed off-chain in a hardened container. A deployment stores the ' +
        'verbatim source and its hashes in a DA blob, then broadcasts a signed DEPLOY ' +
        'transaction binding those hashes, so anyone can verify exactly what code serves an ' +
        'endpoint. Execution is metered per call, per CPU millisecond, per memory ' +
        'megabyte-millisecond and per AI token, and every payment is split exactly between ' +
        'the platform fee and the developer, who receives an immediately spendable ledger ' +
        'credit. Capabilities such as AI inference, chain reads, payments and outbound HTTP ' +
        'are mediated by a host broker, because the sandbox itself has no network access at all.',
    }),
  },
  {
    dir: 'scheduled-agent',
    slug: 'scheduled-agent',
    name: 'Scheduled Agent',
    description: 'A chain-height monitor driven by a CloudSchedule, remembering runs in animica.state.',
    timeoutMs: 15_000,
    memoryMb: 128,
    capabilities: ['READ_CHAIN', 'PERSIST_STATE'],
    packages: [],
    perCallNanm: 0n,
    caller: 'owner',
    callerKind: 'schedule',
    payload: () => ({}),
    schedule: { intervalMinutes: 60 },
    runTwice: true,
  },
  {
    dir: 'paid-api',
    slug: 'anm-toolkit',
    name: 'ANM Toolkit (paid)',
    description: 'Exact-integer ANM money math with a 0.005 ANM per-call surcharge — the earnings split, live.',
    timeoutMs: 10_000,
    memoryMb: 128,
    capabilities: [],
    packages: [],
    perCallNanm: 5_000_000n,
    caller: 'user',
    callerKind: 'user',
    payload: () => ({ op: 'split', amount_nanm: '250000000', fee_bps: 2000 }),
  },
  {
    dir: 'agent-calls-app',
    slug: 'agent-calls-app',
    name: 'Agent Calls App',
    description: 'Nested execution: animica.call() into the paid toolkit, under one shared spend budget.',
    timeoutMs: 60_000,
    memoryMb: 128,
    capabilities: ['CALL_FUNCTION'],
    packages: [],
    perCallNanm: 0n,
    caller: 'user',
    callerKind: 'user',
    payload: (seg) => ({ target: `${seg}/anm-toolkit`, payload: { op: 'convert', anm: '0.25' } }),
    maxSpendNanm: 200_000_000n, // 0.2 ANM budget for the whole call tree
  },
  {
    dir: 'ai-python-workflow',
    slug: 'chain-pulse',
    name: 'Chain Pulse (AI + pandas workflow)',
    description: 'animica.http.fetch live chain data -> pandas/numpy block-interval stats -> AI narrative.',
    timeoutMs: 120_000,
    memoryMb: 256,
    capabilities: ['HTTP_FETCH', 'AI_INFERENCE'],
    packages: ['numpy', 'pandas'],
    perCallNanm: 0n,
    caller: 'user',
    callerKind: 'user',
    payload: () => ({ blocks: 12 }),
  },
];

const EXAMPLES_ROOT = path.join(__dirname, '..', 'examples');

let failures = 0;

function day(offsetDays: number): Date {
  return new Date(Date.now() + offsetDays * 86_400_000);
}

async function ensureAccounts() {
  let dev = await prisma.account.findUnique({ where: { address: DEV_ADDRESS } });
  if (!dev) {
    try {
      dev = await prisma.account.create({
        data: { address: DEV_ADDRESS, handle: DEV_HANDLE, displayName: 'Animica Examples' },
      });
    } catch (e: any) {
      if (e?.code !== 'P2002') throw e;
      // handle already claimed by someone else — the endpoint falls back to the address
      dev = await prisma.account.create({ data: { address: DEV_ADDRESS, displayName: 'Animica Examples' } });
      console.log(`  note: handle "${DEV_HANDLE}" was taken; examples are served under the address instead`);
    }
  }
  let user = await prisma.account.findUnique({ where: { address: USER_ADDRESS } });
  if (!user) {
    user = await prisma.account.create({ data: { address: USER_ADDRESS, displayName: 'Examples Demo Caller' } });
  }
  return { dev, user };
}

/** Pro entitlements for the examples account via the Founding Developer program, with the fee
 *  benefit expired so its sales settle at the standard platform rate. */
async function ensureProGrant(devId: string) {
  const acceptedAt = day(-1);
  await prisma.foundingDeveloper.upsert({
    where: { accountId: devId },
    create: {
      accountId: devId,
      status: 'ACCEPTED',
      handle: DEV_HANDLE,
      proUntil: day(90),
      feeBps: 2000,
      feeUntil: acceptedAt, // already expired => standard fee applies
      acceptedAt,
      acceptedBy: 'cloud-examples',
      notes: 'Platform examples account: Pro-grant so the six documented examples stay published.',
    },
    update: { status: 'ACCEPTED', revokedAt: null, proUntil: day(90) },
  });
}

async function ensureFunding(userId: string) {
  const acct = await prisma.account.findUnique({ where: { id: userId }, select: { balanceNanm: true } });
  if ((acct?.balanceNanm ?? 0n) < LOW_WATER_NANM) {
    await prisma.$transaction((tx) => postInTx(tx, userId, FUND_NANM, 'DEPOSIT', 'cloud-examples', 'examples demo funding'));
    console.log(`  funded demo caller with ${formatAnm(FUND_NANM)} ANM`);
  }
}

async function ensureDeployed(spec: ExampleSpec, dev: { id: string }) {
  const source = readFileSync(path.join(EXAMPLES_ROOT, spec.dir, 'handler.py'), 'utf8');

  let fn = await prisma.cloudFunction.findFirst({ where: { ownerId: dev.id, slug: spec.slug } });
  if (!fn) {
    fn = await prisma.cloudFunction.create({
      data: {
        ownerId: dev.id,
        slug: spec.slug,
        name: spec.name,
        description: spec.description,
        entrypoint: 'main',
        visibility: 'PUBLIC',
        timeoutMs: spec.timeoutMs,
        memoryMb: spec.memoryMb,
        capabilities: spec.capabilities,
        perCallNanm: spec.perCallNanm,
      },
    });
  } else {
    fn = await prisma.cloudFunction.update({
      where: { id: fn.id },
      data: {
        name: spec.name,
        description: spec.description,
        entrypoint: 'main',
        timeoutMs: spec.timeoutMs,
        memoryMb: spec.memoryMb,
        capabilities: spec.capabilities,
        perCallNanm: spec.perCallNanm,
        visibility: 'PUBLIC',
        suspendedAt: null,
        suspendedReason: null,
      },
    });
  }

  // Skip the deploy when the canonical artifact (source + entrypoint + packages + runtime)
  // is already the live version.
  const { artifactSha3 } = computeArtifact({ source, entrypoint: 'main', runtime: fn.runtime, packages: [...spec.packages] });
  if (fn.currentVersion > 0 && fn.status === 'PUBLISHED') {
    const current = await prisma.cloudFunctionVersion.findUnique({
      where: { functionId_version: { functionId: fn.id, version: fn.currentVersion } },
      select: { artifactSha3: true, version: true },
    });
    if (current?.artifactSha3 === artifactSha3) {
      console.log(`  up to date: v${current.version} (artifact ${artifactSha3.slice(0, 16)}…)`);
      return fn;
    }
  }

  const r = await createVersion({
    functionId: fn.id,
    source,
    entrypoint: 'main',
    packages: [...spec.packages],
    actorAccountId: dev.id,
  });
  console.log(
    `  deployed v${r.version} status=${r.status} endpoint=${r.endpoint ?? '-'}\n` +
      `    daBlobId=${r.daBlobId ?? '—'}\n` +
      `    anchorTx=${r.anchorTxid ?? 'unanchored (see deployment log for the recorded reason)'}`,
  );
  return (await prisma.cloudFunction.findUnique({ where: { id: fn.id } }))!;
}

async function ensureSchedule(spec: ExampleSpec, fnId: string, ownerId: string) {
  if (!spec.schedule) return;
  const existing = await prisma.cloudSchedule.findFirst({ where: { functionId: fnId } });
  if (existing) {
    console.log(`  schedule exists: every ${existing.intervalMinutes} min (enabled=${existing.enabled}, runs=${existing.runsTotal})`);
    return;
  }
  const s = await prisma.cloudSchedule.create({
    data: {
      functionId: fnId,
      ownerId,
      kind: 'interval',
      intervalMinutes: spec.schedule.intervalMinutes,
      payloadJson: '{}',
      enabled: true,
      nextRunAt: new Date(Date.now() + spec.schedule.intervalMinutes * 60_000),
    },
  });
  console.log(`  created CloudSchedule ${s.id}: every ${spec.schedule.intervalMinutes} min`);
}

function printResult(res: InvokeResult) {
  console.log(`  status=${res.status} duration=${res.durationMs}ms request=${res.requestId}`);
  const body = JSON.stringify(res.result);
  if (body) console.log(`  result: ${body.length > 900 ? body.slice(0, 900) + '…' : body}`);
  if (res.error) console.log(`  error: [${res.errorType ?? '?'}] ${res.error}`);
  for (const l of res.logs) console.log(`  log[${l.level}]: ${l.message}`);
  if (res.stdout.trim()) console.log(`  stdout: ${res.stdout.trim().slice(0, 300)}`);
  const rc = res.receipt;
  if (rc.freeTier) {
    console.log(`  cost: free tier (gross ${rc.grossNanm} nANM) usage cpu=${rc.usage.cpuMs}ms ai=${rc.usage.aiTokensIn}/${rc.usage.aiTokensOut} tok`);
  } else {
    console.log(
      `  cost: ${rc.grossNanm} nANM (${formatAnm(BigInt(rc.grossNanm))} ANM) = platform ${rc.platformFeeNanm} + developer ${rc.developerNanm} @ ${rc.feeBps}bps` +
        ` | usage cpu=${rc.usage.cpuMs}ms ai=${rc.usage.aiTokensIn}/${rc.usage.aiTokensOut} tok`,
    );
  }
}

async function runExample(spec: ExampleSpec, fnId: string, dev: { id: string; handle: string | null; address: string }, userId: string) {
  const seg = dev.handle ?? dev.address;
  const callerAccountId = spec.caller === 'anon' ? null : spec.caller === 'owner' ? dev.id : userId;
  const runs = spec.runTwice ? 2 : 1;
  let last: InvokeResult | null = null;
  for (let i = 0; i < runs; i++) {
    if (runs > 1) console.log(`  --- run ${i + 1} of ${runs} ---`);
    last = await invokeFunction({
      functionId: fnId,
      payload: spec.payload(seg),
      callerAccountId,
      callerKind: spec.callerKind,
      maxSpendNanm: spec.maxSpendNanm ?? null,
    });
    printResult(last);
    if (last.status !== 'succeeded') failures++;
  }
  return last;
}

async function summarize(devId: string, fnIds: string[]) {
  const [execs, dev] = await Promise.all([
    prisma.cloudExecution.aggregate({
      where: { functionId: { in: fnIds } },
      _count: { _all: true },
      _sum: { priceNanm: true, developerNanm: true, platformFeeNanm: true },
    }),
    prisma.account.findUnique({ where: { id: devId }, select: { balanceNanm: true } }),
  ]);
  console.log('\n=== totals (live DB) ===');
  console.log(
    `executions=${execs._count._all} gross=${execs._sum.priceNanm ?? 0n} nANM ` +
      `developerEarned=${execs._sum.developerNanm ?? 0n} nANM platformFees=${execs._sum.platformFeeNanm ?? 0n} nANM`,
  );
  console.log(`examples account spendable balance: ${dev?.balanceNanm ?? 0n} nANM (${formatAnm(dev?.balanceNanm ?? 0n)} ANM)`);
}

async function cleanup() {
  const dev = await prisma.account.findUnique({ where: { address: DEV_ADDRESS } });
  const user = await prisma.account.findUnique({ where: { address: USER_ADDRESS } });
  if (!dev && !user) {
    console.log('nothing to clean up');
    return;
  }
  const fns = dev ? await prisma.cloudFunction.findMany({ where: { ownerId: dev.id }, select: { id: true } }) : [];
  const fnIds = fns.map((f) => f.id);
  const accountIds = [dev?.id, user?.id].filter(Boolean) as string[];

  if (fnIds.length) {
    await prisma.cloudExecutionLog.deleteMany({ where: { execution: { functionId: { in: fnIds } } } });
    await prisma.cloudExecution.deleteMany({ where: { functionId: { in: fnIds } } });
    await prisma.cloudSchedule.deleteMany({ where: { functionId: { in: fnIds } } });
    await prisma.cloudFunction.deleteMany({ where: { id: { in: fnIds } } }); // versions/deployments/secrets cascade
  }
  await prisma.usageCounter.deleteMany({ where: { accountId: { in: accountIds } } });
  await prisma.ledgerEntry.deleteMany({ where: { accountId: { in: accountIds } } });
  await prisma.account.deleteMany({ where: { id: { in: accountIds } } });
  console.log(
    `cleaned up: ${fnIds.length} functions (+versions/deployments/executions/schedules), ${accountIds.length} demo accounts.\n` +
      'Treasury FEE ledger entries earned from the demo runs are kept (deleting treasury history would break balance == SUM(ledger)).',
  );
}

async function main() {
  if (CLEANUP) {
    await cleanup();
    return;
  }

  console.log('\n=== Animica Python Cloud — deploy & run the documented examples ===\n');
  const { dev, user } = await ensureAccounts();
  await ensureProGrant(dev.id);
  await ensureFunding(user.id);
  const seg = dev.handle ?? dev.address;
  console.log(`examples account: ${seg} (${dev.id})\n`);

  const deployed: { spec: ExampleSpec; fnId: string }[] = [];
  for (const spec of EXAMPLES) {
    console.log(`--- deploy ${spec.dir} -> ${seg}/${spec.slug} ---`);
    try {
      const fn = await ensureDeployed(spec, dev);
      await ensureSchedule(spec, fn.id, dev.id);
      deployed.push({ spec, fnId: fn.id });
    } catch (e: any) {
      failures++;
      console.log(`  DEPLOY FAILED: [${e?.code ?? '?'}] ${e?.message ?? e}`);
      if (e?.details) console.log(`  details: ${JSON.stringify(e.details).slice(0, 500)}`);
    }
    console.log('');
  }

  for (const { spec, fnId } of deployed) {
    console.log(`--- execute ${seg}/${spec.slug} (caller=${spec.caller}) ---`);
    try {
      await runExample(spec, fnId, dev, user.id);
    } catch (e: any) {
      failures++;
      console.log(`  EXECUTION REFUSED: [${e?.code ?? '?'}] ${e?.message ?? e}`);
    }
    console.log('');
  }

  await summarize(dev.id, deployed.map((d) => d.fnId));
  console.log(`\n=== ${failures === 0 ? 'ALL SIX EXAMPLES DEPLOYED AND EXECUTED' : failures + ' FAILURE(S)'} ===\n`);
  process.exitCode = failures === 0 ? 0 : 1;
}

main()
  .catch((e) => {
    console.error('cloud-examples ERROR:', e);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
