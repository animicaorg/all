import { createHash, randomBytes } from 'node:crypto';
import { prisma } from './db';
import { Prisma } from '@prisma/client';
import { moderateMediaPrompt, MediaBlockedError } from './mediaModeration';
import { compileImagePrompt, snapImageSize, pickSeed, normalizePrecision } from './imagePrompt';
import { findReferenceImages, webRefsEnabled } from './webRefs';
import { planShots } from './videoPlan';

// Generative-media job QUEUE (dispatch-only). No model runs on the gateway. Requests are
// enqueued PENDING; a registered GPU media miner atomically CLAIMS + renders them; results
// are delivered back to the requester. A job therefore GOES THROUGH EVENTUALLY — it waits
// in the queue for a free miner instead of failing. Private jobs (image->video from user
// uploads) keep their input bytes ONLY until the job is terminal, never expose them on any
// read path, and wipe them on completion/expiry/delivery. Miner pay is an IOU only.

export type MediaKind =
  | 'image' | 'video_t2v' | 'video_i2v' | 'video_multiscene' | 'audio'
  // 9.0.0 GPU Studios (docs/gpu-studios-9.0.0.md)
  | 'video_upscale' | 'video_interpolate' | 'video_subtitles' | 'video_bgremove' | 'video_shorts'
  | 'audio_stems' | 'audio_isolate' | 'audio_enhance' | 'audio_master'
  | 'render_blender' | 'render_chunk' | 'render_assemble'
  // 11.1.0 distributed video: a video_t2v/video_multiscene PARENT is planned into shots that
  // different miners render in parallel, then one miner assembles them (hand-off).
  | 'video_shot' | 'video_assemble';

// Kinds a CLIENT may submit. render_chunk/render_assemble are gateway-created children of a
// render_blender parent — a direct submit of one would orphan the orchestration.
export const SUBMIT_KINDS: MediaKind[] = [
  'image', 'video_t2v', 'video_i2v', 'video_multiscene', 'audio',
  'video_upscale', 'video_interpolate', 'video_subtitles', 'video_bgremove', 'video_shorts',
  'audio_stems', 'audio_isolate', 'audio_enhance', 'audio_master',
  'render_blender',
];
// Kinds a MINER may advertise/claim. render_blender parents sit in status WAITING and are
// never claimable — a miner advertising it would only pollute the capability counts.
export const CLAIMABLE_KINDS: MediaKind[] = [
  'image', 'video_t2v', 'video_i2v', 'video_multiscene', 'audio',
  'video_upscale', 'video_interpolate', 'video_subtitles', 'video_bgremove', 'video_shorts',
  'audio_stems', 'audio_isolate', 'audio_enhance', 'audio_master',
  'render_chunk', 'render_assemble',
  'video_shot', 'video_assemble',
];
// Union — what the public capabilities endpoint reports on.
export const MEDIA_KINDS: MediaKind[] = Array.from(new Set<MediaKind>([...SUBMIT_KINDS, ...CLAIMABLE_KINDS]));

// Studio kinds whose inputs arrive as disk uploads (MediaUpload ids in params.upload_ids).
export const UPLOAD_KINDS = new Set<string>([
  'video_upscale', 'video_interpolate', 'video_subtitles', 'video_bgremove', 'video_shorts',
  'audio_stems', 'audio_isolate', 'audio_enhance', 'audio_master', 'render_blender',
]);
export const UPLOAD_PURPOSE_FOR_KIND: Record<string, string> = {
  video_upscale: 'video', video_interpolate: 'video', video_subtitles: 'video',
  video_bgremove: 'video', video_shorts: 'video',
  audio_stems: 'audio', audio_isolate: 'audio', audio_enhance: 'audio', audio_master: 'audio',
  render_blender: 'blend',
};

const num = (name: string, d: number) => Number(process.env[name] ?? d);

// Lease + TTL windows.
export const MEDIA_LEASE_SECS = num('MEDIA_LEASE_SECS', 300);        // a claimed job must finish inside this
export const MEDIA_JOB_TTL_SECS = num('MEDIA_JOB_TTL_SECS', 86400);  // undelivered job expires after this
export const MEDIA_PRIVATE_TTL_SECS = num('MEDIA_PRIVATE_TTL_SECS', 21600); // private uploads live at most 6h
export const MEDIA_MAX_ATTEMPTS = num('MEDIA_MAX_ATTEMPTS', 4);
export const MEDIA_MINER_STALE_SECS = num('MEDIA_MINER_STALE_SECS', 90);
export const MEDIA_UPLOAD_TTL_SECS = num('MEDIA_UPLOAD_TTL_SECS', 14400);   // unconsumed uploads live 4h
export const MEDIA_UPLOAD_DONE_GRACE_SECS = num('MEDIA_UPLOAD_DONE_GRACE_SECS', 1800); // after job success
export const MEDIA_RENDER_CHUNK_FRAMES = Math.max(1, num('MEDIA_RENDER_CHUNK_FRAMES', 20));
export const MEDIA_RENDER_MAX_FRAMES = Math.max(1, num('MEDIA_RENDER_MAX_FRAMES', 2000));

// Per-kind claim leases: heavy studio kinds legitimately run past the classic 300s. Miners
// also extend their lease with every progress heartbeat, so these are "time to first sign
// of life", not a hard render budget.
const LEASE_SECS_BY_KIND: Record<string, number> = {
  video_upscale: 900, video_interpolate: 900, video_subtitles: 900, video_bgremove: 900, video_shorts: 900,
  audio_stems: 600, audio_isolate: 600,
  render_chunk: 1200, render_assemble: 900,
  video_shot: 900, video_assemble: 600,
};
export function leaseSecsFor(kind: string): number {
  return LEASE_SECS_BY_KIND[kind] ?? MEDIA_LEASE_SECS;
}

// IOU reward per completed job (nANM). Never a spendable balance — settled on-chain by
// IOU-settlement anchors (block-reward carve, ≤50 ANM/block from height 50,000).
export const MEDIA_REWARD_NANM: Record<string, bigint> = {
  image: BigInt(num('MEDIA_REWARD_IMAGE_NANM', 200_000_000)),          // ~0.2 ANM
  video_t2v: BigInt(num('MEDIA_REWARD_VIDEO_NANM', 2_000_000_000)),    // ~2 ANM
  video_i2v: BigInt(num('MEDIA_REWARD_VIDEO_NANM', 2_000_000_000)),
  video_multiscene: BigInt(num('MEDIA_REWARD_MULTISCENE_NANM', 3_000_000_000)), // ~3 ANM
  audio: BigInt(num('MEDIA_REWARD_AUDIO_NANM', 1_000_000_000)),        // ~1 ANM
  // 9.0.0 GPU Studios
  video_upscale: BigInt(num('MEDIA_REWARD_VIDEO_UPSCALE_NANM', 3_000_000_000)),
  video_interpolate: BigInt(num('MEDIA_REWARD_VIDEO_INTERPOLATE_NANM', 3_000_000_000)),
  video_subtitles: BigInt(num('MEDIA_REWARD_VIDEO_SUBTITLES_NANM', 1_000_000_000)),
  video_bgremove: BigInt(num('MEDIA_REWARD_VIDEO_BGREMOVE_NANM', 3_000_000_000)),
  video_shorts: BigInt(num('MEDIA_REWARD_VIDEO_SHORTS_NANM', 2_000_000_000)),
  audio_stems: BigInt(num('MEDIA_REWARD_AUDIO_STEMS_NANM', 1_500_000_000)),
  audio_isolate: BigInt(num('MEDIA_REWARD_AUDIO_ISOLATE_NANM', 1_000_000_000)),
  audio_enhance: BigInt(num('MEDIA_REWARD_AUDIO_ENHANCE_NANM', 500_000_000)),
  audio_master: BigInt(num('MEDIA_REWARD_AUDIO_MASTER_NANM', 500_000_000)),
  render_chunk: BigInt(num('MEDIA_REWARD_RENDER_CHUNK_NANM', 2_500_000_000)),
  render_assemble: BigInt(num('MEDIA_REWARD_RENDER_ASSEMBLE_NANM', 500_000_000)),
  video_shot: BigInt(num('MEDIA_REWARD_VIDEO_SHOT_NANM', 1_000_000_000)),          // ~1 ANM per shot
  video_assemble: BigInt(num('MEDIA_REWARD_VIDEO_ASSEMBLE_NANM', 500_000_000)),
  // render_blender is the parent bookkeeping row — the work is paid via its children.
  render_blender: 0n,
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
      data: { status: 'PENDING', claimedById: null, leaseUntil: null, progressPct: null, progressNote: null },
    });
    // Fail jobs that exhausted their attempts (a failed render_chunk/assemble fails its parent — below).
    await prisma.mediaJob.updateMany({
      where: { status: 'RUNNING', leaseUntil: { lt: nowD }, attempts: { gte: MEDIA_MAX_ATTEMPTS } },
      data: { status: 'FAILED', error: 'no miner completed the job in time', claimedById: null, inputB64: null },
    });
    // Expire undelivered jobs past their TTL and wipe any private inputs/results.
    const toExpire = await prisma.mediaJob.findMany({
      where: { status: { in: ['PENDING', 'RUNNING', 'WAITING'] }, expiresAt: { lt: nowD } },
      select: { id: true, resultPath: true },
    });
    if (toExpire.length) {
      await prisma.mediaJob.updateMany({
        where: { id: { in: toExpire.map((j) => j.id) } },
        data: { status: 'EXPIRED', error: 'expired before completion', inputB64: null, resultB64: null, resultPath: null, claimedById: null },
      });
      // Cancel still-open children of expired render parents.
      await prisma.mediaJob.updateMany({
        where: { parentId: { in: toExpire.map((j) => j.id) }, status: { in: ['PENDING', 'RUNNING', 'WAITING'] } },
        data: { status: 'CANCELLED', error: 'parent expired', inputB64: null, claimedById: null },
      });
      const { removeFile } = await import('./mediaStore');
      for (const j of toExpire) await removeFile(j.resultPath);
    }
    // Propagate terminally-failed children to their render parent (and cancel siblings).
    // Raw join keeps this bounded: only children whose parent is still WAITING match.
    const failedChildren = await prisma.$queryRaw<Array<{ id: string; parentId: string; error: string | null }>>(Prisma.sql`
      SELECT c.id, c."parentId", c.error FROM "MediaJob" c
      JOIN "MediaJob" p ON p.id = c."parentId"
      WHERE c.status = 'FAILED' AND p.status = 'WAITING'
    `);
    for (const c of failedChildren) {
      const flipped = await prisma.mediaJob.updateMany({
        where: { id: c.parentId!, status: 'WAITING' },
        data: { status: 'FAILED', error: `render chunk failed: ${(c.error || 'unknown').slice(0, 200)}` },
      });
      if (flipped.count > 0) {
        await prisma.mediaJob.updateMany({
          where: { parentId: c.parentId!, status: { in: ['PENDING', 'RUNNING'] } },
          data: { status: 'CANCELLED', error: 'sibling chunk failed', inputB64: null, claimedById: null },
        });
      }
    }
    // Expired disk artifacts of terminal jobs (TTL passed) — unlink + clear the pointer.
    const staleArtifacts = await prisma.mediaJob.findMany({
      where: { status: { in: ['DONE', 'FAILED', 'EXPIRED', 'CANCELLED'] }, resultPath: { not: null }, expiresAt: { lt: nowD } },
      select: { id: true, resultPath: true },
    });
    if (staleArtifacts.length) {
      const { removeFile } = await import('./mediaStore');
      for (const j of staleArtifacts) await removeFile(j.resultPath);
      await prisma.mediaJob.updateMany({
        where: { id: { in: staleArtifacts.map((j) => j.id) } },
        data: { resultPath: null },
      });
    }
    // Expired uploads — unlink files, then drop the rows.
    const deadUploads = await prisma.mediaUpload.findMany({
      where: { expiresAt: { lt: nowD } },
      select: { id: true, path: true },
    });
    if (deadUploads.length) {
      const { removeFile } = await import('./mediaStore');
      for (const u of deadUploads) await removeFile(u.path);
      await prisma.mediaUpload.deleteMany({ where: { id: { in: deadUploads.map((u) => u.id) } } });
    }
    // Mark stale miners offline.
    await prisma.mediaMiner.updateMany({
      where: { online: true, lastSeenAt: { lt: new Date(now - MEDIA_MINER_STALE_SECS * 1000) } },
      data: { online: false },
    });
    // Abandoned .part temp files from aborted streams.
    const { sweepPartFiles } = await import('./mediaStore');
    await sweepPartFiles();
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

export class UploadRefError extends Error {
  constructor(public code: string, msg: string) { super(msg); }
}

// Validate the upload ids a studio submit references: they must exist, be un-expired, and
// carry the purpose the kind expects (audio_master's reference upload is also 'reference').
// Reuse across resubmits IS allowed (client-side miner cycling re-posts the same ids).
async function checkUploads(kind: string, ids: string[], referenceId?: string | null) {
  const want = UPLOAD_PURPOSE_FOR_KIND[kind];
  if (!want) return;
  if (!ids.length) throw new UploadRefError('missing_upload', `${kind} needs an uploaded ${want} (POST /api/mkt/v1/media/uploads first)`);
  if (ids.length > 1) throw new UploadRefError('too_many_uploads', 'exactly one input upload per job');
  const all = [...ids, ...(referenceId ? [referenceId] : [])];
  const rows = await prisma.mediaUpload.findMany({ where: { id: { in: all } } });
  const byId = new Map(rows.map((r) => [r.id, r]));
  for (const id of all) {
    const u = byId.get(id);
    if (!u || u.expiresAt < new Date()) throw new UploadRefError('upload_gone', 'upload not found or expired — upload the file again');
  }
  const main = byId.get(ids[0])!;
  if (main.purpose !== want) throw new UploadRefError('wrong_upload_kind', `this job needs a ${want} upload, got ${main.purpose}`);
  if (referenceId && byId.get(referenceId)!.purpose !== 'reference' && byId.get(referenceId)!.purpose !== 'audio') {
    throw new UploadRefError('wrong_upload_kind', 'reference must be an audio/reference upload');
  }
}

export async function submitJob(inp: SubmitInput) {
  // Content-safety backstop. Routes pre-check and return a friendly 422, but enforce here too
  // so NO caller (present or future) can enqueue prohibited content. Throws MediaBlockedError.
  const verdict = moderateMediaPrompt(inp.prompt ?? '', { hasImages: !!inp.inputB64, kind: inp.kind });
  if (!verdict.allowed) throw new MediaBlockedError(verdict);
  await sweep();
  const params: Record<string, unknown> = { ...(inp.params ?? {}) };
  const referenceId = typeof params.reference_upload_id === 'string' ? params.reference_upload_id : null;
  // Clients may (reasonably) also list the mastering reference in upload_ids so the job
  // owns it — the reference is its own slot, so drop it from the main-input list.
  const uploadIds = (Array.isArray(params.upload_ids) ? (params.upload_ids as string[]) : [])
    .filter((s) => typeof s === 'string' && s !== referenceId)
    .slice(0, 4);
  params.upload_ids = uploadIds;
  if (UPLOAD_KINDS.has(inp.kind)) {
    await checkUploads(inp.kind, uploadIds, inp.kind === 'audio_master' ? referenceId : null);
  }
  // Studio kinds carry user files — always private-input semantics (wipe on terminal).
  const isPrivate = !!inp.isPrivate || UPLOAD_KINDS.has(inp.kind);
  const ttl = isPrivate ? MEDIA_PRIVATE_TTL_SECS : MEDIA_JOB_TTL_SECS;

  // ── render_blender: parent (WAITING, never claimable) + chunk children ──
  if (inp.kind === 'render_blender') {
    const fs = clampInt(params.frame_start, 1, 1_000_000, 1);
    const fe = clampInt(params.frame_end, fs, 1_000_000, fs);
    const step = clampInt(params.frame_step, 1, 10, 1);
    const totalFrames = Math.floor((fe - fs) / step) + 1;
    if (totalFrames > MEDIA_RENDER_MAX_FRAMES) {
      throw new UploadRefError('too_many_frames', `at most ${MEDIA_RENDER_MAX_FRAMES} frames per job (asked for ${totalFrames})`);
    }
    const output = totalFrames === 1 ? 'frames' : (params.output === 'frames' ? 'frames' : 'mp4');
    const shared = {
      upload_id: uploadIds[0],
      engine: 'CYCLES',
      frame_step: step,
      resolution_percent: clampInt(params.resolution_percent, 25, 200, 100),
      fps: clampInt(params.fps, 6, 60, 24),
      ...(params.samples != null ? { samples: clampInt(params.samples, 16, 2048, 128) } : {}),
    };
    const parent = await prisma.mediaJob.create({
      data: {
        kind: 'render_blender', status: 'WAITING',
        prompt: '', paramsJson: JSON.stringify({ ...shared, frame_start: fs, frame_end: fe, output, total_frames: totalFrames }),
        isPrivate: true, priority: inp.priority ?? 0,
        requesterIp: inp.requesterIp ?? null, requesterAcct: inp.requesterAcct ?? null,
        expiresAt: new Date(Date.now() + ttl * 1000),
      },
      select: { id: true, kind: true, status: true, createdAt: true },
    });
    const chunks: { s: number; e: number }[] = [];
    for (let s = fs; s <= fe; s += MEDIA_RENDER_CHUNK_FRAMES * step) {
      chunks.push({ s, e: Math.min(fe, s + (MEDIA_RENDER_CHUNK_FRAMES - 1) * step) });
    }
    await prisma.mediaJob.createMany({
      data: chunks.map((c) => ({
        kind: 'render_chunk', status: 'PENDING', prompt: '',
        paramsJson: JSON.stringify({ ...shared, frame_start: c.s, frame_end: c.e }),
        isPrivate: true, priority: inp.priority ?? 0, parentId: parent.id,
        requesterIp: inp.requesterIp ?? null,
        expiresAt: new Date(Date.now() + ttl * 1000),
      })),
    });
    await consumeUploads([uploadIds[0]], parent.id, new Date(Date.now() + (ttl + 3600) * 1000));
    return { ...parent, position: 1, minersOnline: await onlineMinerCount('render_chunk'), chunks: chunks.length };
  }

  // Image fidelity pass (deterministic, no model): compile the human request into what the
  // renderer needs — instruction wrappers stripped, negations moved to negative_prompt, a
  // machine-readable spec for the miner's reranker, /64-snapped size, recorded seed. The
  // moderation verdict above was taken on the RAW prompt; the raw text is kept in params.
  let prompt = (inp.prompt ?? '').slice(0, 4000);
  let compiled: ReturnType<typeof compileImagePrompt> | null = null;
  if (inp.kind === 'image') {
    compiled = compileImagePrompt(prompt, { negative: typeof params.negative_prompt === 'string' ? params.negative_prompt : null });
    params.prompt_raw = prompt;
    if (compiled.prompt) prompt = compiled.prompt;
    params.negative_prompt = compiled.negative || undefined;
    params.spec = compiled.spec;
    params.compiled = { est_tokens: compiled.est_tokens, truncation_risk: compiled.truncation_risk, notes: compiled.notes };
    params.seed = pickSeed(params.seed);
    params.precision = normalizePrecision(params.precision);
    const snapped = snapImageSize(Number(params.width) || 512, Number(params.height) || 512);
    params.width = snapped.width;
    params.height = snapped.height;
    // Optional renderer hints (11.1.0+ miners honor them; older miners ignore unknown keys).
    params.candidates = params.candidates != null ? clampInt(params.candidates, 1, 8, 0) || undefined : undefined;
    params.steps = params.steps != null ? clampInt(params.steps, 1, 60, 0) || undefined : undefined;
    params.guidance = Number.isFinite(Number(params.guidance)) && params.guidance != null ? Math.max(0, Math.min(20, Number(params.guidance))) : undefined;
  }
  // Video kinds get the same compile pass (negations → negative prompt, spec), a recorded
  // seed, precision and an optional engine pin (11.1.0 director: t2v | keyframe | parallax).
  const VIDEO_GEN_KINDS = new Set(['video_t2v', 'video_multiscene', 'video_i2v']);
  if (VIDEO_GEN_KINDS.has(inp.kind)) {
    if (prompt.trim()) {
      compiled = compileImagePrompt(prompt, { negative: typeof params.negative_prompt === 'string' ? params.negative_prompt : null });
      params.prompt_raw = prompt;
      if (compiled.prompt && inp.kind !== 'video_multiscene') prompt = compiled.prompt;
      params.negative_prompt = compiled.negative || undefined;
      params.spec = compiled.spec;
      params.compiled = { est_tokens: compiled.est_tokens, truncation_risk: compiled.truncation_risk, notes: compiled.notes };
    }
    params.seed = pickSeed(params.seed);
    params.precision = normalizePrecision(params.precision);
    const eng = typeof params.engine === 'string' ? params.engine.toLowerCase() : '';
    params.engine = ['t2v', 'keyframe', 'parallax'].includes(eng) ? eng : undefined;
    if (inp.kind !== 'video_i2v') {
      const snapped = snapImageSize(Number(params.width) || 768, Number(params.height) || 432, 128, 1280);
      params.width = snapped.width;
      params.height = snapped.height;
    }
  }
  // Web access (default on): reference photos of the subject for the miner's fidelity judge.
  // The metasearch takes ~3 s, so it runs AFTER the row exists and attaches to the job only
  // while it is still PENDING (a miner that already claimed it simply renders without refs).
  const wantRefs = (inp.kind === 'image' || VIDEO_GEN_KINDS.has(inp.kind)) && !!prompt.trim() && webRefsEnabled(params) && !verdict.adult;
  if (wantRefs) params.web = { query: null, pending: true };
  if (verdict.adult) params.adult = true;

  // ── Distributed video (11.1.0): plan shots here, queue each as its own video_shot job so
  // several miners render in parallel, then a video_assemble job joins them. The parent row
  // keeps the client-facing kind (video_t2v / video_multiscene) and sits in WAITING.
  // Only split when a miner that can take shots is actually online — otherwise the classic
  // single job (which pre-11.1 miners render end-to-end) is the one that goes through.
  const distributed = (inp.kind === 'video_t2v' || inp.kind === 'video_multiscene')
    && process.env.MEDIA_VIDEO_DISTRIBUTED !== '0' && params.distributed !== false
    && (await onlineMinerCount('video_shot')) > 0;
  if (distributed) {
    const fps = clampInt(params.fps, 6, 60, 24);
    const seconds = inp.kind === 'video_multiscene'
      ? Math.max(1, Math.min(Number(params.seconds_per_scene) || 2.5, 8)) * Math.max(1, Array.isArray(params.scenes) ? params.scenes.length : 1)
      : Math.max(1, Math.min(Number(params.seconds) || 4, 60));
    const shots = planShots(prompt, seconds, {
      scenes: inp.kind === 'video_multiscene' && Array.isArray(params.scenes) ? (params.scenes as string[]) : null,
      transition: typeof params.transition === 'string' ? params.transition : 'fade',
      seed: Number(params.seed) || 0,
    });
    if (shots.length >= 2) {
      const parent = await prisma.mediaJob.create({
        data: {
          kind: inp.kind, status: 'WAITING', prompt,
          paramsJson: JSON.stringify({ ...params, fps, seconds, distributed: true, shots }),
          isPrivate, priority: inp.priority ?? 0,
          requesterIp: inp.requesterIp ?? null, requesterAcct: inp.requesterAcct ?? null,
          expiresAt: new Date(Date.now() + ttl * 1000),
        },
        select: { id: true, kind: true, status: true, createdAt: true },
      });
      const shotParams = (sh: (typeof shots)[number]) => ({
        parent_id: parent.id, shot: sh, shot_index: sh.index, shots_total: shots.length,
        width: params.width, height: params.height, fps,
        tier: params.tier, precision: params.precision, engine: params.engine,
        negative_prompt: params.negative_prompt, references: params.references, web: params.web,
        seed: sh.seed,
      });
      await prisma.mediaJob.createMany({
        data: shots.map((sh) => ({
          kind: 'video_shot', status: 'PENDING', prompt: sh.prompt,
          paramsJson: JSON.stringify(shotParams(sh)),
          isPrivate: true, priority: inp.priority ?? 0, parentId: parent.id,
          requesterIp: inp.requesterIp ?? null,
          expiresAt: new Date(Date.now() + ttl * 1000),
        })),
      });
      if (wantRefs) {
        // References attach to the PENDING shots (the parent is never claimed).
        void (async () => {
          let refs: Awaited<ReturnType<typeof findReferenceImages>> = null;
          try { refs = await findReferenceImages(prompt); } catch { refs = null; }
          if (!refs || !refs.urls.length) return;
          const kids = await prisma.mediaJob.findMany({ where: { parentId: parent.id, kind: 'video_shot', status: 'PENDING' }, select: { id: true, paramsJson: true } });
          for (const k of kids) {
            let kp: any = {};
            try { kp = JSON.parse(k.paramsJson || '{}'); } catch { /* ignore */ }
            kp.references = refs.urls; kp.web = { query: refs.query, sources: refs.sources };
            await prisma.mediaJob.updateMany({ where: { id: k.id, status: 'PENDING' }, data: { paramsJson: JSON.stringify(kp) } }).catch(() => {});
          }
        })().catch(() => {});
      }
      return {
        ...parent, position: 1, minersOnline: await onlineMinerCount('video_shot'), shots: shots.length,
        ...(compiled ? { compiled: { prompt: compiled.prompt, negative: compiled.negative || undefined, seed: params.seed, notes: compiled.notes, truncation_risk: compiled.truncation_risk } } : {}),
      };
    }
  }

  const job = await prisma.mediaJob.create({
    data: {
      kind: inp.kind,
      prompt,
      paramsJson: JSON.stringify(params),
      inputB64: inp.inputB64 ?? null,
      isPrivate,
      priority: inp.priority ?? 0,
      requesterIp: inp.requesterIp ?? null,
      requesterAcct: inp.requesterAcct ?? null,
      expiresAt: new Date(Date.now() + ttl * 1000),
    },
    select: { id: true, kind: true, status: true, createdAt: true },
  });
  if (uploadIds.length) {
    await consumeUploads([...uploadIds, ...(referenceId ? [referenceId] : [])], job.id, new Date(Date.now() + (ttl + 3600) * 1000));
  }
  if (wantRefs) {
    void attachReferences(job.id, prompt, params).catch(() => {});
  }
  const position = await queuePosition(job.id, inp.kind);
  return {
    ...job,
    position,
    minersOnline: await onlineMinerCount(inp.kind),
    ...(compiled
      ? { compiled: { prompt: compiled.prompt, negative: compiled.negative || undefined, seed: params.seed, notes: compiled.notes, truncation_risk: compiled.truncation_risk } }
      : {}),
  };
}

// Consuming an upload binds it to the job AND extends its life to outlast the job (jobs
// legitimately wait in the queue "until a miner comes online" — an input that expires on
// its own 4h clock mid-wait would doom every long-queued studio job). onJobDone later
// SHORTENS it back to the grace window.
async function consumeUploads(ids: string[], jobId: string, minExpiry: Date) {
  await prisma.mediaUpload.updateMany({
    where: { id: { in: ids } },
    data: { consumedByJobId: jobId },
  });
  await prisma.mediaUpload.updateMany({
    where: { id: { in: ids }, expiresAt: { lt: minExpiry } },
    data: { expiresAt: minExpiry },
  });
}

// Look up reference photos for a freshly queued job and attach them while it is still
// PENDING. Never throws; a claimed job keeps the params the miner already received.
async function attachReferences(jobId: string, prompt: string, params: Record<string, unknown>): Promise<void> {
  let refs: Awaited<ReturnType<typeof findReferenceImages>> = null;
  try { refs = await findReferenceImages(prompt); } catch { refs = null; }
  const next: Record<string, unknown> = { ...params };
  if (refs) {
    next.web = { query: refs.query, sources: refs.sources };
    if (refs.urls.length) next.references = refs.urls;
  } else {
    next.web = { query: null, sources: [], unavailable: true };
  }
  await prisma.mediaJob.updateMany({
    where: { id: jobId, status: 'PENDING' },
    data: { paramsJson: JSON.stringify(next) },
  }).catch(() => {});
}

// What an image job is actually being rendered as (compiled prompt, seed, size). Public:
// none of it is private input, and it is what makes a result reproducible.
function imageRenderInfo(kind: string, prompt: string, paramsJson: string | null): Record<string, unknown> {
  if (!['image', 'video_t2v', 'video_multiscene', 'video_i2v'].includes(kind)) return {};
  let p: any = {};
  try { p = paramsJson ? JSON.parse(paramsJson) : {}; } catch { /* ignore */ }
  return {
    render: {
      prompt,
      negative_prompt: p.negative_prompt ?? undefined,
      seed: p.seed ?? undefined,
      width: p.width ?? undefined,
      height: p.height ?? undefined,
      precision: p.precision ?? undefined,
      engine: p.engine ?? undefined,
      references: Array.isArray(p.references) ? p.references.length : undefined,
      web_query: p.web?.query ?? undefined,
      adult: p.adult === true ? true : undefined,
      truncation_risk: p.compiled?.truncation_risk ?? undefined,
      notes: p.compiled?.notes ?? undefined,
    },
  };
}

function clampInt(v: unknown, lo: number, hi: number, d: number): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return d;
  return Math.max(lo, Math.min(hi, Math.round(n)));
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
    progressPct: job.progressPct ?? undefined,
    progressNote: job.progressNote ?? undefined,
    ...imageRenderInfo(job.kind, job.prompt, job.paramsJson),
  };

  // WAITING parent (render_blender / distributed video): aggregate its children into one
  // honest progress view.
  if (job.status === 'WAITING') {
    const childKind = job.kind === 'render_blender' ? 'render_chunk' : 'video_shot';
    const [total, done, running] = await Promise.all([
      prisma.mediaJob.count({ where: { parentId: job.id, kind: childKind } }),
      prisma.mediaJob.count({ where: { parentId: job.id, kind: childKind, status: 'DONE' } }),
      prisma.mediaJob.count({ where: { parentId: job.id, kind: childKind, status: 'RUNNING' } }),
    ]);
    const assembling = job.progressNote === 'assemble_queued';
    const pct = total > 0 ? Math.min(95, Math.round((done / total) * 90) + (assembling ? 3 : 0)) : 0;
    return {
      ...base,
      progressPct: pct,
      progressNote: assembling ? 'assembling final output' : `rendering (${running} ${childKind === 'video_shot' ? 'shot' : 'chunk'}${running === 1 ? '' : 's'} on miners, ${done}/${total} done)`,
      chunks_total: total,
      chunks_done: done,
      position: 0,
      minersOnline: await onlineMinerCount(childKind),
      result: null,
    };
  }

  if (job.status !== 'DONE') {
    const position = job.status === 'PENDING' ? await queuePosition(job.id, job.kind) : 0;
    const minersOnline = await onlineMinerCount(job.kind === 'render_blender' ? 'render_chunk' : job.kind);
    return { ...base, position, minersOnline, result: null };
  }

  // DONE — deliver the result.
  let meta: any = undefined;
  try { meta = job.resultMeta ? JSON.parse(job.resultMeta) : undefined; } catch { /* ignore */ }

  // 9.0.0 disk artifacts: serve a download URL (streaming beats a giant JSON b64). These
  // are NOT once-wiped even when private — a 500 MB download can legitimately fail halfway
  // — they die at TTL instead (private 6h). The classic inline-b64 privacy contract below
  // is unchanged.
  if (job.resultPath) {
    if (!job.deliveredAt) {
      await prisma.mediaJob.update({ where: { id: job.id }, data: { deliveredAt: new Date() } }).catch(() => {});
    }
    return {
      ...base,
      position: 0,
      minersOnline: await onlineMinerCount(job.kind === 'render_blender' ? 'render_chunk' : job.kind),
      // result_url duplicated at the top level — that is the shape the public API docs
      // advertise; result.url stays for clients reading the nested form.
      result_url: `/api/mkt/v1/media/artifacts/${job.id}`,
      result: {
        b64: null,
        url: `/api/mkt/v1/media/artifacts/${job.id}`,
        bytes: job.resultBytes != null ? Number(job.resultBytes) : undefined,
        mime: job.resultMime, sha3: job.resultSha3, meta,
      },
    };
  }

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
  const caps = (opts.capabilities || []).filter((c) => CLAIMABLE_KINDS.includes(c as MediaKind));
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
    select: { id: true, label: true, capabilities: true, device: true, online: true, jobsDone: true, rewardNanm: true, settledNanm: true },
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
  const caps = capabilities.filter((c) => CLAIMABLE_KINDS.includes(c as MediaKind));
  if (caps.length === 0) return null;

  // Per-kind lease, decided IN the claim statement (heavy studio kinds run long; the
  // progress heartbeat extends further). The CASE mirrors leaseSecsFor().
  const rows = await prisma.$queryRaw<Array<any>>(Prisma.sql`
    UPDATE "MediaJob" SET
      status = 'RUNNING',
      "claimedById" = ${minerId},
      "leaseUntil" = now() + make_interval(secs => CASE
        WHEN kind IN ('video_upscale','video_interpolate','video_subtitles','video_bgremove','video_shorts') THEN 900
        WHEN kind IN ('audio_stems','audio_isolate') THEN 600
        WHEN kind = 'render_chunk' THEN 1200
        WHEN kind = 'render_assemble' THEN 900
        ELSE ${MEDIA_LEASE_SECS} END),
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

  // Studio kinds: hand the miner download URLs for its inputs (fetched with its bearer
  // token). render_assemble gets the chunk artifacts (public-by-unguessable-job-id route).
  const inputUrls: string[] = [];
  if (typeof params.upload_id === 'string') inputUrls.push(`/api/mkt/v1/media/uploads/${params.upload_id}`);
  if (Array.isArray(params.upload_ids)) for (const id of params.upload_ids) if (typeof id === 'string') inputUrls.push(`/api/mkt/v1/media/uploads/${id}`);
  if (typeof params.reference_upload_id === 'string') inputUrls.push(`/api/mkt/v1/media/uploads/${params.reference_upload_id}`);
  if ((r.kind === 'render_assemble' && Array.isArray(params.chunk_jobs)) || (r.kind === 'video_assemble' && Array.isArray(params.shot_jobs))) {
    for (const id of (params.chunk_jobs ?? params.shot_jobs)) if (typeof id === 'string') inputUrls.push(`/api/mkt/v1/media/artifacts/${id}`);
  }
  return {
    id: r.id, kind: r.kind, prompt: r.prompt, params,
    inputB64: r.inputB64 ?? null, isPrivate: r.isPrivate, attempts: r.attempts,
    input_urls: inputUrls.length ? inputUrls : undefined,
  };
}

// ── Progress heartbeat (miner) ────────────────────────────────────────────────
// Stores live progress for the requester's poll AND extends the lease — a long render that
// keeps reporting is never swept out from under the miner.
export async function postProgress(minerId: string, jobId: string, pct: number, note?: string) {
  const p = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  const job = await prisma.mediaJob.findUnique({ where: { id: jobId }, select: { claimedById: true, status: true, kind: true } });
  if (!job) return { ok: false, code: 'not_found' as const };
  if (job.claimedById !== minerId) return { ok: false, code: 'not_owner' as const };
  if (job.status !== 'RUNNING') return { ok: false, code: 'not_running' as const };
  await prisma.mediaJob.update({
    where: { id: jobId },
    data: {
      progressPct: p,
      progressNote: (note || '').slice(0, 200) || null,
      leaseUntil: new Date(Date.now() + leaseSecsFor(job.kind) * 1000),
    },
  });
  await prisma.mediaMiner.update({ where: { id: minerId }, data: { lastSeenAt: new Date(), online: true } }).catch(() => {});
  return { ok: true };
}

// ── Failure path shared by both result routes ─────────────────────────────────
// Every kind gets server-side retries: a reported failure requeues the job until
// attempts run out, and only then hard-fails (for render children the sweep then
// fails the parent + cancels siblings). Miner errors are frequently host-specific
// (model-load OOM, broken deps) — another miner often completes the same job.
async function failJob(job: { id: string; kind: string; attempts: number }, error: string) {
  if (job.attempts < MEDIA_MAX_ATTEMPTS) {
    await prisma.mediaJob.update({
      where: { id: job.id },
      data: { status: 'PENDING', claimedById: null, leaseUntil: null, progressPct: null, progressNote: null, error: error.slice(0, 400) },
    });
    return { ok: true as const, terminal: 'RETRY' as const };
  }
  await prisma.mediaJob.update({
    where: { id: job.id },
    data: { status: 'FAILED', error: error.slice(0, 400), inputB64: null },
  });
  await sweep(true); // propagate to a WAITING parent promptly
  return { ok: true as const, terminal: 'FAILED' as const };
}

// ── Render-farm orchestration after a successful child ───────────────────────
// When the LAST chunk of a parent completes → enqueue ONE render_assemble (optimistic
// progressNote guard wins any race between concurrently-finishing chunks). Single-chunk
// parents (or frames output with one chunk) complete directly from the chunk artifact.
// When an assemble completes → the parent takes over its artifact.
async function onJobDone(jobId: string) {
  const job = await prisma.mediaJob.findUnique({
    where: { id: jobId },
    select: { id: true, kind: true, parentId: true, resultPath: true, resultBytes: true, resultMime: true, resultSha3: true, resultMeta: true },
  });
  if (!job || !job.parentId) {
    // A directly-submitted studio job finished: shorten its uploads' TTL to the grace window.
    if (job) await expireJobUploads(job.id);
    return;
  }
  const parent = await prisma.mediaJob.findUnique({
    where: { id: job.parentId },
    select: { id: true, status: true, paramsJson: true },
  });
  if (!parent || parent.status !== 'WAITING') return;
  let pparams: any = {};
  try { pparams = JSON.parse(parent.paramsJson || '{}'); } catch { /* ignore */ }

  if (job.kind === 'render_assemble' || job.kind === 'video_assemble') {
    await promoteChildArtifact(parent.id, job);
    return;
  }
  if (job.kind === 'video_shot') {
    const [total, done] = await Promise.all([
      prisma.mediaJob.count({ where: { parentId: parent.id, kind: 'video_shot' } }),
      prisma.mediaJob.count({ where: { parentId: parent.id, kind: 'video_shot', status: 'DONE' } }),
    ]);
    await prisma.mediaJob.updateMany({
      where: { id: parent.id, status: 'WAITING', OR: [{ progressNote: null }, { progressNote: { not: 'assemble_queued' } }] },
      data: { progressPct: Math.round(85 * done / Math.max(1, total)), progressNote: `${done}/${total} shots rendered` },
    }).catch(() => {});
    if (done < total) return;
    const won = await prisma.mediaJob.updateMany({
      where: { id: parent.id, status: 'WAITING', OR: [{ progressNote: null }, { progressNote: { not: 'assemble_queued' } }] },
      data: { progressNote: 'assemble_queued', progressPct: 90 },
    });
    if (won.count === 0) return;
    const shotRows = await prisma.mediaJob.findMany({
      where: { parentId: parent.id, kind: 'video_shot', status: 'DONE' },
      select: { id: true, paramsJson: true },
    });
    const ordered = shotRows
      .map((r) => { let p: any = {}; try { p = JSON.parse(r.paramsJson || '{}'); } catch { /* ignore */ } return { id: r.id, index: Number(p.shot_index ?? 0), shot: p.shot }; })
      .sort((a, b) => a.index - b.index);
    await prisma.mediaJob.create({
      data: {
        kind: 'video_assemble', status: 'PENDING', prompt: '',
        paramsJson: JSON.stringify({
          parent_id: parent.id, shot_jobs: ordered.map((o) => o.id),
          shots: ordered.map((o) => o.shot), fps: pparams.fps ?? 24,
          width: pparams.width, height: pparams.height,
          transition: typeof pparams.transition === 'string' ? pparams.transition : 'fade',
        }),
        isPrivate: true, priority: 5, parentId: parent.id, requesterIp: null,
        expiresAt: new Date(Date.now() + MEDIA_PRIVATE_TTL_SECS * 1000),
      },
    });
    return;
  }
  if (job.kind !== 'render_chunk') return;

  const [total, done] = await Promise.all([
    prisma.mediaJob.count({ where: { parentId: parent.id, kind: 'render_chunk' } }),
    prisma.mediaJob.count({ where: { parentId: parent.id, kind: 'render_chunk', status: 'DONE' } }),
  ]);
  if (done < total) return;

  // All chunks done. Win the enqueue race via a conditional parent update.
  // NB: Prisma's `{ not: v }` never matches NULL rows — progressNote starts NULL, so the
  // null case must be matched explicitly or the guard silently never fires.
  const won = await prisma.mediaJob.updateMany({
    where: {
      id: parent.id, status: 'WAITING',
      OR: [{ progressNote: null }, { progressNote: { not: 'assemble_queued' } }],
    },
    data: { progressNote: 'assemble_queued', progressPct: 90 },
  });
  if (won.count === 0) return;

  const chunkIds = (await prisma.mediaJob.findMany({
    where: { parentId: parent.id, kind: 'render_chunk', status: 'DONE' },
    orderBy: { createdAt: 'asc' },
    select: { id: true },
  })).map((c) => c.id);

  if (chunkIds.length === 1 && (pparams.output === 'frames' || pparams.total_frames === 1)) {
    // Nothing to assemble — the chunk's zip IS the deliverable.
    const only = await prisma.mediaJob.findUnique({
      where: { id: chunkIds[0] },
      select: { id: true, kind: true, parentId: true, resultPath: true, resultBytes: true, resultMime: true, resultSha3: true, resultMeta: true },
    });
    if (only) await promoteChildArtifact(parent.id, only);
    return;
  }
  await prisma.mediaJob.create({
    data: {
      kind: 'render_assemble', status: 'PENDING', prompt: '',
      paramsJson: JSON.stringify({
        parent_id: parent.id, chunk_jobs: chunkIds,
        mode: pparams.output === 'frames' ? 'zip' : 'mp4',
        fps: pparams.fps ?? 24,
      }),
      isPrivate: true, priority: 5, // assembles jump the queue — the user already waited
      parentId: parent.id,
      requesterIp: null,
      expiresAt: new Date(Date.now() + MEDIA_PRIVATE_TTL_SECS * 1000),
    },
  });
}

// The parent takes ownership of a finished child's artifact (rename on disk so the child
// row can be wiped without touching the parent's file), then chunk artifacts are dropped.
async function promoteChildArtifact(parentId: string, child: { id: string; resultPath: string | null; resultBytes: bigint | null; resultMime: string | null; resultSha3: string | null; resultMeta: string | null }) {
  const { artifactPath, removeFile } = await import('./mediaStore');
  const { rename } = await import('node:fs/promises');
  let newPath: string | null = null;
  if (child.resultPath) {
    newPath = artifactPath(parentId);
    try { await rename(child.resultPath, newPath); } catch { newPath = child.resultPath; }
  }
  await prisma.mediaJob.update({
    where: { id: parentId },
    data: {
      status: 'DONE', progressPct: 100, progressNote: null, error: null,
      resultPath: newPath, resultBytes: child.resultBytes,
      resultMime: child.resultMime, resultSha3: child.resultSha3, resultMeta: child.resultMeta,
    },
  });
  await prisma.mediaJob.update({ where: { id: child.id }, data: { resultPath: null } }).catch(() => {});
  // Chunk artifacts served their purpose — free the disk now instead of at TTL.
  const chunks = await prisma.mediaJob.findMany({
    where: { parentId, kind: { in: ['render_chunk', 'video_shot'] }, resultPath: { not: null } },
    select: { id: true, resultPath: true },
  });
  for (const c of chunks) {
    await removeFile(c.resultPath);
    await prisma.mediaJob.update({ where: { id: c.id }, data: { resultPath: null } }).catch(() => {});
  }
  await expireJobUploads(parentId);
}

// After success, an upload only needs to survive a short grace window (quick re-runs);
// the sweep unlinks it when that passes.
async function expireJobUploads(jobId: string) {
  await prisma.mediaUpload.updateMany({
    where: { consumedByJobId: jobId, expiresAt: { gt: new Date(Date.now() + MEDIA_UPLOAD_DONE_GRACE_SECS * 1000) } },
    data: { expiresAt: new Date(Date.now() + MEDIA_UPLOAD_DONE_GRACE_SECS * 1000) },
  }).catch(() => {});
}

// ── Post result (miner, inline base64 — the classic path) ────────────────────────
export async function postResult(minerId: string, jobId: string, res: { ok: boolean; b64?: string; mime?: string; sha3?: string; meta?: any; error?: string }) {
  const job = await prisma.mediaJob.findUnique({ where: { id: jobId }, select: { id: true, kind: true, claimedById: true, status: true, isPrivate: true, attempts: true } });
  if (!job) return { ok: false as const, code: 'not_found' as const };
  if (job.claimedById !== minerId) {
    // Late delivery: the lease expired and the sweep requeued the job while the miner
    // was still rendering. Adopt a successful result instead of discarding finished
    // work — only for a genuinely unclaimed PENDING job, and only the success path
    // (a late failure report must not consume an attempt another miner could use).
    const adoptable = job.status === 'PENDING' && job.claimedById === null && res.ok && !!res.b64;
    if (!adoptable) return { ok: false as const, code: 'not_owner' as const };
    const adopted = await prisma.mediaJob.updateMany({
      where: { id: jobId, status: 'PENDING', claimedById: null },
      data: { claimedById: minerId, status: 'RUNNING', leaseUntil: new Date(Date.now() + 60_000) },
    });
    if (adopted.count !== 1) return { ok: false as const, code: 'not_owner' as const };
    job.claimedById = minerId;
    job.status = 'RUNNING';
  }
  if (job.status !== 'RUNNING') return { ok: false as const, code: 'not_running' as const };

  if (!res.ok || !res.b64) {
    return failJob(job, res.error || 'miner reported failure');
  }

  const bytes = Buffer.from(res.b64, 'base64');
  const digest = sha3(bytes);
  // Conditional RUNNING→DONE inside the transaction: two concurrent posts (double-submit,
  // retry after a slow response) can BOTH pass the status pre-check above — only the one
  // that actually flips the row may credit the reward, or a hostile miner mints IOUs.
  const won = await prisma.$transaction(async (tx) => {
    const flip = await tx.mediaJob.updateMany({
      where: { id: jobId, status: 'RUNNING', claimedById: minerId },
      data: {
        status: 'DONE',
        resultB64: res.b64,
        resultMime: res.mime || 'application/octet-stream',
        resultSha3: res.sha3 || digest,
        resultMeta: res.meta ? JSON.stringify(res.meta).slice(0, 2000) : null,
        progressPct: 100, progressNote: null,
        error: null,
        inputB64: null, // private inputs are done being needed — wipe them
      },
    });
    if (flip.count !== 1) return false;
    await tx.mediaMiner.update({
      where: { id: minerId },
      data: { jobsDone: { increment: 1 }, rewardNanm: { increment: MEDIA_REWARD_NANM[job.kind] ?? 0n }, lastSeenAt: new Date() },
    });
    return true;
  });
  if (!won) return { ok: false as const, code: 'not_running' as const };
  await onJobDone(jobId);
  return { ok: true as const, terminal: 'DONE' as const, sha3: res.sha3 || digest };
}

// ── Post result (miner, streamed disk artifact — 9.0.0 studio path) ───────────────
// The route streams the raw body to a temp artifact file (hashing as it goes) and then
// calls this with the verified file. Ownership/status re-checked here; on any rejection
// the caller unlinks the file.
export async function postResultFile(minerId: string, jobId: string, file: { path: string; bytes: number; sha3: string; mime: string; meta?: any }) {
  const job = await prisma.mediaJob.findUnique({ where: { id: jobId }, select: { id: true, kind: true, claimedById: true, status: true, attempts: true } });
  if (!job) return { ok: false as const, code: 'not_found' as const };
  if (job.claimedById !== minerId) {
    // Late delivery of a verified file — same adoption rule as postResult (this path
    // only ever carries a successful, hash-verified artifact).
    const adoptable = job.status === 'PENDING' && job.claimedById === null;
    if (!adoptable) return { ok: false as const, code: 'not_owner' as const };
    const adopted = await prisma.mediaJob.updateMany({
      where: { id: jobId, status: 'PENDING', claimedById: null },
      data: { claimedById: minerId, status: 'RUNNING', leaseUntil: new Date(Date.now() + 60_000) },
    });
    if (adopted.count !== 1) return { ok: false as const, code: 'not_owner' as const };
    job.claimedById = minerId;
    job.status = 'RUNNING';
  }
  if (job.status !== 'RUNNING') return { ok: false as const, code: 'not_running' as const };

  // Same conditional-flip discipline as postResult — see the double-credit note there.
  const won = await prisma.$transaction(async (tx) => {
    const flip = await tx.mediaJob.updateMany({
      where: { id: jobId, status: 'RUNNING', claimedById: minerId },
      data: {
        status: 'DONE',
        resultB64: null,
        resultPath: file.path,
        resultBytes: BigInt(file.bytes),
        resultMime: file.mime || 'application/octet-stream',
        resultSha3: file.sha3,
        resultMeta: file.meta ? JSON.stringify(file.meta).slice(0, 2000) : null,
        progressPct: 100, progressNote: null,
        error: null,
        inputB64: null,
      },
    });
    if (flip.count !== 1) return false;
    await tx.mediaMiner.update({
      where: { id: minerId },
      data: { jobsDone: { increment: 1 }, rewardNanm: { increment: MEDIA_REWARD_NANM[job.kind] ?? 0n }, lastSeenAt: new Date() },
    });
    return true;
  });
  if (!won) return { ok: false as const, code: 'not_running' as const };
  await onJobDone(jobId);
  return { ok: true as const, terminal: 'DONE' as const, sha3: file.sha3 };
}
