import { createHash, randomBytes } from 'node:crypto';
import { prisma } from './db';
import { Prisma } from '@prisma/client';
import { moderateMediaPrompt, MediaBlockedError } from './mediaModeration';

// Generative-media job QUEUE (dispatch-only). No model runs on the gateway. Requests are
// enqueued PENDING; a registered GPU media miner atomically CLAIMS + renders them; results
// are delivered back to the requester. A job therefore GOES THROUGH EVENTUALLY — it waits
// in the queue for a free miner instead of failing. Private jobs (image->video from user
// uploads) keep their input bytes ONLY until the job is terminal, never expose them on any
// read path, and wipe them on completion/expiry/delivery. Miner pay is an IOU only.

export type MediaKind = 'image' | 'video_t2v' | 'video_i2v' | 'video_multiscene' | 'audio';
export const MEDIA_KINDS: MediaKind[] = ['image', 'video_t2v', 'video_i2v', 'video_multiscene', 'audio'];

const num = (name: string, d: number) => Number(process.env[name] ?? d);

// Lease + TTL windows.
export const MEDIA_LEASE_SECS = num('MEDIA_LEASE_SECS', 300);        // a claimed job must finish inside this
export const MEDIA_JOB_TTL_SECS = num('MEDIA_JOB_TTL_SECS', 86400);  // undelivered job expires after this
export const MEDIA_PRIVATE_TTL_SECS = num('MEDIA_PRIVATE_TTL_SECS', 21600); // private uploads live at most 6h
export const MEDIA_MAX_ATTEMPTS = num('MEDIA_MAX_ATTEMPTS', 4);
export const MEDIA_MINER_STALE_SECS = num('MEDIA_MINER_STALE_SECS', 90);
// IOU reward per completed job (nANM). Never a spendable balance — deferred treasury settlement.
export const MEDIA_REWARD_NANM: Record<string, bigint> = {
  image: BigInt(num('MEDIA_REWARD_IMAGE_NANM', 200_000_000)),          // ~0.2 ANM
  video_t2v: BigInt(num('MEDIA_REWARD_VIDEO_NANM', 2_000_000_000)),    // ~2 ANM
  video_i2v: BigInt(num('MEDIA_REWARD_VIDEO_NANM', 2_000_000_000)),
  video_multiscene: BigInt(num('MEDIA_REWARD_MULTISCENE_NANM', 3_000_000_000)), // ~3 ANM
  audio: BigInt(num('MEDIA_REWARD_AUDIO_NANM', 1_000_000_000)),        // ~1 ANM
};

export function sha3(buf: Buffer | string): string {
  return createHash('sha3-256').update(buf).digest('hex');
}

export function newMinerToken(): string {
  return 'anm_med_' + randomBytes(24).toString('base64url');
}

// ── Lazy maintenance ─────────────────────────────────────────────────────────
// Called opportunistically on submit/claim/poll so the queue self-heals without a cron:
//  * expired leases: non-terminal RUNNING jobs whose lease lapsed return to PENDING
//    (or FAIL after MEDIA_MAX_ATTEMPTS), so a crashed miner never wedges a job.
//  * TTL: undelivered jobs past their deadline EXPIRE and their private inputs are wiped.
//  * stale miners are marked offline.
let _lastSweep = 0;
export async function sweep(force = false): Promise<void> {
  const now = Date.now();
  if (!force && now - _lastSweep < 4000) return; // throttle
  _lastSweep = now;
  const nowD = new Date(now);
  try {
    // Requeue jobs whose lease expired but still have attempts left.
    await prisma.mediaJob.updateMany({
      where: { status: 'RUNNING', leaseUntil: { lt: nowD }, attempts: { lt: MEDIA_MAX_ATTEMPTS } },
      data: { status: 'PENDING', claimedById: null, leaseUntil: null },
    });
    // Fail jobs that exhausted their attempts.
    await prisma.mediaJob.updateMany({
      where: { status: 'RUNNING', leaseUntil: { lt: nowD }, attempts: { gte: MEDIA_MAX_ATTEMPTS } },
      data: { status: 'FAILED', error: 'no miner completed the job in time', claimedById: null, inputB64: null },
    });
    // Expire undelivered jobs past their TTL and wipe any private inputs/results.
    await prisma.mediaJob.updateMany({
      where: { status: { in: ['PENDING', 'RUNNING'] }, expiresAt: { lt: nowD } },
      data: { status: 'EXPIRED', error: 'expired before a miner was available', inputB64: null, resultB64: null, claimedById: null },
    });
    // Mark stale miners offline.
    await prisma.mediaMiner.updateMany({
      where: { online: true, lastSeenAt: { lt: new Date(now - MEDIA_MINER_STALE_SECS * 1000) } },
      data: { online: false },
    });
  } catch {
    /* best-effort */
  }
}

// ── Submit ───────────────────────────────────────────────────────────────────
export interface SubmitInput {
  kind: MediaKind;
  prompt?: string;
  params?: Record<string, unknown>;
  inputB64?: string | null;   // private i2v uploads (JSON array of data — caller stringifies)
  isPrivate?: boolean;
  requesterIp?: string | null;
  requesterAcct?: string | null;
  priority?: number;
}

export async function submitJob(inp: SubmitInput) {
  // Content-safety backstop. Routes pre-check and return a friendly 422, but enforce here too
  // so NO caller (present or future) can enqueue prohibited content. Throws MediaBlockedError.
  const verdict = moderateMediaPrompt(inp.prompt ?? '', { hasImages: !!inp.inputB64, kind: inp.kind });
  if (!verdict.allowed) throw new MediaBlockedError(verdict);
  await sweep();
  const isPrivate = !!inp.isPrivate;
  const ttl = isPrivate ? MEDIA_PRIVATE_TTL_SECS : MEDIA_JOB_TTL_SECS;
  const job = await prisma.mediaJob.create({
    data: {
      kind: inp.kind,
      prompt: (inp.prompt ?? '').slice(0, 4000),
      paramsJson: JSON.stringify(inp.params ?? {}),
      inputB64: inp.inputB64 ?? null,
      isPrivate,
      priority: inp.priority ?? 0,
      requesterIp: inp.requesterIp ?? null,
      requesterAcct: inp.requesterAcct ?? null,
      expiresAt: new Date(Date.now() + ttl * 1000),
    },
    select: { id: true, kind: true, status: true, createdAt: true },
  });
  const position = await queuePosition(job.id, inp.kind);
  return { ...job, position, minersOnline: await onlineMinerCount(inp.kind) };
}

// 1-based position of a PENDING job among same-kind pending jobs (what the UI shows).
export async function queuePosition(jobId: string, kind: string): Promise<number> {
  const job = await prisma.mediaJob.findUnique({ where: { id: jobId }, select: { status: true, priority: true, createdAt: true } });
  if (!job || job.status !== 'PENDING') return 0;
  const ahead = await prisma.mediaJob.count({
    where: {
      kind, status: 'PENDING',
      OR: [{ priority: { gt: job.priority } }, { priority: job.priority, createdAt: { lt: job.createdAt } }],
    },
  });
  return ahead + 1;
}

export async function onlineMinerCount(kind?: string): Promise<number> {
  const where: Prisma.MediaMinerWhereInput = { online: true, lastSeenAt: { gte: new Date(Date.now() - MEDIA_MINER_STALE_SECS * 1000) } };
  if (kind) where.capabilities = { has: kind };
  return prisma.mediaMiner.count({ where });
}

// Online miner count for EVERY media kind in one pass (the public capabilities
// endpoint is polled by every homepage visitor, so avoid one COUNT per kind).
// Pulls each online miner's capabilities once and tallies in-process.
export async function onlineCapabilityCounts(): Promise<Record<string, number>> {
  const rows = await prisma.mediaMiner.findMany({
    where: { online: true, lastSeenAt: { gte: new Date(Date.now() - MEDIA_MINER_STALE_SECS * 1000) } },
    select: { capabilities: true },
  });
  const counts: Record<string, number> = {};
  for (const k of MEDIA_KINDS) counts[k] = 0;
  for (const r of rows) {
    for (const cap of r.capabilities || []) {
      if (cap in counts) counts[cap] += 1;
    }
  }
  return counts;
}

// ── Poll (requester) ───────────────────────────────────────────────────────────
// Returns a safe view of the job. On first delivery of a DONE PRIVATE result, hands the
// bytes over exactly once and then wipes them (privacy). Never returns inputB64.
export async function pollJob(id: string) {
  await sweep();
  const job = await prisma.mediaJob.findUnique({ where: { id } });
  if (!job) return null;

  const base = {
    id: job.id,
    kind: job.kind,
    status: job.status,
    isPrivate: job.isPrivate,
    createdAt: job.createdAt,
    error: job.error ?? undefined,
    attempts: job.attempts,
  };

  if (job.status !== 'DONE') {
    const position = job.status === 'PENDING' ? await queuePosition(job.id, job.kind) : 0;
    const minersOnline = await onlineMinerCount(job.kind);
    return { ...base, position, minersOnline, result: null };
  }

  // DONE — deliver the result.
  const b64 = job.resultB64;
  if (job.isPrivate) {
    // Deliver once, then wipe so the private render isn't re-servable from the gateway.
    if (!job.resultB64 && job.deliveredAt) {
      return { ...base, status: 'EXPIRED', position: 0, minersOnline: await onlineMinerCount(job.kind), result: null, error: 'private result already delivered and wiped' };
    }
    await prisma.mediaJob.update({ where: { id: job.id }, data: { resultB64: null, deliveredAt: new Date() } }).catch(() => {});
  } else if (!job.deliveredAt) {
    await prisma.mediaJob.update({ where: { id: job.id }, data: { deliveredAt: new Date() } }).catch(() => {});
  }

  let meta: any = undefined;
  try { meta = job.resultMeta ? JSON.parse(job.resultMeta) : undefined; } catch { /* ignore */ }
  return {
    ...base,
    position: 0,
    minersOnline: await onlineMinerCount(job.kind),
    result: { b64, mime: job.resultMime, sha3: job.resultSha3, cid: job.resultCid ?? undefined, meta },
  };
}

// ── Miner registration + auth ──────────────────────────────────────────────────
export async function registerMiner(opts: {
  token: string;
  label?: string;
  capabilities: string[];
  device?: string;
  maxPixels?: number;
  address?: string;
  ownerAccountId?: string;
}) {
  const keyHash = sha3(opts.token);
  const caps = (opts.capabilities || []).filter((c) => MEDIA_KINDS.includes(c as MediaKind));
  if (caps.length === 0) caps.push('image');
  const data = {
    label: (opts.label ?? '').slice(0, 80),
    capabilities: caps,
    device: (opts.device ?? '').slice(0, 24),
    maxPixels: Math.max(4096, Math.min(opts.maxPixels ?? 1048576, 4_194_304)),
    address: opts.address?.slice(0, 128) ?? null,
    ownerAccountId: opts.ownerAccountId ?? null,
    online: true,
    lastSeenAt: new Date(),
  };
  const miner = await prisma.mediaMiner.upsert({
    where: { keyHash },
    create: { keyHash, ...data },
    update: data,
    select: { id: true, label: true, capabilities: true, device: true, online: true, jobsDone: true, rewardNanm: true },
  });
  return miner;
}

export async function resolveMiner(token: string) {
  if (!token) return null;
  const miner = await prisma.mediaMiner.findUnique({ where: { keyHash: sha3(token) } });
  return miner;
}

// ── Claim (miner) ──────────────────────────────────────────────────────────────
// Atomic: SELECT ... FOR UPDATE SKIP LOCKED so two miners never grab the same job. Returns
// the claimed job WITH its private input (handed only to the authenticated claiming miner),
// or null when the queue has nothing this miner can serve.
export async function claimJob(minerId: string, capabilities: string[]) {
  await sweep();
  const caps = capabilities.filter((c) => MEDIA_KINDS.includes(c as MediaKind));
  if (caps.length === 0) return null;
  const leaseUntil = new Date(Date.now() + MEDIA_LEASE_SECS * 1000);

  // Postgres queue claim. RETURNING gives us the row we won.
  const rows = await prisma.$queryRaw<Array<any>>(Prisma.sql`
    UPDATE "MediaJob" SET
      status = 'RUNNING',
      "claimedById" = ${minerId},
      "leaseUntil" = ${leaseUntil},
      attempts = attempts + 1,
      "updatedAt" = now()
    WHERE id = (
      SELECT id FROM "MediaJob"
      WHERE status = 'PENDING' AND kind = ANY(${caps}::text[])
      ORDER BY priority DESC, "createdAt" ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING id, kind, prompt, "paramsJson", "inputB64", "isPrivate", attempts;
  `);
  await prisma.mediaMiner.update({ where: { id: minerId }, data: { lastSeenAt: new Date(), online: true } }).catch(() => {});
  const r = rows?.[0];
  if (!r) return null;
  let params: any = {};
  try { params = JSON.parse(r.paramsJson || '{}'); } catch { /* ignore */ }
  return { id: r.id, kind: r.kind, prompt: r.prompt, params, inputB64: r.inputB64 ?? null, isPrivate: r.isPrivate, attempts: r.attempts };
}

// ── Post result (miner) ──────────────────────────────────────────────────────────
export async function postResult(minerId: string, jobId: string, res: { ok: boolean; b64?: string; mime?: string; sha3?: string; meta?: any; error?: string }) {
  const job = await prisma.mediaJob.findUnique({ where: { id: jobId }, select: { id: true, kind: true, claimedById: true, status: true, isPrivate: true } });
  if (!job) return { ok: false, code: 'not_found' as const };
  if (job.claimedById !== minerId) return { ok: false, code: 'not_owner' as const };
  if (job.status !== 'RUNNING') return { ok: false, code: 'not_running' as const };

  if (!res.ok || !res.b64) {
    await prisma.mediaJob.update({
      where: { id: jobId },
      data: { status: 'FAILED', error: (res.error || 'miner reported failure').slice(0, 400), inputB64: null },
    });
    return { ok: true, terminal: 'FAILED' as const };
  }

  const bytes = Buffer.from(res.b64, 'base64');
  const digest = sha3(bytes);
  await prisma.$transaction([
    prisma.mediaJob.update({
      where: { id: jobId },
      data: {
        status: 'DONE',
        resultB64: res.b64,
        resultMime: res.mime || 'application/octet-stream',
        resultSha3: res.sha3 || digest,
        resultMeta: res.meta ? JSON.stringify(res.meta).slice(0, 2000) : null,
        error: null,
        inputB64: null, // private inputs are done being needed — wipe them
      },
    }),
    prisma.mediaMiner.update({
      where: { id: minerId },
      data: { jobsDone: { increment: 1 }, rewardNanm: { increment: MEDIA_REWARD_NANM[job.kind] ?? 0n }, lastSeenAt: new Date() },
    }),
  ]);
  return { ok: true, terminal: 'DONE' as const, sha3: res.sha3 || digest };
}
