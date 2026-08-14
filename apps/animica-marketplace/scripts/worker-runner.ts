import { prisma } from '../lib/db';
import { config } from '../lib/config';
import { chat } from '../lib/inference';
import { putObject, MAX_CONTENT_BYTES } from '../lib/storage';
import { nextRunAfter, safetyCaps } from '../lib/planConfig';
import {
  buildWorkerMessages,
  validateWebhookUrl,
  assertPublicWebhookTarget,
  parseTools,
  migratePlanLimitShelvedWorkers,
  WORKERS_FREE_LIMITS,
} from '../lib/workers';
import { acquireAdvisoryLock, makeLogger, parseFlags } from './store-worker-util';

// Animica Workers runner — scheduler + executor oneshot fired by animica-workers.timer
// (1 min). Workers are FREE for every account: no plan resolution, no quota consumption,
// no plan-enforcement sweep. Per tick: legacy PLAN_LIMIT fixup, then due ACTIVE interval
// workers → WorkerRun rows, then lease sweep, then claim-and-execute PENDING runs via
// FOR UPDATE SKIP LOCKED (MediaJob claim-lease pattern — conditional status flips make
// every transition happen at most once).
//
// Safety stack: per-run maxRunSeconds, per-account WORKERS_FREE_LIMITS.accountConcurrency,
// global WORKERS_GLOBAL_CONCURRENCY, per-tick WORKERS_MAX_PER_TICK, attempts cap,
// failureThreshold auto-disable, 24h stale-PENDING cancel, WorkerEngineState.paused (admin)
// and the WORKERS_ENABLED env gate (disarmed default = observe-only dry run).
//
// Ops: deploy/systemd/animica-workers.* (flock + pg advisory lock; FILES ONLY).

const WORKER = 'animica-workers';
const log = makeLogger(WORKER);

const ENABLED = process.env.WORKERS_ENABLED === '1';
const OUTPUT_MAX = 20_000; // WorkerRun.output truncation (runs are history, not storage)

interface Outcome {
  kind: 'done' | 'timeout' | 'error';
  output?: string;
  error?: string;
  tokensIn: number;
  tokensOut: number;
}

function mkRunLog() {
  const logs: Array<{ t: string; msg: string }> = [];
  return { logs, push: (msg: string) => logs.push({ t: new Date().toISOString(), msg: msg.slice(0, 500) }) };
}

// ── Worker health bookkeeping (terminal outcomes only — a retried attempt is not a run) ──

async function recordWorkerSuccess(workerId: string, now: Date): Promise<void> {
  await prisma.worker
    .update({ where: { id: workerId }, data: { lastRunAt: now, runsTotal: { increment: 1 }, consecutiveFailures: 0 } })
    .catch(() => {}); // worker may have been deleted mid-run
}

async function recordWorkerFailure(workerId: string, now: Date, reason: string): Promise<void> {
  const w = await prisma.worker.findUnique({ where: { id: workerId } });
  if (!w) return;
  const fails = w.consecutiveFailures + 1;
  const disable = fails >= w.failureThreshold;
  await prisma.worker.update({
    where: { id: workerId },
    data: {
      lastRunAt: now,
      runsTotal: { increment: 1 },
      consecutiveFailures: fails,
      ...(disable
        ? { status: 'DISABLED', statusReason: `auto-disabled: ${fails} consecutive failures`, nextRunAt: null }
        : {}),
    },
  });
  if (disable) log('warn', 'worker_auto_disabled', { workerId, consecutiveFailures: fails, reason });
}

// ── Scheduler pass: due interval workers → PENDING runs ─────────────────────

async function schedulePass(now: Date): Promise<number> {
  let enqueued = 0;
  const due = await prisma.worker.findMany({
    where: { status: 'ACTIVE', scheduleKind: 'interval', nextRunAt: { lte: now } },
    orderBy: { nextRunAt: 'asc' },
    take: 200,
  });
  for (const w of due) {
    // Conditional claim on the exact nextRunAt we read — a concurrent tick (or a user PATCH
    // re-anchoring the schedule) makes this a no-op instead of a double enqueue.
    const claim = await prisma.worker.updateMany({
      where: { id: w.id, nextRunAt: w.nextRunAt },
      data: { nextRunAt: nextRunAfter(w.intervalMinutes ?? 60) },
    });
    if (claim.count === 0) continue;

    // Executions are free — every claimed due worker enqueues at priority 0. Backpressure
    // is enforced downstream (per-account/global concurrency, 24h stale cancel), never by
    // silently skipping a schedule.
    await prisma.workerRun.create({
      data: {
        workerId: w.id,
        accountId: w.accountId,
        trigger: 'schedule',
        scheduledFor: w.nextRunAt,
        priority: 0,
      },
    });
    enqueued += 1;
  }
  return enqueued;
}

// ── Sweep pass: expired leases + fossilized queue entries ───────────────────

async function sweepPass(now: Date, caps: ReturnType<typeof safetyCaps>): Promise<number> {
  let swept = 0;
  const expired = await prisma.workerRun.findMany({
    where: { status: 'RUNNING', leaseUntil: { lt: now } },
    take: 200,
  });
  for (const r of expired) {
    if (r.attempts >= caps.maxAttempts) {
      const flip = await prisma.workerRun.updateMany({
        where: { id: r.id, status: 'RUNNING' },
        data: {
          status: 'TIMEOUT',
          finishedAt: now,
          error: 'lease expired (runner crashed or run exceeded its budget)',
          durationMs: r.startedAt ? Math.max(0, now.getTime() - r.startedAt.getTime()) : null,
        },
      });
      if (flip.count === 1) {
        await recordWorkerFailure(r.workerId, now, 'lease expired');
        swept += 1;
      }
    } else {
      // Lease loss ≠ attempt: requeue without touching the attempts counter — the next
      // claim's increment is what caps a crash-loop at maxAttempts.
      const flip = await prisma.workerRun.updateMany({
        where: { id: r.id, status: 'RUNNING' },
        data: { status: 'PENDING', leaseUntil: null, startedAt: null },
      });
      if (flip.count === 1) swept += 1;
    }
  }
  // A run PENDING for 24h is starved beyond usefulness (concurrency or a paused engine) —
  // cancel rather than surprise-execute a day-old prompt. Executions are unmetered, so
  // there is no quota to hand back; one bulk conditional flip is enough.
  const stale = await prisma.workerRun.updateMany({
    where: { status: 'PENDING', createdAt: { lt: new Date(now.getTime() - 24 * 3600_000) } },
    data: { status: 'CANCELLED', finishedAt: now, error: 'cancelled: queued for more than 24h' },
  });
  swept += stale.count;
  return swept;
}

// ── Execution (one bounded LLM task + configured tools) ─────────────────────

async function deliverWebhook(
  worker: { id: string; name: string; webhookUrl: string | null },
  run: { id: string; trigger: string },
  output: string,
  push: (msg: string) => void,
): Promise<void> {
  // Re-validate at delivery time — the SSRF gate must hold for URLs saved before a rule
  // tightened, not only at config time. The resolver check is delivery-time ONLY: an
  // ordinary hostname that points at 127.0.0.1 / 169.254.169.254 is invisible to the
  // string checks, and DNS answers change after the worker was configured.
  const url = validateWebhookUrl(worker.webhookUrl ?? '');
  await assertPublicWebhookTarget(url);
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 10_000);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ worker: { id: worker.id, name: worker.name }, run: { id: run.id, trigger: run.trigger }, output }),
      signal: ctrl.signal,
      redirect: 'manual', // never follow a redirect into the private network
    });
    push(`webhook ${res.status}`);
    if (res.status < 200 || res.status >= 300) throw new Error(`webhook delivery failed: HTTP ${res.status}`);
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      push('webhook timed out after 10s');
      throw new Error('webhook delivery timed out');
    }
    throw e instanceof Error ? e : new Error(String(e));
  } finally {
    clearTimeout(t);
  }
}

async function publishToAnm(
  worker: { accountId: string; anmName: string | null },
  output: string,
  push: (msg: string) => void,
): Promise<void> {
  // Ownership + status re-checked every run (mirrors the names/[name]/publish route): the
  // domain may have been transferred or suspended since the worker was configured.
  const domain = worker.anmName ? await prisma.anmDomain.findUnique({ where: { name: worker.anmName } }) : null;
  if (!domain || domain.ownerId !== worker.accountId || domain.status !== 'ACTIVE') {
    throw new Error(`anm_publish: ${worker.anmName ?? '(unset)'}.anm is not an ACTIVE domain owned by this account`);
  }
  // Non-HTML output is a content miss, not an infrastructure failure — skip, don't fail.
  if (!/<html/i.test(output)) {
    push('anm_publish skipped: output is not an HTML document');
    return;
  }
  const bytes = Buffer.from(output, 'utf8');
  if (bytes.length > MAX_CONTENT_BYTES) {
    push(`anm_publish skipped: ${bytes.length}B exceeds the ${MAX_CONTENT_BYTES}B cap`);
    return;
  }
  const { cid, size } = await putObject(bytes, 'text/html; charset=utf-8', worker.accountId);
  const providerId = config.nodeId || 'animica.dev';
  const providers = Array.from(new Set([...(domain.nodeProviders ?? []), providerId])).slice(0, 32);
  await prisma.anmDomain.update({ where: { id: domain.id }, data: { contentCid: cid, nodeProviders: providers } });
  await prisma.domainEvent.create({
    data: { domainId: domain.id, kind: 'update', detail: `worker publish ${cid.slice(0, 12)}… (${size}B)` },
  });
  push(`anm_publish ${worker.anmName}.anm ← ${cid.slice(0, 12)}… (${size}B)`);
}

async function executeRun(
  run: { id: string; trigger: string; attempts: number },
  worker: any,
  push: (msg: string) => void,
): Promise<Outcome> {
  push(`start (trigger=${run.trigger}, attempt=${run.attempts}, model=${worker.model})`);
  let result;
  try {
    // Free bridge path (paid:false) — a Worker run is one bounded LLM task; the
    // subscription sells automation, not tokens ("AI is free. Put it to work.").
    result = await chat(buildWorkerMessages(worker), {
      model: worker.model,
      temperature: worker.temperature,
      maxTokens: 4096,
      paid: false,
      timeoutMs: worker.maxRunSeconds * 1000,
    });
  } catch (e: any) {
    if (e?.name === 'AbortError' || /abort/i.test(String(e?.message ?? ''))) {
      push(`timed out after ${worker.maxRunSeconds}s`);
      return { kind: 'timeout', error: `timed out after ${worker.maxRunSeconds}s`, tokensIn: 0, tokensOut: 0 };
    }
    push(`inference error: ${String(e?.message ?? e).slice(0, 300)}`);
    return { kind: 'error', error: `inference: ${String(e?.message ?? e)}`, tokensIn: 0, tokensOut: 0 };
  }
  push(`inference ok (${result.tokensIn} in / ${result.tokensOut} out)`);
  const output = result.text ?? '';
  const tools = parseTools(worker.toolsJson);
  try {
    if (tools.includes('webhook')) await deliverWebhook(worker, run, output, push);
    if (tools.includes('anm_publish')) await publishToAnm(worker, output, push);
  } catch (e: any) {
    // Tool failure = run failure (the whole point of the run was the delivery) — but the
    // model output is preserved on the run row for debugging.
    push(`tool error: ${String(e?.message ?? e).slice(0, 300)}`);
    return { kind: 'error', error: String(e?.message ?? e), output, tokensIn: result.tokensIn, tokensOut: result.tokensOut };
  }
  return { kind: 'done', output, tokensIn: result.tokensIn, tokensOut: result.tokensOut };
}

// ── Executor pass: claim → gate → execute → record (conditional flips only) ──

async function executorPass(caps: ReturnType<typeof safetyCaps>): Promise<{ executed: number; failed: number }> {
  let executed = 0;
  let failed = 0;
  const requeuedIds = new Set<string>(); // concurrency-requeued runs; re-claiming one = spin, stop the tick

  for (let i = 0; i < caps.maxPerTick; i++) {
    const running = await prisma.workerRun.count({ where: { status: 'RUNNING' } });
    if (running >= caps.globalConcurrency) break;

    // Claim exactly one PENDING run, FIFO (priority is 0 on every new row; the DESC sort
    // only still matters for pre-migration PENDING rows and keeps the index usable).
    // SKIP LOCKED keeps concurrent runners (belt over the flock+advisory-lock braces) from
    // colliding; the fixed 600s lease covers us until the worker row (and its real budget)
    // is loaded.
    const claimed = await prisma.$queryRaw<Array<{ id: string; workerId: string }>>`
      UPDATE "WorkerRun"
      SET status = 'RUNNING', "startedAt" = now(), "leaseUntil" = now() + interval '600 seconds', attempts = attempts + 1
      WHERE id = (
        SELECT id FROM "WorkerRun"
        WHERE status = 'PENDING'
        ORDER BY priority DESC, "createdAt" ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
      )
      RETURNING id, "workerId"`;
    if (claimed.length === 0) break;
    const { id: runId, workerId } = claimed[0];
    const run = await prisma.workerRun.findUnique({ where: { id: runId } });
    if (!run) continue;
    const worker = await prisma.worker.findUnique({ where: { id: workerId } });
    if (!worker) {
      await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: { status: 'CANCELLED', finishedAt: new Date(), error: 'cancelled: worker deleted' },
      });
      continue;
    }

    // Status gate: schedule/external runs need ACTIVE; a manual run may execute on a
    // PAUSED worker (that's how you test one) but never on a DISABLED one.
    const executable = worker.status === 'ACTIVE' || (run.trigger === 'manual' && worker.status === 'PAUSED');
    if (!executable) {
      await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: { status: 'CANCELLED', finishedAt: new Date(), error: `cancelled: worker ${worker.status.toLowerCase()}` },
      });
      continue;
    }

    // Per-account concurrency (count includes this freshly-claimed run) — the flat free
    // constant, NOT a plan lookup (free-plan worker_max_concurrency was 0, which would
    // requeue every claim forever). Over → hand the run back untouched: attempts-1 because
    // this claim never became an attempt.
    const accountRunning = await prisma.workerRun.count({ where: { accountId: run.accountId, status: 'RUNNING' } });
    if (accountRunning > WORKERS_FREE_LIMITS.accountConcurrency) {
      await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: { status: 'PENDING', leaseUntil: null, startedAt: null, attempts: { decrement: 1 } },
      });
      if (requeuedIds.has(runId)) break; // it would be re-claimed immediately — yield to the next tick
      requeuedIds.add(runId);
      continue;
    }

    // Tighten the lease to the worker's real budget (+60s recording overhead).
    await prisma.workerRun
      .update({ where: { id: runId }, data: { leaseUntil: new Date(Date.now() + (worker.maxRunSeconds + 60) * 1000) } })
      .catch(() => {});

    const startedMs = Date.now();
    const { logs, push } = mkRunLog();
    const outcome = await executeRun(run, worker, push);
    const now = new Date();
    const durationMs = Date.now() - startedMs;
    const logsJson = () => JSON.stringify(logs);

    if (outcome.kind === 'done') {
      // Conditional flip (MediaJob pattern): if the sweep already timed this run out, the
      // count is 0 and we record nothing twice.
      const flip = await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: {
          status: 'DONE',
          finishedAt: now,
          durationMs,
          output: (outcome.output ?? '').slice(0, OUTPUT_MAX),
          tokensIn: outcome.tokensIn,
          tokensOut: outcome.tokensOut,
          logsJson: logsJson(),
        },
      });
      if (flip.count === 1) {
        executed += 1;
        await recordWorkerSuccess(worker.id, now);
      }
    } else if (outcome.kind === 'timeout') {
      const flip = await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: { status: 'TIMEOUT', finishedAt: now, durationMs, error: (outcome.error ?? 'timeout').slice(0, 2000), logsJson: logsJson() },
      });
      if (flip.count === 1) {
        failed += 1;
        await recordWorkerFailure(worker.id, now, outcome.error ?? 'timeout');
      }
    } else if (run.attempts >= caps.maxAttempts) {
      // run.attempts already includes this claim's increment — this was the last attempt.
      const flip = await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: {
          status: 'FAILED',
          finishedAt: now,
          durationMs,
          error: (outcome.error ?? 'failed').slice(0, 2000),
          output: outcome.output ? outcome.output.slice(0, OUTPUT_MAX) : undefined,
          tokensIn: outcome.tokensIn,
          tokensOut: outcome.tokensOut,
          logsJson: logsJson(),
        },
      });
      if (flip.count === 1) {
        failed += 1;
        await recordWorkerFailure(worker.id, now, outcome.error ?? 'failed');
      }
    } else {
      // Retryable failure with attempts left: back to the queue (error kept for visibility).
      push(`attempt ${run.attempts}/${caps.maxAttempts} failed — requeued`);
      await prisma.workerRun.updateMany({
        where: { id: runId, status: 'RUNNING' },
        data: { status: 'PENDING', leaseUntil: null, startedAt: null, error: (outcome.error ?? 'failed').slice(0, 2000), logsJson: logsJson() },
      });
    }
  }
  return { executed, failed };
}

// ── Main tick ────────────────────────────────────────────────────────────────

async function main() {
  const { dryRun } = parseFlags();
  const caps = safetyCaps();
  const now = new Date();

  // Disarmed (default) or --dry-run: observe-only — report what a live tick would do.
  if (!ENABLED || dryRun) {
    const [dueWorkers, pendingRuns] = await Promise.all([
      prisma.worker.count({ where: { status: 'ACTIVE', scheduleKind: 'interval', nextRunAt: { lte: now } } }),
      prisma.workerRun.count({ where: { status: 'PENDING' } }),
    ]);
    log('info', 'disarmed_observe_only', {
      enabled: ENABLED,
      dryRun,
      detail: `would process ${dueWorkers} due workers / ${pendingRuns} pending runs`,
    });
    return;
  }

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }

  // Admin kill switch — heartbeat still recorded so /admin/billing can tell "paused" from "dead".
  const engine = await prisma.workerEngineState.upsert({ where: { id: 'workers' }, create: { id: 'workers' }, update: {} });
  if (engine.paused) {
    await prisma.workerEngineState.update({ where: { id: 'workers' }, data: { lastTickAt: now } });
    log('info', 'engine_paused', { note: engine.note ?? null });
    return;
  }

  // Legacy fixup: plan-era shelved workers become PAUSED (idempotent updateMany, ~free
  // when there are none). Rides every armed tick so a row restored from a backup can never
  // resurrect the retired PLAN_LIMIT state.
  try {
    const migrated = await migratePlanLimitShelvedWorkers();
    if (migrated) log('info', 'plan_limit_workers_unshelved', { migrated });
  } catch (e: any) {
    log('error', 'plan_limit_migration_error', { error: String(e?.message ?? e) });
  }

  const enqueued = await schedulePass(new Date());
  const swept = await sweepPass(new Date(), caps);
  const { executed, failed } = await executorPass(caps);

  await prisma.workerEngineState
    .update({
      where: { id: 'workers' },
      data: { lastTickAt: new Date(), lastTickInfo: JSON.stringify({ enqueued, executed, failed, swept }) },
    })
    .catch(() => {});
  log('info', 'run_done', { enqueued, executed, failed, swept });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
