import { NextRequest, NextResponse } from 'next/server';
import { publicPreflight, PUBLIC_CORS } from '@/lib/api';
import { rateLimit } from '@/lib/apikey';
import { submitJob, pollJob, type MediaKind } from '@/lib/mediaQueue';
import { moderateMediaPrompt } from '@/lib/mediaModeration';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// OpenAI-compatible media facade backed by the queue. nginx maps:
//   /v1/images/generations -> /api/mkt/v1/media/openai/images/generations   (type = images)
//   /v1/videos/generations -> .../videos/generations                        (type = videos)
//   /v1/audio/generations  -> .../audio/generations                         (type = audio)
// It enqueues a job and LONG-POLLS for the miner's result (so a synchronous OpenAI-style
// call still returns the bytes inline). If no miner finishes it in time, it returns 202
// with a job_id + poll_url so the caller can keep polling — the job stays queued and GOES
// THROUGH EVENTUALLY. No model runs on this host.

const LONGPOLL_MS = Number(process.env.MEDIA_LONGPOLL_MS ?? 55_000);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0].trim();
  return req.headers.get('x-real-ip') || 'unknown';
}

function kindFor(type: string, body: any, hasImages: boolean): MediaKind | null {
  if (type === 'images') return 'image';
  if (type === 'audio') return 'audio';
  if (type === 'videos') {
    if (Array.isArray(body.scenes) && body.scenes.length > 1) return 'video_multiscene';
    if (body.mode === 'multiscene') return 'video_multiscene';
    return hasImages ? 'video_i2v' : 'video_t2v';
  }
  return null;
}

export function OPTIONS() {
  return publicPreflight();
}

export async function POST(req: NextRequest, { params }: { params: { type: string } }) {
  const type = params.type;
  const ip = clientIp(req);
  const rl = rateLimit('media:' + ip, Number(process.env.MEDIA_SUBMIT_PER_MIN ?? 12));
  if (!rl.ok) {
    return NextResponse.json({ error: { code: 'rate_limited', message: `retry in ${rl.retryAfter}s` } }, { status: 429, headers: { ...PUBLIC_CORS, 'retry-after': String(rl.retryAfter) } });
  }

  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: { code: 'bad_json', message: 'invalid JSON' } }, { status: 400, headers: PUBLIC_CORS }); }

  let images: string[] = [];
  if (typeof body.image === 'string') images = [body.image];
  if (Array.isArray(body.images)) images = body.images.filter((s: any) => typeof s === 'string');
  images = images.slice(0, 6);
  const hasImages = images.length > 0;

  const kind = kindFor(type, body, hasImages);
  if (!kind) return NextResponse.json({ error: { code: 'bad_type', message: 'unknown media type' } }, { status: 404, headers: PUBLIC_CORS });

  const prompt = typeof body.prompt === 'string' ? body.prompt : '';
  if (kind !== 'video_i2v' && !prompt.trim()) {
    return NextResponse.json({ error: { code: 'need_prompt', message: 'prompt required' } }, { status: 400, headers: PUBLIC_CORS });
  }

  const verdict = moderateMediaPrompt(prompt, { hasImages, kind });
  if (!verdict.allowed) {
    console.warn(`[media/openai] blocked ${verdict.category} from ${ip} (${verdict.matched ?? '?'}) kind=${kind} imgs=${hasImages}`);
    return NextResponse.json(
      { error: { code: verdict.code, category: verdict.category, message: verdict.message } },
      { status: 422, headers: PUBLIC_CORS },
    );
  }

  const isPrivate = kind === 'video_i2v';
  const params2: Record<string, unknown> = {
    tier: body.tier, width: body.width, height: body.height, fps: body.fps,
    seconds: body.seconds, seconds_per_scene: body.seconds_per_scene, transition: body.transition,
    seed: body.seed, negative_prompt: body.negative_prompt, scenes: body.scenes,
  };

  const job = await submitJob({ kind, prompt, params: params2, inputB64: hasImages ? JSON.stringify(images) : null, isPrivate, requesterIp: ip });

  // Only hold the connection open (long-poll) while a miner is actually online to serve it.
  // With no miner connected, return the queued reference immediately — the job still stays
  // enqueued and renders when a miner comes online (poll poll_url).
  const deadline = job.minersOnline > 0 ? Date.now() + LONGPOLL_MS : 0;
  while (Date.now() < deadline) {
    await sleep(1500);
    const st = await pollJob(job.id);
    if (!st) break;
    if (st.status === 'DONE' && st.result?.b64) {
      const created = Math.floor(job.createdAt.getTime() / 1000);
      const mime = st.result.mime || (kind === 'image' ? 'image/png' : kind === 'audio' ? 'audio/wav' : 'video/mp4');
      return NextResponse.json({
        created,
        model: (st.result.meta && (st.result.meta.model as string)) || `animica-${kind}`,
        data: [{ b64_json: st.result.b64, mime }],
        animica: { kind, sha3: st.result.sha3, mime, status: 'done', ...(st.result.meta || {}) },
      }, { headers: PUBLIC_CORS });
    }
    if (st.status === 'FAILED' || st.status === 'EXPIRED') {
      return NextResponse.json({ error: { code: 'generation_failed', message: st.error || 'render failed' } }, { status: 422, headers: PUBLIC_CORS });
    }
  }

  // Still queued — hand back a job reference so the caller keeps polling. It WILL render
  // once a miner is free (nothing is lost).
  const st = await pollJob(job.id);
  return NextResponse.json({
    status: 'queued',
    job_id: job.id,
    poll_url: `/api/mkt/v1/media/jobs/${job.id}`,
    position: st?.position ?? 0,
    miners_online: st?.minersOnline ?? 0,
    animica: { kind, status: 'queued', dispatch: 'miner' },
    message: (st?.minersOnline ?? 0) > 0
      ? 'Rendering on a GPU miner — poll poll_url for the result.'
      : 'Queued for a GPU miner. Poll poll_url — it renders as soon as a miner is online.',
  }, { status: 202, headers: PUBLIC_CORS });
}
