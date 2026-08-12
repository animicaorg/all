import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { loadGameListing, gameOk, gameErr, gamePreflight } from '@/lib/gameSocial';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/games/[slug]/stats -> { playCount, uniquePlayers, scoredPlayers, topScore }
// Powers the game-card badges + the creator dashboard's per-game metrics (plays, players, top
// score). playCount is the denormalized counter (cheap); uniquePlayers is DISTINCT players who hit
// POST /play; scoredPlayers is how many have a leaderboard row. All cosmetic — never tied to ANM.
export async function GET(_req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const listing = await loadGameListing(params.slug);

    const [uniqueRows, agg] = await Promise.all([
      prisma.$queryRaw<{ count: number }[]>`
        SELECT COUNT(DISTINCT "playerKey")::int AS count FROM "GamePlay" WHERE "listingId" = ${listing.id}`,
      prisma.gameScore.aggregate({ where: { listingId: listing.id }, _max: { score: true }, _count: true }),
    ]);

    return gameOk({
      slug: params.slug,
      playCount: listing.playCount,
      uniquePlayers: uniqueRows[0]?.count ?? 0,
      scoredPlayers: agg._count,
      topScore: agg._max.score ?? null,
      disclaimer: 'Play counts and scores are client-reported and cosmetic only — for fun, never tied to ANM.',
    });
  } catch (e) {
    return gameErr(e);
  }
}

export function OPTIONS() {
  return gamePreflight();
}
