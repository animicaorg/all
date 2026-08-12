import { NextRequest } from 'next/server';
import { ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import {
  loadGameListing,
  resolvePlayer,
  clientIp,
  gameRateLimit,
  gameOk,
  gameErr,
  gamePreflight,
  PLAY_DEDUP_WINDOW_MS,
  PLAY_RL_PER_MIN,
  PLAY_RL_IP_PER_MIN,
} from '@/lib/gameSocial';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/games/[slug]/play  { playerKey? }
// Records a play session + bumps the denormalized Listing.playCount. Deduped per player per short
// window (so re-mounting the iframe / a page refresh doesn't inflate the count) and rate-limited
// per player + ip. Cosmetic only — never tied to ANM. Returns the current playCount.
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const listing = await loadGameListing(params.slug);
    const body = await req.json().catch(() => ({}));
    const player = await resolvePlayer(req, body?.playerKey);

    if (!gameRateLimit(`play:${player.playerKey}`, PLAY_RL_PER_MIN) || !gameRateLimit(`play-ip:${clientIp(req)}`, PLAY_RL_IP_PER_MIN)) {
      throw new ApiError(429, 'rate_limited', 'too many play events — slow down');
    }

    // Dedup: if this player already registered a play within the window, don't double-count.
    const recent = await prisma.gamePlay.findFirst({
      where: { listingId: listing.id, playerKey: player.playerKey, startedAt: { gt: new Date(Date.now() - PLAY_DEDUP_WINDOW_MS) } },
      select: { id: true },
    });

    if (recent) {
      return gameOk({ ok: true, counted: false, playCount: listing.playCount });
    }

    // Record the session + bump the counter atomically.
    const [, updated] = await prisma.$transaction([
      prisma.gamePlay.create({ data: { listingId: listing.id, playerKey: player.playerKey } }),
      prisma.listing.update({ where: { id: listing.id }, data: { playCount: { increment: 1 } }, select: { playCount: true } }),
    ]);

    return gameOk({ ok: true, counted: true, playCount: updated.playCount });
  } catch (e) {
    return gameErr(e);
  }
}

export function OPTIONS() {
  return gamePreflight();
}
