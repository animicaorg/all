import { NextRequest, NextResponse } from 'next/server';
import { publicOk, publicPreflight, PUBLIC_CORS } from '@/lib/api';
import { rateLimit } from '@/lib/apikey';
import { submitJob, onlineMinerCount, SUBMIT_KINDS, UPLOAD_KINDS, UploadRefError, type MediaKind } from '@/lib/mediaQueue';
import { moderateMediaPrompt } from '@/lib/mediaModeration';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0].trim();
  return req.headers.get('x-real-ip') || 'unknown';
}

// Total accepted request body (image uploads for i2v are the heavy case).
const MAX_BODY = 12 * 1024 * 1024;
const MAX_IMAGES = 6;

function normalizeKind(raw: any, mode: any, hasImages: boolean): MediaKind | null {
  let k = String(raw || '').trim();
  if (k === 'video' || k === 'video_t2v') k = hasImages ? 'video_i2v' : 'video_t2v';
  if (k === 'music' || k === 'song') k = 'audio';
  if (k === 'i2v') k = 'video_i2v';
  if (k === 'multiscene' || k === 'scenes') k = 'video_multiscene';
  if (mode === 'i2v') k = 'video_i2v';
  // 9.0.0 GPU-Studio friendly aliases
  if (k === 'upscale') k = 'video_upscale';
  if (k === 'interpolate' || k === 'smooth') k = 'video_interpolate';
  if (k === 'subtitles' || k === 'captions') k = 'video_subtitles';
  if (k === 'bgremove' || k === 'greenscreen') k = 'video_bgremove';
  if (k === 'shorts') k = 'video_shorts';
  if (k === 'stems') k = 'audio_stems';
  if (k === 'isolate' || k === 'vocals') k = 'audio_isolate';
  if (k === 'enhance') k = 'audio_enhance';
  if (k === 'master') k = 'audio_master';
  if (k === 'render' || k === 'blender') k = 'render_blender';
  if (!SUBMIT_KINDS.includes(k as MediaKind)) return null;
  return k as MediaKind;
}

// Per-kind param allowlist for the 9.0.0 studio kinds (unlisted params are dropped; the
// classic generative clamp block below stays untouched for the classic kinds).
function studioParams(kind: MediaKind, p: any, clampInt: (v: any, lo: number, hi: number, d: number) => number): Record<string, unknown> {
  const uploadIds = Array.isArray(p.upload_ids) ? p.upload_ids.filter((s: any) => typeof s === 'string').slice(0, 4) : [];
  const base: Record<string, unknown> = { upload_ids: uploadIds };
  switch (kind) {
    case 'video_upscale':
      return { ...base, scale: [2, 4].includes(Number(p.scale)) ? Number(p.scale) : 2, model: p.model === 'quality' ? 'quality' : 'fast' };
    case 'video_interpolate':
      return { ...base, factor: [2, 4].includes(Number(p.factor)) ? Number(p.factor) : 2 };
    case 'video_subtitles':
      return {
        ...base,
        language: typeof p.language === 'string' && /^[a-z]{2}$/i.test(p.language) ? p.language.toLowerCase() : 'auto',
        burn_in: p.burn_in !== false,
      };
    case 'video_bgremove':
      return { ...base, mode: p.mode === 'alpha' ? 'alpha' : 'green' };
    case 'video_shorts':
      return {
        ...base,
        count: clampInt(p.count, 1, 5, 3),
        duration: clampInt(p.duration, 10, 60, 30),
        aspect: ['9:16', '1:1', '16:9'].includes(p.aspect) ? p.aspect : '9:16',
        subtitles: p.subtitles !== false,
      };
    case 'audio_stems':
    case 'audio_isolate':
      return { ...base, format: p.format === 'wav' ? 'wav' : 'mp3' };
    case 'audio_enhance':
      return {
        ...base,
        denoise: p.denoise !== false,
        loudness: Math.max(-30, Math.min(-6, Number(p.loudness) || -16)),
        format: p.format === 'wav' ? 'wav' : 'mp3',
      };
    case 'audio_master':
      return {
        ...base,
        preset: ['streaming', 'loud', 'podcast'].includes(p.preset) ? p.preset : 'streaming',
        format: p.format === 'wav' ? 'wav' : 'mp3',
        reference_upload_id: typeof p.reference_upload_id === 'string' ? p.reference_upload_id : undefined,
      };
    case 'render_blender':
      return {
        ...base,
        frame_start: clampInt(p.frame_start, 1, 1_000_000, 1),
        frame_end: clampInt(p.frame_end, 1, 1_000_000, clampInt(p.frame_start, 1, 1_000_000, 1)),
        frame_step: clampInt(p.frame_step, 1, 10, 1),
        resolution_percent: clampInt(p.resolution_percent, 25, 200, 100),
        fps: clampInt(p.fps, 6, 60, 24),
        output: p.output === 'frames' ? 'frames' : 'mp4',
        ...(p.samples != null ? { samples: clampInt(p.samples, 16, 2048, 128) } : {}),
      };
    default:
      return base;
  }
}

export function OPTIONS() {
  return publicPreflight();
}

export async function POST(req: NextRequest) {
  const ip = clientIp(req);
  const rl = rateLimit('media:' + ip, Number(process.env.MEDIA_SUBMIT_PER_MIN ?? 12));
  if (!rl.ok) {
    return NextResponse.json(
      { error: { code: 'rate_limited', message: `Too many media jobs — retry in ${rl.retryAfter}s.` } },
      { status: 429, headers: { ...PUBLIC_CORS, 'retry-after': String(rl.retryAfter) } },
    );
  }

  const raw = await req.text();
  if (raw.length > MAX_BODY) {
    return NextResponse.json({ error: { code: 'too_large', message: 'Request too large (max 12MB, up to 6 images).' } }, { status: 413, headers: PUBLIC_CORS });
  }
  let body: any;
  try { body = JSON.parse(raw || '{}'); } catch { return NextResponse.json({ error: { code: 'bad_json', message: 'Invalid JSON body.' } }, { status: 400, headers: PUBLIC_CORS }); }

  // Images for i2v (kept private, miner<->user only). Accept data: URLs or bare base64.
  let images: string[] = Array.isArray(body.images) ? body.images : [];
  images = images.filter((s) => typeof s === 'string' && s.length > 0).slice(0, MAX_IMAGES);
  const hasImages = images.length > 0;

  const kind = normalizeKind(body.kind, body.mode, hasImages);
  if (!kind) {
    return NextResponse.json({ error: { code: 'bad_kind', message: `kind must be one of ${SUBMIT_KINDS.join(', ')}.` } }, { status: 400, headers: PUBLIC_CORS });
  }

  const prompt = typeof body.prompt === 'string' ? body.prompt : '';
  if (kind === 'video_i2v' && !hasImages) {
    return NextResponse.json({ error: { code: 'need_images', message: 'image->video needs at least one uploaded image.' } }, { status: 400, headers: PUBLIC_CORS });
  }
  // Studio kinds transform an uploaded file — no prompt needed. Classic generative kinds keep requiring one.
  if (kind !== 'video_i2v' && !UPLOAD_KINDS.has(kind) && !prompt.trim()) {
    return NextResponse.json({ error: { code: 'need_prompt', message: 'A prompt is required.' } }, { status: 400, headers: PUBLIC_CORS });
  }

  // Content-safety gate: reject prohibited prompts before queuing (see lib/mediaModeration).
  const verdict = moderateMediaPrompt(prompt, { hasImages, kind });
  if (!verdict.allowed) {
    console.warn(`[media] blocked ${verdict.category} from ${ip} (${verdict.matched ?? '?'}) kind=${kind} imgs=${hasImages}`);
    return NextResponse.json(
      { error: { code: verdict.code, category: verdict.category, message: verdict.message } },
      { status: 422, headers: PUBLIC_CORS },
    );
  }

  // Sanitize params — pass a compact, clamped set through to the miner.
  const p = body.params && typeof body.params === 'object' ? body.params : body;
  const clampInt = (v: any, lo: number, hi: number, d: number) => {
    const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.max(lo, Math.min(n, hi)) : d;
  };

  // 9.0.0 studio kinds: per-kind allowlist + disk-upload references instead of the
  // generative clamp block below. The contract puts upload_ids at the TOP level of the
  // body ({kind, params, upload_ids}) — accept it there AND inside params.
  if (UPLOAD_KINDS.has(kind)) {
    if (Array.isArray(body.upload_ids) && !Array.isArray(p.upload_ids)) {
      p.upload_ids = body.upload_ids;
    }
    try {
      const job = await submitJob({
        kind,
        prompt: '',
        params: studioParams(kind, p, clampInt),
        requesterIp: ip,
      });
      return publicOk({
        job_id: job.id,
        kind: job.kind,
        status: job.status,
        position: job.position,
        miners_online: job.minersOnline,
        chunks: (job as any).chunks,
        private: true,
        poll_url: `/api/mkt/v1/media/jobs/${job.id}`,
        message: job.minersOnline > 0
          ? 'Queued — a GPU miner is online and will process this shortly.'
          : 'Queued — waiting for a GPU miner to come online. It will process as soon as one is available.',
      }, { status: 202 });
    } catch (e) {
      if (e instanceof UploadRefError) {
        return NextResponse.json({ error: { code: e.code, message: e.message } }, { status: 400, headers: PUBLIC_CORS });
      }
      throw e;
    }
  }

  const params: Record<string, unknown> = {
    tier: typeof p.tier === 'string' ? p.tier.slice(0, 24) : undefined,
    width: clampInt(p.width, 64, 1280, kind.startsWith('video') ? 768 : 512),
    height: clampInt(p.height, 64, 1280, kind.startsWith('video') ? 432 : 512),
    fps: clampInt(p.fps, 6, 30, 24),
    seconds: Math.max(1, Math.min(Number(p.seconds) || (kind === 'audio' ? 8 : 4), kind === 'audio' ? 20 : 30)),
    seconds_per_scene: Math.max(0.6, Math.min(Number(p.seconds_per_scene) || 2.5, 8)),
    transition: typeof p.transition === 'string' ? p.transition.slice(0, 16) : 'fade',
    seed: Number.isFinite(Number(p.seed)) ? Math.round(Number(p.seed)) : undefined,
    negative_prompt: typeof p.negative_prompt === 'string' ? p.negative_prompt.slice(0, 500) : undefined,
    scenes: Array.isArray(p.scenes) ? p.scenes.filter((s: any) => typeof s === 'string').slice(0, 8) : undefined,
    // Image fidelity hints (validated + defaulted in submitJob's compile pass).
    precision: typeof p.precision === 'string' ? p.precision.slice(0, 12) : undefined,
    engine: typeof p.engine === 'string' ? p.engine.slice(0, 12) : undefined,
    web: p.web === false || p.web === 'off' ? false : undefined,
    candidates: p.candidates != null ? p.candidates : undefined,
    steps: p.steps != null ? p.steps : undefined,
    guidance: p.guidance != null ? p.guidance : undefined,
  };

  const isPrivate = kind === 'video_i2v'; // uploads path — private by construction
  const job = await submitJob({
    kind,
    prompt,
    params,
    inputB64: hasImages ? JSON.stringify(images) : null,
    isPrivate,
    requesterIp: ip,
  });

  return publicOk({
    job_id: job.id,
    kind: job.kind,
    status: job.status,
    position: job.position,
    miners_online: job.minersOnline,
    private: isPrivate,
    compiled: (job as any).compiled,
    poll_url: `/api/mkt/v1/media/jobs/${job.id}`,
    message: job.minersOnline > 0
      ? 'Queued — a GPU miner is online and will render this shortly.'
      : 'Queued — waiting for a GPU miner to come online. It will render as soon as one is available.',
  }, { status: 202 });
}
