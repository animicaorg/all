import { NextRequest } from 'next/server';
import { publicOk, publicPreflight, err } from '@/lib/api';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Lightweight in-memory throttle so a single client can't inflate the play counter by
// hammering the endpoint. Best-effort per (ip, item), resets on process restart — good
// enough for a soft vanity metric; it never gates the response.
const PLAY_WINDOW_MS = 30_000;
const MAX_ENTRIES = 50_000;
const lastPlay = new Map<string, number>();

function throttled(key: string): boolean {
  const now = Date.now();
  const prev = lastPlay.get(key);
  if (prev !== undefined && now - prev < PLAY_WINDOW_MS) return true;
  if (lastPlay.size > MAX_ENTRIES) {
    for (const [k, t] of lastPlay) if (now - t > PLAY_WINDOW_MS) lastPlay.delete(k);
    if (lastPlay.size > MAX_ENTRIES) lastPlay.clear();
  }
  lastPlay.set(key, now);
  return false;
}

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0].trim();
  return req.headers.get('x-real-ip') || 'unknown';
}

export function OPTIONS() {
  return publicPreflight();
}

// POST /api/mkt/v1/stream/item/[id]/play — bump the play counter. Fire-and-forget:
// we don't block the response on the write, and a bad/missing id just no-ops.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const key = `${clientIp(req)}:${params.id}`;
    if (!throttled(key)) {
      prisma.mediaItem
        .update({ where: { id: params.id }, data: { plays: { increment: 1 } } })
        .catch(() => { /* unknown id / transient DB error: swallow, it's a soft metric */ });
    }
    return publicOk({ ok: true });
  } catch (e) {
    return err(e);
  }
}
