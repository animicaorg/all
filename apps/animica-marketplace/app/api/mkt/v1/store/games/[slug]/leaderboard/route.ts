import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import {
  loadGameListing,
  resolvePlayer,
  topScores,
  playerStanding,
  decoratePlayers,
  leaderboardEntry,
  gameOk,
  gameErr,
  gamePreflight,
  LEADERBOARD_TOP_N,
} from '@/lib/gameSocial';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/games/[slug]/leaderboard?limit=&playerKey=
// Public, cosmetic leaderboard: top N personal-best scores. If the caller identifies (store
// session cookie, or their anonymous ?playerKey uuid) an `me` block gives their rank + best so the
// UI can show "around me" even when they're off the top page. Reads only — safe wildcard CORS.
export async function GET(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const listing = await loadGameListing(params.slug);
    const sp = req.nextUrl.searchParams;
    const limit = Math.min(Math.max(Number(sp.get('limit') ?? LEADERBOARD_TOP_N), 1), 100);
    // The play shell (lib/gameClient.ts) sends the anonymous uuid as ?player=; accept ?playerKey=
    // too so either lane's param name resolves the caller's OWN rank/best. Without this an anon
    // player's "me" block silently falls back to their ip-hashed key and never matches their score.
    const player = await resolvePlayer(req, sp.get('player') ?? sp.get('playerKey') ?? undefined);

    const [top, me, total, distinctPlayers] = await Promise.all([
      topScores(listing.id, limit),
      playerStanding(listing.id, player.playerKey),
      prisma.gameScore.count({ where: { listingId: listing.id } }),
      prisma.$queryRaw<{ count: number }[]>`
        SELECT COUNT(DISTINCT "playerKey")::int AS count FROM "GamePlay" WHERE "listingId" = ${listing.id}`,
    ]);
    const display = await decoratePlayers(top);
    const players = distinctPlayers[0]?.count ?? 0;

    return gameOk({
      slug: params.slug,
      total,
      // `plays`/`players` mirror the /stats endpoint so the play-shell leaderboard UI can render its
      // "▶ N plays · N players" line straight from this one call (the gameClient contract expects
      // them, and normalizeLeaderboard reads exactly these field names).
      plays: listing.playCount,
      players,
      leaderboard: top.map((r, i) => leaderboardEntry(r, i + 1, display, player.playerKey)),
      me: me ? { rank: me.rank, best: me.best } : null,
      disclaimer: 'Scores are client-reported and cosmetic only — for fun, never tied to ANM.',
    });
  } catch (e) {
    return gameErr(e);
  }
}

export function OPTIONS() {
  return gamePreflight();
}
