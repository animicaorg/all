import { prisma } from '../lib/db';
import { ApiError } from '../lib/api';
import { flags, limits } from '../lib/cloud/config';
import { resolvePlan, scheduleFloorMinutes, type ResolvedPlan } from '../lib/cloud/entitlements';
import { invokeFunction } from '../lib/cloud/executor';
import { acquireAdvisoryLock, makeLogger, parseFlags } from './store-worker-util';

// Animica Python Cloud scheduler — fires due CloudSchedule rows (§29). Oneshot run by
// animica-cloud-scheduler.timer (1 min), following the proven worker-runner pattern:
// flock in ExecStart (belt), pg advisory lock (braces), and a conditional-updateMany claim
// on the EXACT nextRunAt we read so a concurrent tick — or a user PATCH re-anchoring the
// schedule — turns into a no-op instead of a double fire.
//
// Money/safety model: a scheduled run is an ordinary invokeFunction() with callerKind
// "schedule" and the schedule OWNER as the paying account. That routes it through the whole
// admission stack the executor already enforces (plan resolution, per-account concurrency,
// quota consumption, affordability, settlement) — the scheduler adds nothing money-shaped of
// its own. What it DOES own: the plan's schedule-frequency floor, auto-disable after
// consecutive failures, and a global cap so a burst of due schedules cannot monopolize the
// sandbox capacity the mainnet node shares this box with.
//
// Ops: deploy/systemd/animica-cloud-scheduler.{service,timer} (FILES ONLY — integrator installs).

const WORKER = 'animica-cloud-scheduler';
const log = makeLogger(WORKER);

const ENABLED = process.env.CLOUD_SCHEDULER_ENABLED === '1';
// Pool width for concurrent invocations THIS tick. Kept small: each invoke holds a sandbox
// slot for up to fn.timeoutMs, and the sandbox semaphore (limits.globalConcurrency) is shared
// with interactive traffic that should win.
const POOL = clampInt(Number(process.env.CLOUD_SCHEDULER_CONCURRENCY ?? '3'), 1, limits.globalConcurrency);
// Global cap on schedule-lane executions in flight (DB-wide, so it also counts fleet-lane and
// crashed-tick leftovers, not just this process).
const INFLIGHT_CAP = clampInt(Number(process.env.CLOUD_SCHEDULER_INFLIGHT_CAP ?? String(limits.globalConcurrency)), 1, 64);
const MAX_PER_TICK = clampInt(Number(process.env.CLOUD_SCHEDULER_MAX_PER_TICK ?? '50'), 1, 500);
// Consecutive-failure threshold before a schedule is auto-disabled (CloudSchedule has no
// per-row threshold field, so this is a global policy knob).
const MAX_FAILURES = clampInt(Number(process.env.CLOUD_SCHEDULE_MAX_FAILURES ?? '5'), 1, 100);
// Stop STARTING new invocations after this much tick time. Claims happen lazily (inside the
// pool, right before the invoke), so hitting the budget defers unclaimed schedules to the next
// tick instead of stranding them with an advanced nextRunAt and no run.
const TICK_BUDGET_MS = clampInt(Number(process.env.CLOUD_SCHEDULER_TICK_BUDGET_MS ?? '600000'), 30_000, 3_600_000);

// Admission-side ApiError codes that mean "the owner cannot run this RIGHT NOW" (quota,
// funds, concurrency, capacity, billing hold) rather than "the function is broken". These do
// not consume the failure budget — auto-disable exists for broken targets, not tight wallets.
const SOFT_CODES = new Set(['quota_exceeded', 'insufficient_funds', 'concurrency_limit', 'busy', 'billing_past_due', 'plan_limit']);

function clampInt(v: number, lo: number, hi: number): number {
  const n = Number.isFinite(v) ? Math.trunc(v) : lo;
  return Math.min(hi, Math.max(lo, n));
}

// ── 5-field cron (UTC) ───────────────────────────────────────────────────────
//
// Deliberately dependency-free and strict: minute hour day-of-month month day-of-week, with
// `*`, values, ranges, steps and lists. Anything else (names, seconds fields, @macros) is
// REJECTED and the schedule is disabled with a reason — fail closed beats guessing. Vixie
// semantics for the dom/dow pair: when BOTH are restricted, a day matches if EITHER does.

interface CronField {
  any: boolean;
  set: Set<number>;
}

export interface CronSpec {
  minute: CronField;
  hour: CronField;
  dom: CronField;
  month: CronField;
  dow: CronField;
  domRestricted: boolean;
  dowRestricted: boolean;
}

function parseCronField(text: string, min: number, max: number): CronField | null {
  if (text === '*') return { any: true, set: new Set() };
  const set = new Set<number>();
  for (const part of text.split(',')) {
    const m = /^(\*|\d{1,2})(?:-(\d{1,2}))?(?:\/(\d{1,2}))?$/.exec(part);
    if (!m) return null;
    const [, a, b, step] = m;
    let lo: number;
    let hi: number;
    if (a === '*') {
      if (b !== undefined) return null; // "*-5" is nonsense
      lo = min;
      hi = max;
    } else {
      lo = Number(a);
      // "a/step" with no range means "a through max, stepping" (standard cron).
      hi = b !== undefined ? Number(b) : step !== undefined ? max : Number(a);
    }
    const st = step !== undefined ? Number(step) : 1;
    if (st < 1 || lo < min || hi > max || lo > hi) return null;
    for (let v = lo; v <= hi; v += st) set.add(v);
  }
  return set.size > 0 ? { any: false, set } : null;
}

export function parseCron(expr: string): CronSpec | null {
  if (typeof expr !== 'string' || expr.length === 0 || expr.length > 100) return null;
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const minute = parseCronField(fields[0], 0, 59);
  const hour = parseCronField(fields[1], 0, 23);
  const dom = parseCronField(fields[2], 1, 31);
  const month = parseCronField(fields[3], 1, 12);
  const dowRaw = parseCronField(fields[4], 0, 7);
  if (!minute || !hour || !dom || !month || !dowRaw) return null;
  // 7 is Sunday too; collapse onto 0 so getUTCDay() comparisons are direct.
  const dow: CronField = dowRaw.any
    ? dowRaw
    : { any: false, set: new Set([...dowRaw.set].map((d) => (d === 7 ? 0 : d))) };
  return { minute, hour, dom, month, dow, domRestricted: !dom.any, dowRestricted: !dow.any };
}

/** Next matching instant strictly after `from`, in UTC. Null if nothing matches within a full
 *  leap cycle (e.g. "0 0 30 2 *") — the caller disables such schedules rather than spinning
 *  forever. The horizon is 4 years + a margin so "0 0 29 2 *" (leap day) stays schedulable. */
export function nextCronAfter(spec: CronSpec, from: Date): Date | null {
  const t = new Date(from.getTime());
  t.setUTCSeconds(0, 0);
  t.setUTCMinutes(t.getUTCMinutes() + 1);
  const horizon = from.getTime() + (4 * 366 + 2) * 86_400_000;
  // Field-at-a-time jumps keep this O(days), never a minute-by-minute crawl across a year.
  while (t.getTime() <= horizon) {
    if (!spec.month.any && !spec.month.set.has(t.getUTCMonth() + 1)) {
      t.setUTCMonth(t.getUTCMonth() + 1, 1);
      t.setUTCHours(0, 0, 0, 0);
      continue;
    }
    const domMatch = spec.dom.any || spec.dom.set.has(t.getUTCDate());
    const dowMatch = spec.dow.any || spec.dow.set.has(t.getUTCDay());
    const dayOk = spec.domRestricted && spec.dowRestricted ? domMatch || dowMatch : domMatch && dowMatch;
    if (!dayOk) {
      t.setUTCDate(t.getUTCDate() + 1);
      t.setUTCHours(0, 0, 0, 0);
      continue;
    }
    if (!spec.hour.any && !spec.hour.set.has(t.getUTCHours())) {
      t.setUTCHours(t.getUTCHours() + 1, 0, 0, 0);
      continue;
    }
    if (!spec.minute.any && !spec.minute.set.has(t.getUTCMinutes())) {
      t.setUTCMinutes(t.getUTCMinutes() + 1, 0, 0);
      continue;
    }
    return new Date(t.getTime());
  }
  return null;
}

// ── Per-schedule bookkeeping (terminal outcomes only) ────────────────────────

async function recordSuccess(scheduleId: string, now: Date): Promise<void> {
  await prisma.cloudSchedule
    .update({
      where: { id: scheduleId },
      data: { lastRunAt: now, lastStatus: 'succeeded', runsTotal: { increment: 1 }, failures: 0 },
    })
    .catch(() => {}); // schedule may have been deleted mid-run (function cascade)
}

async function recordFailure(scheduleId: string, now: Date, code: string, message: string): Promise<void> {
  const s = await prisma.cloudSchedule.findUnique({ where: { id: scheduleId } });
  if (!s) return;
  const fails = s.failures + 1;
  const disable = fails >= MAX_FAILURES;
  await prisma.cloudSchedule
    .update({
      where: { id: scheduleId },
      data: {
        lastRunAt: now,
        lastStatus: code.slice(0, 100),
        runsTotal: { increment: 1 },
        failures: fails,
        ...(disable
          ? { enabled: false, nextRunAt: null, disabledReason: `auto-disabled: ${fails} consecutive failures (last: ${code})` }
          : {}),
      },
    })
    .catch(() => {});
  if (disable) log('warn', 'schedule_auto_disabled', { scheduleId, consecutiveFailures: fails, code, message: message.slice(0, 300) });
}

/** Soft skip: the function never ran, so no lastRunAt/runsTotal — only the visible reason. */
async function recordSkip(scheduleId: string, code: string): Promise<void> {
  await prisma.cloudSchedule
    .update({ where: { id: scheduleId }, data: { lastStatus: `skipped:${code}`.slice(0, 100) } })
    .catch(() => {});
}

async function disableWithReason(s: { id: string; nextRunAt: Date | null }, reason: string): Promise<boolean> {
  // Same conditional-claim discipline as firing: only the tick that "owns" this nextRunAt may
  // disable, so a user fixing the cron concurrently is not clobbered.
  const flip = await prisma.cloudSchedule.updateMany({
    where: { id: s.id, nextRunAt: s.nextRunAt, enabled: true },
    data: { enabled: false, nextRunAt: null, disabledReason: reason, lastStatus: 'disabled' },
  });
  return flip.count === 1;
}

// ── Fire one schedule ────────────────────────────────────────────────────────

type FireOutcome = 'fired' | 'failed' | 'skipped' | 'disabled' | 'lost_claim' | 'deferred';

interface DueSchedule {
  id: string;
  functionId: string;
  ownerId: string;
  kind: string;
  intervalMinutes: number | null;
  cron: string | null;
  payloadJson: string;
  nextRunAt: Date | null;
}

async function fireOne(s: DueSchedule, planCache: Map<string, ResolvedPlan>, now: Date, startedMs: number): Promise<FireOutcome> {
  if (Date.now() - startedMs > TICK_BUDGET_MS) return 'deferred'; // unclaimed => still due next tick

  // Global in-flight cap, checked against the DB (not process memory) so fleet-lane and
  // crashed-tick executions count too. Deferring leaves nextRunAt untouched.
  const inFlight = await prisma.cloudExecution.count({
    where: { callerKind: 'schedule', status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
  });
  if (inFlight >= INFLIGHT_CAP) return 'deferred';

  let plan = planCache.get(s.ownerId);
  if (!plan) {
    plan = await resolvePlan(s.ownerId);
    planCache.set(s.ownerId, plan);
  }
  const floorMinutes = scheduleFloorMinutes(plan);

  // Compute the NEXT occurrence before claiming — the claim writes it atomically.
  let next: Date;
  if (s.kind === 'cron') {
    const spec = parseCron(s.cron ?? '');
    if (!spec) {
      return (await disableWithReason(s, 'invalid cron expression (5 numeric fields: min hour dom mon dow)')) ? 'disabled' : 'lost_claim';
    }
    const n = nextCronAfter(spec, now);
    if (!n) {
      return (await disableWithReason(s, 'cron expression never matches a real date')) ? 'disabled' : 'lost_claim';
    }
    next = n;
  } else {
    // Interval anchored on NOW, not the missed slot — a backlog must never stampede-backfill.
    const mins = Math.max(s.intervalMinutes ?? 60, floorMinutes);
    next = new Date(now.getTime() + mins * 60_000);
  }
  // Plan floor applies to BOTH kinds: no schedule may fire more often than the plan allows,
  // whatever the cron says ("* * * * *" on Free effectively becomes hourly).
  const floorAt = new Date(now.getTime() + floorMinutes * 60_000);
  if (next < floorAt) next = floorAt;

  // The double-enqueue guard: claim on the exact nextRunAt we read. count 0 => another tick
  // (or a user PATCH) already moved it — this fire never happens twice.
  const claim = await prisma.cloudSchedule.updateMany({
    where: { id: s.id, nextRunAt: s.nextRunAt, enabled: true },
    data: { nextRunAt: next },
  });
  if (claim.count === 0) return 'lost_claim';

  let payload: unknown = {};
  try {
    payload = JSON.parse(s.payloadJson || '{}');
  } catch {
    payload = {}; // a corrupt payload should not kill the schedule; the function sees {}
  }

  try {
    // The OWNER pays. The executor does plan resolution, quota, affordability, settlement.
    const res = await invokeFunction({
      functionId: s.functionId,
      payload,
      callerAccountId: s.ownerId,
      callerKind: 'schedule',
    });
    if (res.status === 'succeeded') {
      await recordSuccess(s.id, new Date());
      return 'fired';
    }
    await recordFailure(s.id, new Date(), res.errorType || res.status, res.error ?? '');
    return 'failed';
  } catch (e: any) {
    if (e instanceof ApiError && SOFT_CODES.has(e.code)) {
      await recordSkip(s.id, e.code);
      return 'skipped';
    }
    // not_found / not_active / suspended / anything unexpected: a real failure that should
    // eventually auto-disable a schedule pointing at a dead target.
    const code = e instanceof ApiError ? e.code : 'error';
    await recordFailure(s.id, new Date(), code, String(e?.message ?? e));
    return 'failed';
  }
}

// ── Main tick ────────────────────────────────────────────────────────────────

async function runPool<T>(items: T[], size: number, worker: (item: T) => Promise<void>): Promise<void> {
  let i = 0;
  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(size, items.length)) }, async () => {
      while (i < items.length) {
        const item = items[i++];
        await worker(item);
      }
    }),
  );
}

async function main() {
  const { dryRun } = parseFlags();
  const now = new Date();

  if (!flags.schedules) {
    log('info', 'feature_disabled', { flag: 'CLOUD_SCHEDULES_ENABLED' });
    return;
  }

  // Disarmed (default) or --dry-run: observe-only — report what a live tick would do.
  if (!ENABLED || dryRun) {
    const [due, inFlight, enabled] = await Promise.all([
      prisma.cloudSchedule.count({ where: { enabled: true, nextRunAt: { lte: now } } }),
      prisma.cloudExecution.count({ where: { callerKind: 'schedule', status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } } }),
      prisma.cloudSchedule.count({ where: { enabled: true } }),
    ]);
    log('info', 'disarmed_observe_only', {
      enabled: ENABLED,
      dryRun,
      detail: `would fire up to ${Math.min(due, MAX_PER_TICK)} of ${due} due schedules (${enabled} enabled total, ${inFlight} schedule executions in flight)`,
    });
    return;
  }

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }

  const startedMs = Date.now();
  const due = await prisma.cloudSchedule.findMany({
    where: { enabled: true, nextRunAt: { lte: now } },
    orderBy: { nextRunAt: 'asc' },
    take: MAX_PER_TICK,
    select: {
      id: true,
      functionId: true,
      ownerId: true,
      kind: true,
      intervalMinutes: true,
      cron: true,
      payloadJson: true,
      nextRunAt: true,
    },
  });

  const stats: Record<FireOutcome, number> = { fired: 0, failed: 0, skipped: 0, disabled: 0, lost_claim: 0, deferred: 0 };
  const planCache = new Map<string, ResolvedPlan>();
  await runPool(due, POOL, async (s) => {
    try {
      const outcome = await fireOne(s, planCache, new Date(), startedMs);
      stats[outcome] += 1;
    } catch (e: any) {
      // One broken schedule never aborts the tick.
      stats.failed += 1;
      log('error', 'fire_crashed', { scheduleId: s.id, error: String(e?.stack ?? e).slice(0, 500) });
    }
  });

  log('info', 'run_done', { due: due.length, ...stats, tickMs: Date.now() - startedMs });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
