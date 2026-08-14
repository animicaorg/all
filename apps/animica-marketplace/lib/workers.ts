import { randomBytes, createHash } from 'node:crypto';
import { prisma } from './db';
import { ApiError } from './api';
import { requireWorkspaceRole } from './workspaces';
import { safetyCaps } from './planConfig';
import type { ChatMessage } from './inference';

// Animica Workers — shared helpers for the /api/mkt/v1/workers/* routes and the runner
// (scripts/worker-runner.ts). Workers are FREE for every account: there is no plan gating
// anywhere in this subsystem. This file owns the free-tier abuse limits, config validation,
// token minting, DTO shapes and prompt assembly so route files export only HTTP handlers
// (house rule).

// ── Free-tier abuse limits (NOT plan limits) ─────────────────────────────────
// Workers cost real inference + scheduler capacity, so "free" cannot mean "unbounded".
// These are flat per-account constants, env-tunable without a deploy, and deliberately NOT
// read from lib/plan.ts — the old free-plan defaults were workers:0/executions:0, so wiring
// any plan resolver back in here silently bricks the whole subsystem. Platform-wide
// infrastructure caps (run seconds, global concurrency, attempts) stay in safetyCaps().

function intEnv(name: string, dflt: number): number {
  const n = Number(process.env[name]);
  return Number.isFinite(n) && n > 0 ? Math.trunc(n) : dflt;
}

export const WORKERS_FREE_LIMITS = {
  // Worker rows one account may own (workspace-shared workers count against their owner).
  maxWorkersPerAccount: intEnv('WORKERS_MAX_PER_ACCOUNT', 10),
  // Simultaneously RUNNING runs per account (the runner requeues past this, never drops).
  accountConcurrency: intEnv('WORKERS_ACCOUNT_CONCURRENCY', 2),
  // Tightest recurring schedule anyone gets; clamped at save time, not rejected.
  minIntervalMinutes: intEnv('WORKERS_MIN_INTERVAL_MINUTES', 15),
} as const;

// Interval clamp shared by create + PATCH: floor at the free minimum, cap at monthly (an
// interval longer than a month is a config typo, not a schedule).
export function clampFreeInterval(requested: number): number {
  const min = Math.max(1, WORKERS_FREE_LIMITS.minIntervalMinutes);
  const r = Math.trunc(Number.isFinite(requested) ? requested : min);
  return Math.max(min, Math.min(r, 60 * 24 * 31));
}

// One-time fixup for the plan era: PLAN_LIMIT was "shelved by a downgrade", which no longer
// exists — those workers become PAUSED so their owners can simply press Start. Idempotent
// (updateMany on the status filter) and safe to run every tick; prod currently has 0 rows.
export async function migratePlanLimitShelvedWorkers(): Promise<number> {
  const res = await prisma.worker.updateMany({
    where: { status: 'PLAN_LIMIT' },
    data: { status: 'PAUSED', statusReason: 'Workers are now free for every account — press Start to resume', nextRunAt: null },
  });
  return res.count;
}

// ── External trigger tokens (free for every account) ─────────────────────────
// Single-purpose bearer secret, pool-API-key pattern: raw shown exactly once, only the
// sha256 stored (Worker.triggerTokenHash @unique doubles as the lookup index).

export const TRIGGER_TOKEN_PREFIX = 'anm_trg_';

export function mintTriggerToken(): { raw: string; hash: string } {
  const raw = TRIGGER_TOKEN_PREFIX + randomBytes(24).toString('hex');
  return { raw, hash: hashTriggerToken(raw) };
}

export function hashTriggerToken(raw: string): string {
  return createHash('sha256').update(raw).digest('hex');
}

// ── Webhook URL validation (SSRF guard) ──────────────────────────────────────
// The runner POSTs run output to this URL from inside our network, so it must never be
// coaxed into hitting loopback/RFC1918/link-local targets (metadata endpoints, the node RPC,
// Postgres...). Two layers:
//   1. validateWebhookUrl (sync, used at create/edit time): scheme, credentials, IP-literal
//      and internal-name checks. The WHATWG parser already normalizes hex/octal/decimal IPv4
//      obfuscations to dotted-quad; we additionally strip the root-label trailing dot, since
//      "localhost." and "metadata.google.internal." are the same hosts to a resolver but slip
//      past naive suffix checks.
//   2. assertPublicWebhookTarget (async, used at DELIVERY time): resolves the hostname and
//      refuses to POST when ANY answer is a private/loopback/link-local address. This is what
//      stops an ordinary DNS name that simply points at 127.0.0.1 or 169.254.169.254. A
//      rebind between check and connect remains theoretically possible, but the window is a
//      single request and every address the name is willing to admit to must be public.

// True for addresses that must never be reached from inside our network.
export function isPrivateAddress(addr: string): boolean {
  const a = addr.toLowerCase().replace(/^\[|\]$/g, '');
  if (a.includes(':')) {
    // IPv6: ::1 loopback, :: unspecified, fc00::/7 ULA, fe80::/10 link-local,
    // and v4-mapped/compatible forms (::ffff:127.0.0.1) — recurse on the v4 tail.
    if (a === '::1' || a === '::') return true;
    const v4 = a.match(/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/);
    if (v4) return isPrivateAddress(v4[1]);
    const head = a.split(':')[0];
    if (/^f[cd]/.test(head)) return true; // fc00::/7
    if (/^fe[89ab]/.test(head)) return true; // fe80::/10
    return false;
  }
  const m = a.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!m) return false;
  const [x, y] = [Number(m[1]), Number(m[2])];
  return (
    x === 127 || // loopback
    x === 0 || // "this host"
    x === 10 || // RFC1918
    (x === 192 && y === 168) || // RFC1918
    (x === 172 && y >= 16 && y <= 31) || // RFC1918
    (x === 169 && y === 254) || // link-local / cloud metadata
    (x === 100 && y >= 64 && y <= 127) || // CGNAT
    x >= 224 // multicast + reserved
  );
}

// Delivery-time guard: every address the hostname resolves to must be public.
export async function assertPublicWebhookTarget(url: string): Promise<void> {
  const host = new URL(url).hostname.replace(/\.+$/, '');
  if (isPrivateAddress(host)) {
    throw new ApiError(400, 'bad_webhook_url', 'private webhook target');
  }
  // A literal that passed the check above needs no resolution.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return;
  const { lookup } = await import('node:dns/promises');
  let addrs: Array<{ address: string }>;
  try {
    addrs = await lookup(host, { all: true, verbatim: true });
  } catch {
    throw new ApiError(400, 'bad_webhook_url', `webhook hostname does not resolve: ${host}`);
  }
  if (!addrs.length) throw new ApiError(400, 'bad_webhook_url', `webhook hostname does not resolve: ${host}`);
  for (const a of addrs) {
    if (isPrivateAddress(a.address)) {
      throw new ApiError(400, 'bad_webhook_url', `webhook hostname resolves to a private address (${a.address})`);
    }
  }
}

export function validateWebhookUrl(raw: string): string {
  if (typeof raw !== 'string' || !raw.trim() || raw.length > 2000) {
    throw new ApiError(400, 'bad_webhook_url', 'webhookUrl must be a valid https URL');
  }
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    throw new ApiError(400, 'bad_webhook_url', 'webhookUrl must be a valid https URL');
  }
  if (url.protocol !== 'https:') throw new ApiError(400, 'bad_webhook_url', 'webhookUrl must use https');
  // Credentials in the URL leak into logs and enable confused-deputy auth — never allowed.
  if (url.username || url.password) throw new ApiError(400, 'bad_webhook_url', 'credentials in webhookUrl are not allowed');
  // Strip the root-label trailing dot: "localhost." resolves exactly like "localhost" but
  // would otherwise sail past the suffix checks below. Re-serialize from the normalized host
  // so the stored URL is the one we actually validated.
  const host = url.hostname.toLowerCase().replace(/\.+$/, '');
  if (!host) throw new ApiError(400, 'bad_webhook_url', 'webhookUrl must have a hostname');
  if (host !== url.hostname) url.hostname = host;
  // IPv6 literals ([::1], fe80::…, ULAs, v4-mapped loopback…) — parsing every private range
  // is error-prone, so fail closed on ALL of them; public webhook endpoints have names.
  if (host.startsWith('[') || host.includes(':')) {
    throw new ApiError(400, 'bad_webhook_url', 'IPv6 literal webhook targets are not allowed');
  }
  if (
    host === 'localhost' ||
    host.endsWith('.localhost') ||
    host.endsWith('.local') ||
    host.endsWith('.internal')
  ) {
    throw new ApiError(400, 'bad_webhook_url', 'internal hostnames are not allowed');
  }
  const m = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (m) {
    if (isPrivateAddress(host)) {
      throw new ApiError(400, 'bad_webhook_url', 'private/loopback IP webhook targets are not allowed');
    }
  } else if (/^\d+$/.test(host)) {
    // Bare-integer host that survived URL normalization — never a legitimate endpoint.
    throw new ApiError(400, 'bad_webhook_url', 'numeric webhook hostnames are not allowed');
  }
  return url.toString();
}

// ── Config input clamps ──────────────────────────────────────────────────────

export const WORKER_TOOLS = ['webhook', 'anm_publish'] as const;
export type WorkerTool = (typeof WORKER_TOOLS)[number];

export const NAME_MAX = 80;
export const PURPOSE_MAX = 300;
export const PROMPT_MAX = 8000;
export const MIN_RUN_SECONDS = 10;

export interface WorkerPatch {
  name?: string;
  purpose?: string;
  taskPrompt?: string;
  systemPrompt?: string;
  model?: string;
  temperature?: number;
  toolsJson?: string;
  webhookUrl?: string | null;
  anmName?: string | null;
  scheduleKind?: 'manual' | 'interval';
  intervalMinutes?: number;
  maxRunSeconds?: number;
}

// Validate + clamp the user-supplied config fields PRESENT in the body (create and PATCH
// share this; absent keys are untouched). Wrong types are rejected, oversized strings are
// clamped (not rejected — a Worker config should never be lost to a copy-paste overflow).
// The interval floor (clampFreeInterval) stays in the routes: PATCH must clamp the MERGED
// schedule, not the patch in isolation.
export function sanitizeWorkerPatch(body: Record<string, unknown>): WorkerPatch {
  const out: WorkerPatch = {};
  const str = (v: unknown, field: string): string => {
    if (typeof v !== 'string') throw new ApiError(400, 'bad_request', `${field} must be a string`);
    return v;
  };
  if ('name' in body) {
    const name = str(body.name, 'name').trim().slice(0, NAME_MAX);
    if (!name) throw new ApiError(400, 'bad_request', 'name required');
    out.name = name;
  }
  if ('purpose' in body) out.purpose = str(body.purpose, 'purpose').trim().slice(0, PURPOSE_MAX);
  if ('taskPrompt' in body) {
    const p = str(body.taskPrompt, 'taskPrompt').trim().slice(0, PROMPT_MAX);
    if (!p) throw new ApiError(400, 'bad_request', 'taskPrompt required');
    out.taskPrompt = p;
  }
  if ('systemPrompt' in body) out.systemPrompt = str(body.systemPrompt, 'systemPrompt').trim().slice(0, PROMPT_MAX);
  if ('model' in body) {
    const model = str(body.model, 'model').trim().slice(0, 80);
    if (!model) throw new ApiError(400, 'bad_request', 'model required');
    out.model = model;
  }
  if ('temperature' in body) {
    const t = Number(body.temperature);
    if (!Number.isFinite(t)) throw new ApiError(400, 'bad_request', 'temperature must be a number');
    out.temperature = Math.max(0, Math.min(2, t));
  }
  if ('tools' in body) {
    if (!Array.isArray(body.tools)) throw new ApiError(400, 'bad_request', 'tools must be an array');
    const tools = body.tools.filter((t): t is WorkerTool => (WORKER_TOOLS as readonly string[]).includes(t as string));
    if (tools.length !== body.tools.length) {
      throw new ApiError(400, 'bad_request', `tools may only contain: ${WORKER_TOOLS.join(', ')}`);
    }
    out.toolsJson = JSON.stringify(Array.from(new Set(tools)));
  }
  if ('webhookUrl' in body) {
    out.webhookUrl = body.webhookUrl == null || body.webhookUrl === '' ? null : validateWebhookUrl(str(body.webhookUrl, 'webhookUrl'));
  }
  if ('anmName' in body) {
    // Label only ("research", not "research.anm"); ownership is checked in the route.
    const v = body.anmName == null || body.anmName === '' ? null : str(body.anmName, 'anmName').trim().toLowerCase().replace(/\.anm$/, '');
    if (v !== null && !/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(v)) {
      throw new ApiError(400, 'bad_request', 'anmName must be a valid .anm label');
    }
    out.anmName = v;
  }
  if ('scheduleKind' in body) {
    if (body.scheduleKind !== 'manual' && body.scheduleKind !== 'interval') {
      throw new ApiError(400, 'bad_request', "scheduleKind must be 'manual' or 'interval'");
    }
    out.scheduleKind = body.scheduleKind;
  }
  if ('intervalMinutes' in body) {
    const n = Number(body.intervalMinutes);
    if (!Number.isFinite(n) || n < 1) throw new ApiError(400, 'bad_request', 'intervalMinutes must be a positive number');
    out.intervalMinutes = Math.trunc(n); // free-minimum clamp happens in the route
  }
  if ('maxRunSeconds' in body) {
    const n = Number(body.maxRunSeconds);
    if (!Number.isFinite(n)) throw new ApiError(400, 'bad_request', 'maxRunSeconds must be a number');
    out.maxRunSeconds = Math.max(MIN_RUN_SECONDS, Math.min(Math.trunc(n), safetyCaps().maxRunSeconds));
  }
  return out;
}

// A tool without its wiring is a run that can only fail at 3am — reject at save time.
export function assertToolWiring(tools: string[], webhookUrl: string | null, anmName: string | null): void {
  if (tools.includes('webhook') && !webhookUrl) throw new ApiError(400, 'bad_request', "the 'webhook' tool requires webhookUrl");
  if (tools.includes('anm_publish') && !anmName) throw new ApiError(400, 'bad_request', "the 'anm_publish' tool requires anmName");
}

export function parseTools(toolsJson: string | null | undefined): string[] {
  try {
    const v = JSON.parse(toolsJson || '[]');
    return Array.isArray(v) ? v.filter((t) => typeof t === 'string') : [];
  } catch {
    return [];
  }
}

// ── Access control ───────────────────────────────────────────────────────────
// 'read'  = owner, or any active workspace member  (GET / runs / run_now)
// 'admin' = owner, or workspace ADMIN+             (PATCH / actions)
// 'owner' = the owning account only                (DELETE)
// Always 404 (never 403) so worker ids don't leak existence; a workspace-check failure of
// ANY kind (missing membership, missing workspace, thrown ApiError) reads as no access.

export type WorkerAccess = 'read' | 'admin' | 'owner';

export async function requireWorkerAccess(workerId: string, accountId: string, level: WorkerAccess) {
  const worker = await prisma.worker.findUnique({ where: { id: workerId } });
  if (!worker) throw new ApiError(404, 'not_found', 'worker not found');
  if (worker.accountId === accountId) return worker;
  if (level !== 'owner' && worker.workspaceId) {
    try {
      await requireWorkspaceRole(worker.workspaceId, accountId, level === 'admin' ? 'ADMIN' : 'MEMBER');
      return worker;
    } catch {
      // fall through — treat any failure as no access
    }
  }
  throw new ApiError(404, 'not_found', 'worker not found');
}

// ── Enqueue (run_now + external trigger) ─────────────────────────────────────
// One place so the manual action and the trigger route can never drift. Executions are free
// and unmetered — no quota consume, no plan gate. Every run enqueues at priority 0 (the
// queue is a plain FIFO now; the column survives only for pre-migration PENDING rows).
// Abuse control happens elsewhere: the 6/min trigger rate limit, the per-account
// concurrency requeue in the runner, and safetyCaps().

export async function enqueueRun(
  worker: { id: string; accountId: string },
  trigger: 'manual' | 'external',
) {
  return prisma.workerRun.create({
    data: {
      workerId: worker.id,
      accountId: worker.accountId,
      trigger,
      priority: 0,
    },
  });
}

// ── DTOs (the exact shapes the contract promises Agent UI) ───────────────────

export function workerDto(w: any, lastRun?: any, opts: { config?: boolean } = {}) {
  return {
    id: w.id,
    name: w.name,
    purpose: w.purpose,
    // Full execution config only on the single-worker GET (the edit form needs it);
    // list rows stay lean.
    ...(opts.config
      ? { taskPrompt: w.taskPrompt, systemPrompt: w.systemPrompt, temperature: w.temperature }
      : {}),
    status: w.status,
    statusReason: w.statusReason ?? null,
    model: w.model,
    tools: parseTools(w.toolsJson),
    webhookUrl: w.webhookUrl ?? null,
    anmName: w.anmName ?? null,
    scheduleKind: w.scheduleKind,
    intervalMinutes: w.intervalMinutes ?? null,
    nextRunAt: w.nextRunAt ?? null,
    lastRunAt: w.lastRunAt ?? null,
    maxRunSeconds: w.maxRunSeconds,
    failureThreshold: w.failureThreshold,
    consecutiveFailures: w.consecutiveFailures,
    runsTotal: w.runsTotal,
    workspaceId: w.workspaceId ?? null,
    accountId: w.accountId, // lets the UI tell own workers (account slots) from workspace-shared ones
    hasTriggerToken: !!w.triggerTokenHash, // the raw token is never re-shown
    createdAt: w.createdAt,
    ...(lastRun
      ? { lastRun: { status: lastRun.status, finishedAt: lastRun.finishedAt ?? null, durationMs: lastRun.durationMs ?? null, error: lastRun.error ?? null } }
      : {}),
  };
}

// List rows stay lean; `full` (run detail route) adds the output + step log.
export function runDto(r: any, opts: { full?: boolean } = {}) {
  return {
    id: r.id,
    status: r.status,
    trigger: r.trigger,
    attempts: r.attempts,
    scheduledFor: r.scheduledFor ?? null,
    startedAt: r.startedAt ?? null,
    finishedAt: r.finishedAt ?? null,
    durationMs: r.durationMs ?? null,
    tokensIn: r.tokensIn,
    tokensOut: r.tokensOut,
    error: r.error ?? null,
    ...(opts.full
      ? {
          output: r.output ?? null,
          logs: (() => {
            try {
              const v = JSON.parse(r.logsJson || '[]');
              return Array.isArray(v) ? v : [];
            } catch {
              return [];
            }
          })(),
        }
      : {}),
  };
}

// ── Prompt assembly ──────────────────────────────────────────────────────────
// The guardrail preamble is appended AFTER the user's systemPrompt so it cannot be shadowed
// by it; the current date goes in the user turn because scheduled prompts are usually
// time-relative ("summarize yesterday's…") and the model has no other clock.

export function buildWorkerMessages(worker: { systemPrompt: string; taskPrompt: string }): ChatMessage[] {
  const guardrails =
    'You are an autonomous Animica Worker executing a scheduled task with no human in the loop. ' +
    'Perform only the configured task below and reply with the task output only. ' +
    'You have no access to other systems, accounts, or workers, and you must not attempt any action beyond producing this output.';
  const system = [worker.systemPrompt?.trim(), guardrails].filter(Boolean).join('\n\n');
  const user = `${worker.taskPrompt}\n\nCurrent date: ${new Date().toISOString()}`;
  return [
    { role: 'system', content: system },
    { role: 'user', content: user },
  ];
}
