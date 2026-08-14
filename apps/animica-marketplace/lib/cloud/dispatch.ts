// Animica Python Cloud — the compute provider network / fleet dispatch (§23, §24, §25, §89).
//
// Two execution lanes:
//   local — the gateway's own hardened sandbox (lib/cloud/sandbox.ts). Default.
//   fleet — a self-registered third-party provider claims the job, runs the SAME sandbox
//           contract on its own machine, and posts the result back.
//
// The queue mechanics deliberately mirror the proven media queue (lib/mediaQueue.ts):
// sha3-hashed self-registered bearer tokens, a raw `UPDATE ... WHERE id = (SELECT ... FOR
// UPDATE SKIP LOCKED LIMIT 1)` claim, a lease with heartbeat extension, and a conditional
// CLAIMED/RUNNING -> DONE flip inside a transaction so a double-submit can never double-pay.
//
// THE ECONOMIC DIFFERENCE from the media queue: media miners accrue an IOU. Python Cloud
// providers are paid FOR REAL, as spendable ledger balance, because the customer already paid
// ANM for that execution. settleExecution() posts the provider's SALE_CREDIT in the same
// exactly-once transaction that debits the caller, and splitOf() guarantees the provider's
// share can never exceed the revenue allocated to compute (feeBps + providerShareBps <= 10000,
// §89). That is also why ONLY REVENUE-BEARING executions are ever dispatched to the fleet:
// a provider that completes a job is always paid.
//
// WHAT MAY GO TO THE FLEET (decideLane): only pure-compute executions —
//   * the function declares NO capabilities (a remote provider cannot broker AI/chain/wallet/
//     HTTP host calls; the broker credentials never leave the gateway),
//   * the function/owner has NO secrets (secrets are sealed on the gateway and are never
//     shipped to third-party machines),
//   * the caller is a paying account that is not the developer (free-tier work carries no
//     revenue, so there would be nothing to pay the provider with).
// Everything else runs on the local lane. This is a scheduling policy, not a limitation of the
// wire protocol, and it is enforced at dispatch time — never trusted from the client.

import { createHash, randomBytes } from 'node:crypto';
import { Prisma } from '@prisma/client';
import { prisma } from '../db';
import { ensureAccount } from '../accounts';
import { limits, flags, runtime } from './config';
import { activePolicy, quote, costOf, splitOf, priceForFailure, type Usage, type Policy } from './pricing';
import { settleExecution } from './settle';
import { resolvePlan, priorityFor } from './entitlements';

// ---------------------------------------------------------------------------
// Constants (env-overridable; the hard queue windows live in lib/cloud/config.ts limits.*)
// ---------------------------------------------------------------------------

function envInt(name: string, fallback: number): number {
  const v = process.env[name];
  if (v == null || v === '') return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/** Per-developer admission cap: open fleet jobs (PENDING/CLAIMED/RUNNING) one developer's
 *  functions may occupy, so a single developer cannot exhaust the whole network (§25). */
export const FLEET_PER_DEVELOPER_CAP = envInt('CLOUD_FLEET_DEV_CAP', 20);
/** Reputation floor: a provider at or below this is auto-suspended from claiming. */
export const PROVIDER_REPUTATION_FLOOR = envInt('CLOUD_PROVIDER_REPUTATION_FLOOR', -8);
/** Grace added to a function's timeout when validating a provider-reported wall time. */
const WALL_MS_GRACE = 5_000;

/** The capability vocabulary providers may advertise. `python3.12` is the runtime every job
 *  requires (it is the sandbox image's interpreter); `gpu` is informational until a GPU job
 *  kind exists. Anything else is dropped at registration. */
export const PROVIDER_CAPABILITIES = ['python3.12', 'gpu'] as const;
export const REQUIRED_RUNTIME = 'python3.12';

export class DispatchError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export function sha3(data: string | Buffer): string {
  return createHash('sha3-256').update(data).digest('hex');
}

export function newProviderToken(): string {
  return 'anm_prov_' + randomBytes(32).toString('base64url');
}

const ADDRESS_RE = /^anim1[0-9a-z]{20,}$/;

// ---------------------------------------------------------------------------
// Provider registration + auth (media-miner pattern: token is the credential,
// only its sha3 is stored)
// ---------------------------------------------------------------------------

export interface RegisterProviderInput {
  token: string;
  address: string;
  name?: string;
  capabilities?: string[];
  cpuCores?: number;
  memoryMb?: number;
  gpu?: string | null;
}

function clampInt(v: unknown, lo: number, hi: number, dflt: number): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Math.max(lo, Math.min(hi, Math.trunc(n)));
}

export async function registerProvider(input: RegisterProviderInput) {
  if (!ADDRESS_RE.test(input.address)) {
    throw new DispatchError('bad_address', 'address must be a bech32m anim1... payout address');
  }
  if (typeof input.token !== 'string' || input.token.length < 32) {
    throw new DispatchError('bad_token', 'token must be at least 32 characters');
  }
  const keyHash = sha3(input.token);
  // Earnings are REAL spendable balance — they need a ledger Account to land in.
  const account = await ensureAccount(input.address);

  const caps = Array.from(
    new Set([
      REQUIRED_RUNTIME,
      ...(input.capabilities ?? []).filter((c) => (PROVIDER_CAPABILITIES as readonly string[]).includes(c)),
    ]),
  );

  const data = {
    accountId: account.id,
    address: input.address,
    name: (input.name ?? '').slice(0, 80),
    capabilities: caps,
    cpuCores: clampInt(input.cpuCores, 1, 512, 1),
    memoryMb: clampInt(input.memoryMb, 256, 1_048_576, 1024),
    gpu: input.gpu ? String(input.gpu).slice(0, 64) : null,
    lastSeenAt: new Date(),
  };

  const existing = await prisma.cloudProvider.findUnique({ where: { keyHash } });
  if (existing && (existing.status === 'SUSPENDED' || existing.status === 'DISABLED')) {
    // Registration is a heartbeat, but it must never resurrect a suspended provider.
    await prisma.cloudProvider.update({ where: { keyHash }, data: { lastSeenAt: new Date() } });
    return prisma.cloudProvider.findUniqueOrThrow({ where: { keyHash } });
  }

  return prisma.cloudProvider.upsert({
    where: { keyHash },
    create: { keyHash, ...data, status: 'ACTIVE' },
    update: { ...data, status: 'ACTIVE' },
  });
}

export async function resolveProvider(token: string) {
  if (!token || token.length < 32) return null;
  return prisma.cloudProvider.findUnique({ where: { keyHash: sha3(token) } });
}

// ---------------------------------------------------------------------------
// Lazy maintenance — called opportunistically on claim/stats so the queue
// self-heals without a cron (same discipline as mediaQueue.sweep()).
// ---------------------------------------------------------------------------

let _lastSweep = 0;

export async function sweepFleet(force = false): Promise<void> {
  const now = Date.now();
  if (!force && now - _lastSweep < 4000) return;
  _lastSweep = now;
  const nowD = new Date(now);
  try {
    // Lease expired, attempts left: back to PENDING so another provider can pick it up.
    const requeued = await prisma.cloudJob.findMany({
      where: { status: { in: ['CLAIMED', 'RUNNING'] }, leaseUntil: { lt: nowD }, attempts: { lt: limits.jobMaxAttempts } },
      select: { id: true, executionId: true },
    });
    if (requeued.length) {
      await prisma.cloudJob.updateMany({
        where: { id: { in: requeued.map((j) => j.id) } },
        data: { status: 'PENDING', providerId: null, leaseUntil: null, claimedAt: null },
      });
      await prisma.cloudExecution.updateMany({
        where: { id: { in: requeued.map((j) => j.executionId) }, status: 'RUNNING' },
        data: { status: 'DISPATCHED' },
      });
    }
    // Lease expired, attempts exhausted: the job — and its execution — fail terminally.
    const dead = await prisma.cloudJob.findMany({
      where: { status: { in: ['CLAIMED', 'RUNNING'] }, leaseUntil: { lt: nowD }, attempts: { gte: limits.jobMaxAttempts } },
      select: { id: true, executionId: true },
    });
    if (dead.length) {
      await prisma.cloudJob.updateMany({
        where: { id: { in: dead.map((j) => j.id) } },
        data: { status: 'EXPIRED', error: 'no provider completed the job in time', providerId: null, finishedAt: nowD },
      });
      for (const j of dead) await closeUnservedExecution(j.executionId, 'fleet_expired', 'no provider completed the job in time');
    }
    // Stale providers drop to IDLE (they come back ACTIVE on their next claim/heartbeat).
    await prisma.cloudProvider.updateMany({
      where: { status: 'ACTIVE', lastSeenAt: { lt: new Date(now - limits.providerStaleSeconds * 1000) } },
      data: { status: 'IDLE' },
    });
  } catch {
    /* best-effort */
  }
}

/** A fleet execution that terminally never produced a verified result: the customer is NOT
 *  charged (no verified resources were delivered), the row is closed exactly once. */
async function closeUnservedExecution(executionId: string, code: string, message: string) {
  await prisma.$transaction(async (tx) => {
    const claim = await tx.cloudExecution.updateMany({
      where: { id: executionId, billed: false },
      data: { billed: true },
    });
    if (claim.count !== 1) return; // already settled/closed elsewhere
    await tx.cloudExecution.update({
      where: { id: executionId },
      data: {
        status: 'FAILED',
        errorCode: code,
        error: message,
        finishedAt: new Date(),
        priceNanm: 0n,
        platformFeeNanm: 0n,
        developerNanm: 0n,
        providerNanm: 0n,
        freeTier: false,
      },
    });
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Lane decision + enqueue
// ---------------------------------------------------------------------------

export interface LaneDecision {
  lane: 'local' | 'fleet';
  reason: string;
}

export async function onlineProviderCount(capability: string = REQUIRED_RUNTIME): Promise<number> {
  return prisma.cloudProvider.count({
    where: {
      status: { in: ['ACTIVE', 'IDLE'] },
      lastSeenAt: { gte: new Date(Date.now() - limits.providerStaleSeconds * 1000) },
      capabilities: { has: capability },
      reputation: { gt: PROVIDER_REPUTATION_FLOOR },
    },
  });
}

/**
 * Decide which lane an invocation should run on. Pure policy — call it BEFORE creating the
 * CloudJob. Every check is server-held state; nothing here trusts the client.
 */
export async function decideLane(opts: {
  functionId: string;
  functionCapabilities: string[];
  ownerId: string;
  callerAccountId: string | null;
  perCallNanm: bigint;
}): Promise<LaneDecision> {
  if (!flags.computeMarket) return { lane: 'local', reason: 'compute market disabled' };
  if ((opts.functionCapabilities ?? []).length > 0) {
    return { lane: 'local', reason: 'capabilities require the gateway broker' };
  }
  if (!opts.callerAccountId || opts.callerAccountId === opts.ownerId) {
    return { lane: 'local', reason: 'no revenue to fund a provider share' };
  }
  const secretCount = await prisma.cloudSecret.count({
    where: {
      ownerId: opts.ownerId,
      OR: [{ functionId: opts.functionId }, { functionId: null }],
      NOT: { name: { startsWith: '__state__' } },
    },
  });
  if (secretCount > 0) return { lane: 'local', reason: 'secrets never leave the gateway' };

  const online = await onlineProviderCount();
  if (online === 0) return { lane: 'local', reason: 'no providers online' };

  const inFlight = await prisma.cloudJob.count({
    where: { status: { in: ['PENDING', 'CLAIMED', 'RUNNING'] }, execution: { developerAccountId: opts.ownerId } },
  });
  if (inFlight >= FLEET_PER_DEVELOPER_CAP) {
    return { lane: 'local', reason: 'per-developer fleet admission cap reached' };
  }
  return { lane: 'fleet', reason: `${online} provider(s) online` };
}

/**
 * Enqueue an existing CloudExecution for the fleet. The execution must already be recorded
 * (QUEUED) with its funding checks done by the caller. Re-validates the fleet-eligibility
 * policy server-side; the queue priority comes from the CALLER's plan (§25 priorityFor).
 *
 * `requestPayload` is stored in the job because CloudExecution does not persist request
 * bodies; it is handed only to the authenticated claiming provider.
 */
export async function dispatchToFleet(executionId: string, requestPayload: unknown): Promise<{ jobId: string; priority: number }> {
  if (!flags.computeMarket) throw new DispatchError('market_disabled', 'the compute market is disabled');
  const exec = await prisma.cloudExecution.findUnique({
    where: { id: executionId },
    include: { function: { select: { id: true, ownerId: true, capabilities: true, perCallNanm: true } } },
  });
  if (!exec) throw new DispatchError('not_found', 'execution not found');
  if (exec.status !== 'QUEUED') throw new DispatchError('bad_state', `execution is ${exec.status}, expected QUEUED`);

  const decision = await decideLane({
    functionId: exec.functionId,
    functionCapabilities: exec.function.capabilities ?? [],
    ownerId: exec.function.ownerId,
    callerAccountId: exec.callerAccountId,
    perCallNanm: exec.function.perCallNanm,
  });
  if (decision.lane !== 'fleet') throw new DispatchError('not_fleet_eligible', decision.reason);

  const encoded = JSON.stringify({ runtime: REQUIRED_RUNTIME, request: requestPayload ?? {} });
  if (Buffer.byteLength(encoded, 'utf8') > limits.maxRequestBytes + 1024) {
    throw new DispatchError('payload_too_large', `request payload exceeds ${limits.maxRequestBytes} bytes`);
  }

  const plan = exec.callerAccountId ? await resolvePlan(exec.callerAccountId) : null;
  const priority = plan ? priorityFor(plan) : 0;

  const job = await prisma.cloudJob.create({
    data: { executionId: exec.id, status: 'PENDING', priority, payloadJson: encoded },
  });
  await prisma.cloudExecution.update({
    where: { id: exec.id },
    data: { status: 'DISPATCHED', lane: 'fleet' },
  });
  return { jobId: job.id, priority };
}

// ---------------------------------------------------------------------------
// Claim (provider) — SELECT ... FOR UPDATE SKIP LOCKED, exactly like the media queue
// ---------------------------------------------------------------------------

export interface ClaimedJob {
  jobId: string;
  executionId: string;
  requestId: string;
  source: string;
  entrypoint: string;
  request: unknown;
  meta: Record<string, unknown>;
  timeoutMs: number;
  memoryMb: number;
  attempts: number;
  leaseSeconds: number;
  image: string;
}

export async function claimNextJob(provider: { id: string; capabilities: string[]; status: string; reputation: number }): Promise<ClaimedJob | null> {
  await sweepFleet();
  if (provider.status === 'SUSPENDED' || provider.status === 'DISABLED') return null;
  if (provider.reputation <= PROVIDER_REPUTATION_FLOOR) return null;
  const caps = provider.capabilities.filter((c) => (PROVIDER_CAPABILITIES as readonly string[]).includes(c));
  if (!caps.includes(REQUIRED_RUNTIME)) return null;

  const rows = await prisma.$queryRaw<Array<{ id: string; executionId: string; payloadJson: string; attempts: number }>>(Prisma.sql`
    UPDATE "CloudJob" SET
      status = 'CLAIMED',
      "providerId" = ${provider.id},
      "leaseUntil" = now() + make_interval(secs => ${limits.jobLeaseSeconds}),
      attempts = attempts + 1,
      "claimedAt" = now()
    WHERE id = (
      SELECT id FROM "CloudJob"
      WHERE status = 'PENDING'
        AND ("payloadJson"::jsonb->>'runtime') = ANY(${caps}::text[])
      ORDER BY priority DESC, "createdAt" ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING id, "executionId", "payloadJson", attempts;
  `);
  await prisma.cloudProvider
    .update({
      where: { id: provider.id },
      data: { lastSeenAt: new Date(), status: 'ACTIVE', ...(rows?.[0] ? { jobsClaimed: { increment: 1 } } : {}) },
    })
    .catch(() => {});
  const r = rows?.[0];
  if (!r) return null;

  const exec = await prisma.cloudExecution.findUnique({
    where: { id: r.executionId },
    include: {
      version: { select: { source: true, entrypoint: true, version: true } },
      function: { select: { slug: true, entrypoint: true, timeoutMs: true, memoryMb: true, owner: { select: { address: true } } } },
    },
  });
  if (!exec) {
    // Execution row vanished under the job (should be impossible — FK cascade). Kill the job.
    await prisma.cloudJob.updateMany({ where: { id: r.id }, data: { status: 'FAILED', error: 'execution missing' } });
    return null;
  }
  await prisma.cloudExecution.update({
    where: { id: exec.id },
    data: { status: 'RUNNING', startedAt: exec.startedAt ?? new Date(), providerId: provider.id },
  });

  let payload: any = {};
  try {
    payload = JSON.parse(r.payloadJson || '{}');
  } catch {
    /* tolerate */
  }

  return {
    jobId: r.id,
    executionId: exec.id,
    requestId: exec.requestId,
    source: exec.version.source,
    entrypoint: exec.version.entrypoint || exec.function.entrypoint || 'main',
    request: payload.request ?? {},
    meta: {
      request_id: exec.requestId,
      function: exec.function.slug,
      version: exec.version.version,
      owner: exec.function.owner.address,
      caller: 'account',
      deadline_ms: exec.function.timeoutMs,
    },
    timeoutMs: exec.function.timeoutMs,
    memoryMb: exec.function.memoryMb,
    attempts: r.attempts,
    leaseSeconds: limits.jobLeaseSeconds,
    image: runtime.image,
  };
}

// ---------------------------------------------------------------------------
// Heartbeat — extends the lease so a long job is never swept out from under a live provider
// ---------------------------------------------------------------------------

export async function heartbeatJob(providerId: string, jobId: string): Promise<{ ok: boolean; code?: string; leaseUntil?: Date }> {
  const leaseUntil = new Date(Date.now() + limits.jobLeaseSeconds * 1000);
  const flipped = await prisma.cloudJob.updateMany({
    where: { id: jobId, providerId, status: { in: ['CLAIMED', 'RUNNING'] } },
    data: { status: 'RUNNING', leaseUntil },
  });
  await prisma.cloudProvider.update({ where: { id: providerId }, data: { lastSeenAt: new Date(), status: 'ACTIVE' } }).catch(() => {});
  if (flipped.count !== 1) {
    const job = await prisma.cloudJob.findUnique({ where: { id: jobId }, select: { providerId: true, status: true } });
    if (!job) return { ok: false, code: 'not_found' };
    if (job.providerId !== providerId) return { ok: false, code: 'not_owner' };
    return { ok: false, code: 'not_running' };
  }
  return { ok: true, leaseUntil };
}

// ---------------------------------------------------------------------------
// Result — verify, meter, complete the execution, and PAY the provider for real
// ---------------------------------------------------------------------------

export interface FleetResultReport {
  status: 'ok' | 'error' | 'timeout';
  result?: unknown;
  error?: string;
  errorType?: string;
  stdout?: string;
  logs?: Array<{ level?: string; message?: string }>;
  wallMs: number;
  reportedCpuMs?: number;
  maxRssKb?: number;
}

export interface FleetCompletion {
  ok: true;
  terminal: 'DONE';
  executionStatus: 'SUCCEEDED' | 'FAILED' | 'TIMEOUT';
  priceNanm: bigint;
  providerNanm: bigint;
  settled: boolean;
}

export type FleetResultError = { ok: false; code: 'not_found' | 'not_owner' | 'not_running' | 'settle_failed' };

function byteLen(v: unknown): number {
  if (v == null) return 0;
  try {
    return Buffer.byteLength(JSON.stringify(v), 'utf8');
  } catch {
    return 0;
  }
}

export async function completeFleetJob(
  provider: { id: string; accountId: string | null },
  jobId: string,
  report: FleetResultReport,
): Promise<FleetCompletion | FleetResultError> {
  const job = await prisma.cloudJob.findUnique({
    where: { id: jobId },
    include: {
      execution: {
        include: { function: { select: { id: true, perCallNanm: true, timeoutMs: true, memoryMb: true } } },
      },
    },
  });
  if (!job) return { ok: false, code: 'not_found' };
  if (job.providerId !== provider.id) return { ok: false, code: 'not_owner' };
  if (job.status !== 'CLAIMED' && job.status !== 'RUNNING') return { ok: false, code: 'not_running' };
  const exec = job.execution;

  // An oversized result is a FAILURE, not a bill-me-anyway success — the caller could never
  // have received it through the API's own output cap.
  let status = report.status;
  let resultValue = report.result;
  let errorMsg = report.error;
  let errorType = report.errorType;
  if (status === 'ok' && byteLen(resultValue) > limits.maxOutputBytes) {
    status = 'error';
    resultValue = undefined;
    errorMsg = `result exceeds the ${limits.maxOutputBytes}-byte output limit`;
    errorType = 'OutputTooLarge';
  }

  // METERING. The wall time is provider-reported and providers are untrusted, so it is
  // clamped to the function's own timeout budget: pay can be under-reported by a lazy
  // provider (their loss) but never inflated past what the customer authorized.
  const billedMs = Math.max(1, Math.min(Math.trunc(report.wallMs || 0), exec.function.timeoutMs + WALL_MS_GRACE));
  const usage: Usage = {
    cpuMs: billedMs,
    memoryMbMs: BigInt(billedMs) * BigInt(exec.function.memoryMb),
    aiTokensIn: 0, // fleet jobs are pure compute — no host-brokered AI
    aiTokensOut: 0,
    egressBytes: byteLen(resultValue),
    gpuMs: 0,
  };

  const succeeded = status === 'ok';
  const execStatus: 'SUCCEEDED' | 'FAILED' | 'TIMEOUT' = succeeded ? 'SUCCEEDED' : status === 'timeout' ? 'TIMEOUT' : 'FAILED';

  // Conditional flip inside a transaction — the only writer that may complete this job.
  const flipped = await prisma.$transaction(async (tx) => {
    const flip = await tx.cloudJob.updateMany({
      where: { id: jobId, providerId: provider.id, status: { in: ['CLAIMED', 'RUNNING'] } },
      data: {
        status: 'DONE',
        finishedAt: new Date(),
        resultJson: JSON.stringify({ status, error: errorMsg ?? null }).slice(0, 4000),
        error: succeeded ? null : (errorMsg ?? status).slice(0, 1000),
      },
    });
    if (flip.count !== 1) return false;
    await tx.cloudProvider.update({
      where: { id: provider.id },
      data: {
        jobsDone: { increment: 1 },
        reputation: { increment: 1 },
        lastSeenAt: new Date(),
        status: 'ACTIVE',
      },
    });
    return true;
  });
  if (!flipped) return { ok: false, code: 'not_running' };

  await prisma.cloudExecution.update({
    where: { id: exec.id },
    data: {
      status: execStatus,
      finishedAt: new Date(),
      durationMs: billedMs,
      cpuMs: billedMs,
      memoryMbMs: usage.memoryMbMs,
      egressBytes: usage.egressBytes,
      bytesOut: byteLen(resultValue),
      errorCode: succeeded ? null : errorType || status,
      error: succeeded ? null : (errorMsg || '').slice(0, 2000),
      httpStatus: succeeded ? 200 : status === 'timeout' ? 504 : 500,
      providerId: provider.id,
    },
  });
  await persistFleetLogs(exec.id, report.logs ?? [], report.stdout ?? '');

  // ---- SETTLE: the provider is paid REAL, spendable balance -------------------------------
  const policy = await activePolicy();
  const feeBps = exec.feeBps; // snapshot taken when the execution was admitted (§88)

  // §46 failure economics, fleet edition: a failed run still consumed the provider's real
  // resources, so the caller pays metered cost with no developer surcharge and no margin
  // uplift — and the PROVIDER still receives its compute share (it faithfully ran the code;
  // the code failing is the developer's bug, not the provider's). The developer earns 0.
  const priced = succeeded ? quote(usage, policy, { surchargeNanm: exec.function.perCallNanm, feeBps, fleet: true }) : null;
  const priceNanm = succeeded ? priced!.totalNanm : priceForFailure(usage, policy);
  const effFeeBps = succeeded ? feeBps : 10_000 - policy.providerShareBps;
  const split = splitOf(priceNanm, effFeeBps, policy.providerShareBps, true);

  // COGS truth for the fleet lane: Animica's compute cost IS the provider payout (real money
  // out), not the internal per-unit rate; only the fixed per-call/egress infra cost remains.
  const infraOnly = costOf({ ...usage, cpuMs: 0, memoryMbMs: 0n }, policy);
  const cogs = {
    computeNanm: split.providerNanm,
    aiNanm: 0n,
    infraNanm: infraOnly.infraNanm,
    totalNanm: split.providerNanm + infraOnly.infraNanm,
  };

  let settled = false;
  try {
    const r = await settleExecution({
      executionId: exec.id,
      callerAccountId: exec.callerAccountId,
      developerAccountId: exec.developerAccountId,
      providerAccountId: provider.accountId,
      priceNanm,
      platformFeeNanm: split.platformFeeNanm,
      developerNanm: split.developerNanm,
      providerNanm: split.providerNanm,
      cogs,
      feeBps: split.feeBps,
      policy,
    });
    settled = r.settled;
    if (r.settled && r.providerNanm > 0n) {
      await prisma.cloudProvider
        .update({ where: { id: provider.id }, data: { earnedNanm: { increment: r.providerNanm } } })
        .catch(() => {});
    }
  } catch (e: any) {
    // The caller's funds evaporated between admission and settlement (the affordability check
    // bounds this window). Nothing moved — the settle transaction rolled back atomically.
    // Leave billed=false with the error code so the reconciler can retry, and report honestly.
    await prisma.cloudExecution
      .update({ where: { id: exec.id }, data: { errorCode: 'settlement_failed', error: String(e?.message ?? e).slice(0, 500) } })
      .catch(() => {});
    return { ok: false, code: 'settle_failed' };
  }

  return {
    ok: true,
    terminal: 'DONE',
    executionStatus: execStatus,
    priceNanm,
    providerNanm: settled ? split.providerNanm : 0n,
    settled,
  };
}

async function persistFleetLogs(executionId: string, logs: Array<{ level?: string; message?: string }>, stdout: string) {
  const rows: { executionId: string; level: string; message: string; seq: number }[] = [];
  let seq = 0;
  for (const l of logs.slice(0, limits.maxLogLines)) {
    rows.push({
      executionId,
      level: String(l.level ?? 'info').slice(0, 16),
      message: String(l.message ?? '').slice(0, limits.maxLogLineChars),
      seq: seq++,
    });
  }
  for (const line of String(stdout).split('\n').slice(0, limits.maxLogLines)) {
    if (!line.trim()) continue;
    if (seq >= limits.maxLogLines) break;
    rows.push({ executionId, level: 'stdout', message: line.slice(0, limits.maxLogLineChars), seq: seq++ });
  }
  if (rows.length) await prisma.cloudExecutionLog.createMany({ data: rows }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Failure report (provider) — requeue while attempts remain, else terminal
// ---------------------------------------------------------------------------

export async function failFleetJob(
  providerId: string,
  jobId: string,
  error: string,
): Promise<{ ok: true; terminal: 'RETRY' | 'FAILED' } | FleetResultError> {
  const job = await prisma.cloudJob.findUnique({ where: { id: jobId }, select: { id: true, executionId: true, providerId: true, status: true, attempts: true } });
  if (!job) return { ok: false, code: 'not_found' };
  if (job.providerId !== providerId) return { ok: false, code: 'not_owner' };
  if (job.status !== 'CLAIMED' && job.status !== 'RUNNING') return { ok: false, code: 'not_running' };

  // A provider that keeps failing jobs bleeds reputation; at the floor it is suspended so it
  // can no longer burn attempts other providers could use.
  const p = await prisma.cloudProvider.update({
    where: { id: providerId },
    data: { jobsFailed: { increment: 1 }, reputation: { decrement: 1 }, lastSeenAt: new Date() },
    select: { reputation: true },
  });
  if (p.reputation <= PROVIDER_REPUTATION_FLOOR) {
    await prisma.cloudProvider
      .updateMany({
        where: { id: providerId, status: { in: ['ACTIVE', 'IDLE'] } },
        data: { status: 'SUSPENDED', suspendedReason: 'reputation floor reached (repeated failures)' },
      })
      .catch(() => {});
  }

  if (job.attempts < limits.jobMaxAttempts) {
    const flipped = await prisma.cloudJob.updateMany({
      where: { id: jobId, providerId, status: { in: ['CLAIMED', 'RUNNING'] } },
      data: { status: 'PENDING', providerId: null, leaseUntil: null, claimedAt: null, error: error.slice(0, 1000) },
    });
    if (flipped.count !== 1) return { ok: false, code: 'not_running' };
    await prisma.cloudExecution.updateMany({ where: { id: job.executionId, status: 'RUNNING' }, data: { status: 'DISPATCHED' } });
    return { ok: true, terminal: 'RETRY' };
  }

  const flipped = await prisma.cloudJob.updateMany({
    where: { id: jobId, providerId, status: { in: ['CLAIMED', 'RUNNING'] } },
    data: { status: 'FAILED', error: error.slice(0, 1000), finishedAt: new Date() },
  });
  if (flipped.count !== 1) return { ok: false, code: 'not_running' };
  await closeUnservedExecution(job.executionId, 'fleet_failed', `no provider could run the job: ${error.slice(0, 300)}`);
  return { ok: true, terminal: 'FAILED' };
}

// ---------------------------------------------------------------------------
// Public network stats (§23: REAL numbers from the DB only — never invented)
// ---------------------------------------------------------------------------

export interface FleetStats {
  providersOnline: number;
  providersRegistered: number;
  cpuCoresOnline: number;
  memoryMbOnline: number;
  gpusOnline: number;
  jobsPending: number;
  jobsInFlight: number;
  jobsCompleted: number;
  jobsFailed: number;
  paidToProvidersNanm: bigint;
  providerShareBps: number;
  leaseSeconds: number;
  requiredRuntime: string;
}

export async function fleetStats(): Promise<FleetStats> {
  await sweepFleet();
  const staleCutoff = new Date(Date.now() - limits.providerStaleSeconds * 1000);
  const [online, registered, capacity, pending, inFlight, completed, failed, paid, policy] = await Promise.all([
    prisma.cloudProvider.count({ where: { status: 'ACTIVE', lastSeenAt: { gte: staleCutoff } } }),
    prisma.cloudProvider.count({ where: { status: { not: 'DISABLED' } } }),
    prisma.cloudProvider.aggregate({
      where: { status: 'ACTIVE', lastSeenAt: { gte: staleCutoff } },
      _sum: { cpuCores: true, memoryMb: true },
    }),
    prisma.cloudJob.count({ where: { status: 'PENDING' } }),
    prisma.cloudJob.count({ where: { status: { in: ['CLAIMED', 'RUNNING'] } } }),
    prisma.cloudJob.count({ where: { status: 'DONE' } }),
    prisma.cloudJob.count({ where: { status: { in: ['FAILED', 'EXPIRED'] } } }),
    // Authoritative: what was actually posted to providers through settlement, not a cache.
    prisma.cloudExecution.aggregate({
      where: { providerId: { not: null }, billed: true },
      _sum: { providerNanm: true },
    }),
    activePolicy(),
  ]);
  const gpusOnline = await prisma.cloudProvider.count({
    where: { status: 'ACTIVE', lastSeenAt: { gte: staleCutoff }, gpu: { not: null } },
  });
  return {
    providersOnline: online,
    providersRegistered: registered,
    cpuCoresOnline: capacity._sum.cpuCores ?? 0,
    memoryMbOnline: capacity._sum.memoryMb ?? 0,
    gpusOnline,
    jobsPending: pending,
    jobsInFlight: inFlight,
    jobsCompleted: completed,
    jobsFailed: failed,
    paidToProvidersNanm: paid._sum.providerNanm ?? 0n,
    providerShareBps: policy.providerShareBps,
    leaseSeconds: limits.jobLeaseSeconds,
    requiredRuntime: REQUIRED_RUNTIME,
  };
}
