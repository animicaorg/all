import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { generateImage } from '@/lib/media';
import { ok, err, ApiError } from '@/lib/api';
import { moderateMediaPrompt } from '@/lib/mediaModeration';

export const dynamic = 'force-dynamic';
export const maxDuration = 300;

// POST /api/mkt/v1/media/[slug]/preview { prompt } -> free, IP-rate-limited media preview (small).
const ipHits = new Map<string, { n: number; resetAt: number }>();
function limited(ip: string): boolean {
  const now = Date.now();
  const b = ipHits.get(ip);
  if (!b || now >= b.resetAt) { ipHits.set(ip, { n: 1, resetAt: now + 120_000 }); return false; }
  if (b.n >= 4) return true;
  b.n += 1; return false;
}

export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ip = req.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'local';
    if (limited(ip)) throw new ApiError(429, 'rate_limited', 'preview limit reached; buy access for full-res generation');
    const body = await req.json();
    const prompt = String(body.prompt ?? '').trim();
    if (!prompt) throw new ApiError(400, 'bad_request', 'prompt required');
    const verdict = moderateMediaPrompt(prompt, { kind: 'image' });
    if (!verdict.allowed) throw new ApiError(422, verdict.code!, verdict.message!);

    const listing = await prisma.listing.findUnique({ where: { slug: params.slug } });
    if (!listing || listing.status !== 'PUBLISHED' || listing.type !== 'MEDIA') throw new ApiError(404, 'not_found', 'media listing not found');

    const result = await generateImage({ prompt, width: 256, height: 256, timeoutMs: 240_000 });
    return ok({ image: { b64_json: result.b64, mime: 'image/png' }, preview: true, served_by: result.device });
  } catch (e) {
    if (e instanceof Error && /media worker|unavailable|ECONNREFUSED/i.test(e.message)) {
      return err(new ApiError(503, 'no_media_capacity', 'no media worker available right now — try again shortly'));
    }
    return err(e);
  }
}
