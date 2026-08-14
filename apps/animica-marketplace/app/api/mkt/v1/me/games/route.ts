import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';
import { gameStatsFor } from '@/lib/gameStats';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/games -> creator dashboard data for the signed-in publisher's Game Lab games.
// Per-game plays / unique players / top score joined with the SALE_CREDIT/FORK_ROYALTY revenue the
// earnings page already shows (ref = listingId; see lib/ledger.ts settlePurchaseInTx). Scope
// 'publish' so it is reachable only from a devportal session / publish-scoped key.
//
// The play/player/top-score numbers are CLIENT-REPORTED and UNTRUSTED (cosmetic, "for fun" — never
// tied to ANM). gameStatsFor fails closed, so a not-yet-migrated DB simply reports zeros here.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');

    const acct = await prisma.account.findUnique({ where: { id: ctx.accountId }, select: { address: true } });

    const listings = await prisma.listing.findMany({
      where: { ownerId: ctx.accountId, type: 'DIGITAL_GOOD', appCategory: 'GAMES' },
      orderBy: { createdAt: 'desc' },
      select: {
        id: true, slug: true, name: true, status: true, visibility: true,
        bundleCid: true, publishedAt: true, createdAt: true,
      },
    });

    const ids = listings.map((l) => l.id);
    const stats = await gameStatsFor(ids);

    // Revenue per game — same source as /me/earnings (SALE_CREDIT + FORK_ROYALTY, ref = listingId).
    const grouped = ids.length
      ? await prisma.ledgerEntry.groupBy({
          by: ['ref'],
          where: { accountId: ctx.accountId, kind: { in: ['SALE_CREDIT', 'FORK_ROYALTY'] }, ref: { in: ids } },
          _sum: { deltaNanm: true },
          _count: true,
        })
      : [];
    const byRef = new Map(grouped.map((g) => [g.ref!, g]));

    const games = listings.map((l) => {
      const s = stats.get(l.id);
      const g = byRef.get(l.id);
      const earned = g?._sum.deltaNanm ?? 0n;
      return {
        slug: l.slug,
        name: l.name,
        status: l.status,
        visibility: l.visibility,
        published: !!l.publishedAt && l.status === 'PUBLISHED',
        playable: !!l.bundleCid,
        plays: s?.plays ?? 0,
        players: s?.players ?? 0,
        recentPlays: s?.recentPlays ?? 0,
        topScore: s?.topScore ?? null,
        sales: g?._count ?? 0,
        earnedNanm: earned.toString(),
        earnedAnm: nanmToAnm(earned),
      };
    });

    const earnedTotal = games.reduce((a, b) => a + BigInt(b.earnedNanm), 0n);
    const totals = {
      games: games.length,
      plays: games.reduce((a, b) => a + b.plays, 0),
      players: games.reduce((a, b) => a + b.players, 0), // per-game distinct; may over-count across games
      recentPlays: games.reduce((a, b) => a + b.recentPlays, 0),
      topScore: games.reduce((m, b) => (b.topScore != null && b.topScore > m ? b.topScore : m), 0),
      earnedNanm: earnedTotal.toString(),
      earnedAnm: nanmToAnm(earnedTotal),
    };

    return ok(jsonSafe({ address: acct?.address ?? null, games, totals }));
  } catch (e) {
    return err(e);
  }
}
