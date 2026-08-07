import { prisma } from '../lib/db';
import { limits } from '../lib/cloud/config';
import { resolvePlan, logRetentionDays } from '../lib/cloud/entitlements';
import { reapOrphans } from '../lib/cloud/sandbox';
import { acquireAdvisoryLock, makeLogger, parseFlags } from './store-worker-util';

// Animica Python Cloud janitor — the hygiene sweeps that keep the platform truthful over time
// (§30 log retention, §35 lease hygiene). Oneshot run by animica-cloud-janitor.timer (5 min),
// same belt-and-braces as the other workers: flock in ExecStart + pg advisory lock here.
//
// Six passes, each independent (one failing never blocks the rest):
//   a. CloudExecutionLog rows past the OWNER plan's retention are deleted. Retention is a
//      per-developer plan entitlement (log_retention_days), so the cutoff is resolved per
//      developer — never a single global number.
//   b. Orphaned sandbox containers are reaped by label (in-memory tracking dies with the app
//      process; the studio-host "Up 6 weeks" incident is exactly what this prevents).
//   c. CloudJob leases: expired CLAIMED/RUNNING leases requeue to PENDING with attempts left,
//      or go EXPIRED past jobMaxAttempts — and an EXPIRED job FAILS its execution so the row
//      cannot pin concurrency/affordability accounting forever.
//   d. CloudProvider rows that stopped heartbeating go IDLE (dispatch skips them).
//   e. CloudExecution watchdog: RUNNING past the hard deadline becomes TIMEOUT (a crashed app
//      process leaves RUNNING rows nothing else will ever close); ancient QUEUED/DISPATCHED
//      rows are CANCELLED so their quotedNanm reservation stops starving checkAffordable().
//   f. CloudGrant rows past expiresAt get revokedAt stamped. Expiry is already enforced
//      fail-closed at every use — this just makes the state durable and visible in the UI.
//
// Ops: deploy/systemd/animica-cloud-janitor.{service,timer} (FILES ONLY — integrator installs).

const WORKER = 'animica-cloud-janitor';
const log = makeLogger(WORKER);

const ENABLED = process.env.CLOUD_JANITOR_ENABLED === '1';

// Hard watchdog for RUNNING executions: the sandbox SIGKILLs at timeoutMs+5s, so anything
// still RUNNING this long after start belongs to a process that died mid-execution.
const RUNNING_HARD_MS = limits.maxTimeoutMs + 10 * 60_000;
// A QUEUED/DISPATCHED row this old will never run (its caller gave up long ago); closing it
// releases the quotedNanm reservation counted by checkAffordable().
const QUEUE_STALE_MS = 24 * 3600_000;
// Containers may legitimately live maxTimeoutMs; only older ones are orphans.
const ORPHAN_AGE_S = Math.max(900, Math.ceil(limits.maxTimeoutMs / 1000) + 120);

interface Stats {
  logsDeleted: number;
  logDevelopers: number;
  orphansKilled: number;
  jobsRequeued: number;
  jobsExpired: number;
  providersIdled: number;
  execsTimedOut: number;
  execsCancelled: number;
  grantsExpired: number;
}

// ── (a) log retention ────────────────────────────────────────────────────────

async function sweepLogs(now: Date, dryRun: boolean, stats: Stats): Promise<void> {
  // Enumerate only developers who actually HAVE logs older than a day — the per-developer
  // retention (>= 1 via plan overrides, 3 on Free by default) then decides what to delete.
  // The join runs on the DB so we never page log rows through the app.
  const oneDayAgo = new Date(now.getTime() - 86_400_000);
  const devs = await prisma.$queryRaw<Array<{ developerAccountId: string }>>`
    SELECT DISTINCT e."developerAccountId"
    FROM "CloudExecutionLog" l
    JOIN "CloudExecution" e ON e.id = l."executionId"
    WHERE l."ts" < ${oneDayAgo}`;
  for (const { developerAccountId } of devs) {
    try {
      const plan = await resolvePlan(developerAccountId);
      const cutoff = new Date(now.getTime() - logRetentionDays(plan) * 86_400_000);
      const where = { ts: { lt: cutoff }, execution: { developerAccountId } } as const;
      if (dryRun) {
        const n = await prisma.cloudExecutionLog.count({ where });
        if (n > 0) {
          stats.logsDeleted += n;
          stats.logDevelopers += 1;
        }
        continue;
      }
      const res = await prisma.cloudExecutionLog.deleteMany({ where });
      if (res.count > 0) {
        stats.logsDeleted += res.count;
        stats.logDevelopers += 1;
      }
    } catch (e: any) {
      log('error', 'log_sweep_error', { developerAccountId, error: String(e?.message ?? e) });
    }
  }
}

// ── (c) job leases ───────────────────────────────────────────────────────────

async function sweepJobLeases(now: Date, dryRun: boolean, stats: Stats): Promise<void> {
  const stale = await prisma.cloudJob.findMany({
    where: { status: { in: ['CLAIMED', 'RUNNING'] }, leaseUntil: { lt: now } },
    take: 200,
    select: { id: true, executionId: true, status: true, attempts: true },
  });
  if (dryRun) {
    for (const j of stale) {
      if (j.attempts >= limits.jobMaxAttempts) stats.jobsExpired += 1;
      else stats.jobsRequeued += 1;
    }
    return;
  }
  for (const j of stale) {
    if (j.attempts >= limits.jobMaxAttempts) {
      // Conditional flip (the MediaJob pattern): if a late provider result already landed,
      // count is 0 and nothing is recorded twice.
      const flip = await prisma.cloudJob.updateMany({
        where: { id: j.id, status: j.status },
        data: { status: 'EXPIRED', finishedAt: now, error: `lease expired after ${j.attempts} attempts` },
      });
      if (flip.count === 1) {
        stats.jobsExpired += 1;
        // The execution behind an EXPIRED job is dead work — close it so it stops counting
        // against concurrency and in-flight affordability. billed stays false: settlement
        // never ran, so nobody was charged for work that never completed.
        await prisma.cloudExecution.updateMany({
          where: { id: j.executionId, status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
          data: {
            status: 'FAILED',
            errorCode: 'lease_expired',
            error: `compute provider lease expired after ${j.attempts} attempts`,
            finishedAt: now,
          },
        });
      }
    } else {
      // Lease loss ≠ attempt (worker-runner discipline): requeue without touching attempts;
      // the next claim's increment is what caps a crash-loop at jobMaxAttempts.
      const flip = await prisma.cloudJob.updateMany({
        where: { id: j.id, status: j.status },
        data: { status: 'PENDING', leaseUntil: null, providerId: null, claimedAt: null },
      });
      if (flip.count === 1) stats.jobsRequeued += 1;
    }
  }
}

// ── (e) execution watchdog ───────────────────────────────────────────────────

async function sweepExecutions(now: Date, dryRun: boolean, stats: Stats): Promise<void> {
  const hardCutoff = new Date(now.getTime() - RUNNING_HARD_MS);
  const stuck = await prisma.cloudExecution.findMany({
    where: {
      status: 'RUNNING',
      OR: [{ startedAt: { lt: hardCutoff } }, { startedAt: null, createdAt: { lt: hardCutoff } }],
    },
    take: 200,
    select: { id: true, startedAt: true },
  });
  if (dryRun) {
    stats.execsTimedOut += stuck.length;
  } else {
    for (const e of stuck) {
      const flip = await prisma.cloudExecution.updateMany({
        where: { id: e.id, status: 'RUNNING' },
        data: {
          status: 'TIMEOUT',
          errorCode: 'watchdog',
          error: `closed by janitor: RUNNING longer than the ${Math.round(RUNNING_HARD_MS / 1000)}s hard deadline (executor process likely died)`,
          finishedAt: now,
          durationMs: e.startedAt ? Math.max(0, now.getTime() - e.startedAt.getTime()) : 0,
        },
      });
      if (flip.count === 1) stats.execsTimedOut += 1;
    }
  }

  // Fossilized queue entries. DISPATCHED rows with a live job were handled by the lease sweep;
  // this catches rows whose job vanished or that never got one.
  const staleQueue = { status: { in: ['QUEUED', 'DISPATCHED'] as any }, createdAt: { lt: new Date(now.getTime() - QUEUE_STALE_MS) } };
  if (dryRun) {
    stats.execsCancelled += await prisma.cloudExecution.count({ where: staleQueue });
  } else {
    const res = await prisma.cloudExecution.updateMany({
      where: staleQueue,
      data: { status: 'CANCELLED', errorCode: 'stale_queue', error: 'cancelled: queued for more than 24h', finishedAt: now },
    });
    stats.execsCancelled += res.count;
  }
}

// ── Main tick ────────────────────────────────────────────────────────────────

async function main() {
  const { dryRun } = parseFlags();
  const now = new Date();
  const stats: Stats = {
    logsDeleted: 0,
    logDevelopers: 0,
    orphansKilled: 0,
    jobsRequeued: 0,
    jobsExpired: 0,
    providersIdled: 0,
    execsTimedOut: 0,
    execsCancelled: 0,
    grantsExpired: 0,
  };

  // Disarmed or --dry-run: full observe pass — every count below is what a live run would do,
  // computed with the same queries, zero writes and zero container kills.
  const observeOnly = !ENABLED || dryRun;

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }

  // (a) log retention
  await sweepLogs(now, observeOnly, stats);

  // (b) orphaned containers. Skipped entirely in observe mode — reapOrphans() kills as it
  // lists, and a "dry run" that SIGKILLs containers would be a lie.
  if (!observeOnly) {
    try {
      stats.orphansKilled = await reapOrphans(ORPHAN_AGE_S);
    } catch (e: any) {
      log('error', 'reap_orphans_error', { error: String(e?.message ?? e) });
    }
  }

  // (c) job leases
  await sweepJobLeases(now, observeOnly, stats);

  // (d) stale providers -> IDLE. ACTIVE only: SUSPENDED/DISABLED are admin decisions this
  // worker must never overwrite. Providers flip themselves back to ACTIVE on heartbeat.
  {
    const where = { status: 'ACTIVE' as any, lastSeenAt: { lt: new Date(now.getTime() - limits.providerStaleSeconds * 1000) } };
    if (observeOnly) {
      stats.providersIdled = await prisma.cloudProvider.count({ where });
    } else {
      stats.providersIdled = (await prisma.cloudProvider.updateMany({ where, data: { status: 'IDLE' } })).count;
    }
  }

  // (e) execution watchdog
  await sweepExecutions(now, observeOnly, stats);

  // (f) expired grants
  {
    const where = { revokedAt: null, expiresAt: { lt: now } };
    if (observeOnly) {
      stats.grantsExpired = await prisma.cloudGrant.count({ where });
    } else {
      stats.grantsExpired = (await prisma.cloudGrant.updateMany({ where, data: { revokedAt: now } })).count;
    }
  }

  log('info', observeOnly ? 'observe_only_done' : 'run_done', { enabled: ENABLED, dryRun, ...stats });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
