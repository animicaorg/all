import { NextRequest, NextResponse } from 'next/server';
import { publicOk, publicPreflight, PUBLIC_CORS } from '@/lib/api';
import { rateLimit } from '@/lib/apikey';
import { submitJob, onlineMinerCount, MEDIA_KINDS, type MediaKind } from '@/lib/mediaQueue';
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
  if (!MEDIA_KINDS.includes(k as MediaKind)) return null;
  return k as MediaKind;
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
    return NextResponse.json({ error: { code: 'bad_kind', message: `kind must be one of ${MEDIA_KINDS.join(', ')}.` } }, { status: 400, headers: PUBLIC_CORS });
  }

  const prompt = typeof body.prompt === 'string' ? body.prompt : '';
  if (kind === 'video_i2v' && !hasImages) {
    return NextResponse.json({ error: { code: 'need_images', message: 'image->video needs at least one uploaded image.' } }, { status: 400, headers: PUBLIC_CORS });
  }
  if (kind !== 'video_i2v' && !prompt.trim()) {
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
  const params: Record<string, unknown> = {
    tier: typeof p.tier === 'string' ? p.tier.slice(0, 24) : undefined,
    width: clampInt(p.width, 64, 1280, kind.startsWith('video') ? 768 : 512),
    height: clampInt(p.height, 64, 1280, kind.startsWith('video') ? 432 : 512),
    fps: clampInt(p.fps, 6, 30, 24),
    seconds: Math.max(1, Math.min(Number(p.seconds) || (kind === 'audio' ? 8 : 4), 20)),
    seconds_per_scene: Math.max(0.6, Math.min(Number(p.seconds_per_scene) || 2.5, 8)),
    transition: typeof p.transition === 'string' ? p.transition.slice(0, 16) : 'fade',
    seed: Number.isFinite(Number(p.seed)) ? Math.round(Number(p.seed)) : undefined,
    negative_prompt: typeof p.negative_prompt === 'string' ? p.negative_prompt.slice(0, 500) : undefined,
    scenes: Array.isArray(p.scenes) ? p.scenes.filter((s: any) => typeof s === 'string').slice(0, 8) : undefined,
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
    poll_url: `/api/mkt/v1/media/jobs/${job.id}`,
    message: job.minersOnline > 0
      ? 'Queued — a GPU miner is online and will render this shortly.'
      : 'Queued — waiting for a GPU miner to come online. It will render as soon as one is available.',
  }, { status: 202 });
}
