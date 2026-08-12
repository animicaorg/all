import { prisma } from './db';
import { STORE_TYPES, assetUrl } from './storeCatalog';
import { normalizeAppCategory } from './appStore';
import { jsonSafe } from './nanm';
import { gameStatsFor, trendHeat, type GameStat } from './gameStats';
import type { AppCardData } from '@/components/AppCard';

// Server-side reader for the public store catalog, shared by the /marketplace Apps section and the
// /marketplace/apps storefront. It mirrors GET /api/mkt/v1/store/apps byte-for-byte (same filters:
// PUBLISHED+PUBLIC store-type listings, and an APP is only surfaced once it has an APPROVED build)
// so the web mirror can never show something the API wouldn't. Bigints are jsonSafe-stringified for
// the presentational AppCard, exactly like the AI marketplace does with ListingCard.

export interface StoreAppsQuery {
  q?: string;
  category?: string | null;
  type?: string; // APP | DIGITAL_GOOD (empty = both)
  sort?: 'top' | 'new' | 'trending';
  take?: number;
  skip?: number;
}

// The Listing fields every card needs, plus the internal id used to join social stats. `id` is
// stripped before the card data is returned so AppCardData stays presentational.
const CARD_SELECT = {
  id: true,
  slug: true, name: true, tagline: true, type: true, appCategory: true, verified: true,
  usersCount: true, ratingSum: true, ratingCount: true, coverUrl: true, publishedAt: true,
  owner: { select: { address: true, displayName: true } },
  anmDomain: { select: { name: true } },
  prices: { where: { active: true }, select: { model: true, amountNanm: true, perCallNanm: true, periodDays: true } },
  storeAssets: { where: { kind: 'ICON' as const }, orderBy: { sortOrder: 'asc' as const }, take: 1, select: { cid: true } },
} as const;

type CardRow = {
  id: string;
  slug: string; name: string; tagline: string | null; type: string; appCategory: string | null;
  verified: boolean; usersCount: number; ratingSum: number; ratingCount: number;
  coverUrl: string | null; publishedAt: Date | null;
  owner: { address: string; displayName: string | null };
  anmDomain: { name: string } | null;
  prices: { model: string; amountNanm: bigint; perCallNanm: bigint; periodDays: number | null }[];
  storeAssets: { cid: string }[];
};

function toCard(l: CardRow, stat?: GameStat): AppCardData {
  return {
    slug: l.slug,
    name: l.name,
    tagline: l.tagline,
    type: l.type,
    category: l.appCategory,
    verified: l.verified,
    usersCount: l.usersCount,
    avgRating: l.ratingCount ? l.ratingSum / l.ratingCount : 0,
    ratingCount: l.ratingCount,
    iconUrl: l.storeAssets[0] ? assetUrl(l.storeAssets[0].cid) : null,
    coverUrl: l.coverUrl,
    publisher: {
      address: l.owner.address,
      displayName: l.owner.displayName,
      anm: l.anmDomain ? `${l.anmDomain.name}.anm` : null,
    },
    prices: l.prices,
    // Social badges — only meaningful for GAMES listings; undefined elsewhere so nothing renders.
    plays: stat && stat.plays > 0 ? stat.plays : undefined,
    players: stat && stat.players > 0 ? stat.players : undefined,
    topScore: stat && stat.topScore != null ? stat.topScore : undefined,
  };
}

// Attach Game Lab social stats to any batch of card rows: fetches stats once for the GAMES rows
// and returns cards keyed in the same order. Non-game rows are unaffected. Fails closed.
async function withGameStats(rows: CardRow[]): Promise<AppCardData[]> {
  const gameIds = rows.filter((r) => r.appCategory === 'GAMES').map((r) => r.id);
  const stats = gameIds.length ? await gameStatsFor(gameIds) : new Map<string, GameStat>();
  return rows.map((r) => toCard(r, stats.get(r.id)));
}

export async function fetchStoreApps(opts: StoreAppsQuery = {}): Promise<AppCardData[]> {
  const type = (opts.type ?? '').trim().toUpperCase();
  const category = normalizeAppCategory(opts.category);
  const sort = opts.sort ?? 'top';
  const take = Math.min(opts.take ?? 40, 100);
  const skip = Math.max(opts.skip ?? 0, 0);

  const where: any = {
    status: 'PUBLISHED',
    visibility: 'PUBLIC',
    type: (STORE_TYPES as readonly string[]).includes(type) ? type : { in: [...STORE_TYPES] },
    // Never list an installable app that has nothing approved to install.
    AND: [{ OR: [{ type: 'DIGITAL_GOOD' }, { builds: { some: { status: 'APPROVED' } } }] }],
  };
  if (category) where.appCategory = category;
  if (opts.q) {
    where.AND.push({
      OR: [
        { name: { contains: opts.q, mode: 'insensitive' } },
        { tagline: { contains: opts.q, mode: 'insensitive' } },
        { description: { contains: opts.q, mode: 'insensitive' } },
        { packageName: { contains: opts.q, mode: 'insensitive' } },
      ],
    });
  }
  const orderBy =
    sort === 'new' ? { publishedAt: 'desc' as const } :
    sort === 'trending' ? { usageCount: 'desc' as const } :
    { ratingCount: 'desc' as const };

  const rows = (await prisma.listing.findMany({ where, orderBy, take, skip, select: CARD_SELECT })) as CardRow[];
  const apps = await withGameStats(rows);
  return jsonSafe(apps) as AppCardData[];
}

// ── Games discovery (Trending Games + the dedicated /marketplace/games page) ──────────────────
// A "game" is a published, public DIGITAL_GOOD in GAMES with a self-contained play bundle
// (Listing.bundleCid) — the exact gate the /play page and the detail play panel use. We load a
// candidate pool (recent-first), attach social stats, then rank in-process so the "trending" order
// (recent plays + score activity) never depends on a denormalized column being present in an
// ORDER BY (which would break a pre-migration DB). Cosmetic surface; the pool cap is generous for
// the current catalog size and noted as a scaling follow-up.
export type GameSort = 'trending' | 'top' | 'new';

const GAME_POOL_CAP = 120;

export async function fetchGames(opts: { sort?: GameSort; take?: number; q?: string } = {}): Promise<AppCardData[]> {
  const sort = opts.sort ?? 'trending';
  const take = Math.min(Math.max(opts.take ?? 60, 1), 100);

  const where: any = {
    status: 'PUBLISHED',
    visibility: 'PUBLIC',
    type: 'DIGITAL_GOOD',
    appCategory: 'GAMES',
    bundleCid: { not: null },
  };
  if (opts.q) {
    where.OR = [
      { name: { contains: opts.q, mode: 'insensitive' } },
      { tagline: { contains: opts.q, mode: 'insensitive' } },
      { description: { contains: opts.q, mode: 'insensitive' } },
    ];
  }

  const rows = (await prisma.listing.findMany({
    where,
    orderBy: { publishedAt: 'desc' },
    take: GAME_POOL_CAP,
    select: CARD_SELECT,
  })) as CardRow[];
  if (rows.length === 0) return [];

  const stats = await gameStatsFor(rows.map((r) => r.id));
  const ranked = rows
    .map((r) => ({ row: r, stat: stats.get(r.id) }))
    .sort((a, b) => {
      const sa = a.stat, sb = b.stat;
      if (sort === 'new') {
        return (b.row.publishedAt?.getTime() ?? 0) - (a.row.publishedAt?.getTime() ?? 0);
      }
      if (sort === 'top') {
        const pd = (sb?.plays ?? 0) - (sa?.plays ?? 0);
        if (pd) return pd;
      } else {
        // trending: recent heat first, then lifetime plays.
        const hd = trendHeat(sb ?? emptyStat()) - trendHeat(sa ?? emptyStat());
        if (hd) return hd;
        const pd = (sb?.plays ?? 0) - (sa?.plays ?? 0);
        if (pd) return pd;
      }
      // shared tiebreakers: rating volume, then recency.
      const rd = b.row.ratingCount - a.row.ratingCount;
      if (rd) return rd;
      return (b.row.publishedAt?.getTime() ?? 0) - (a.row.publishedAt?.getTime() ?? 0);
    })
    .slice(0, take)
    .map(({ row, stat }) => toCard(row, stat));

  return jsonSafe(ranked) as AppCardData[];
}

function emptyStat(): GameStat {
  return { plays: 0, players: 0, recentPlays: 0, recentScores: 0, topScore: null };
}

// Total published+installable store listings (for the storefront KPI).
export async function countStoreApps(): Promise<number> {
  return prisma.listing.count({
    where: {
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      type: { in: [...STORE_TYPES] },
      OR: [{ type: 'DIGITAL_GOOD' }, { builds: { some: { status: 'APPROVED' } } }],
    },
  });
}

// Count of published, playable Game Lab games (for the games-page KPI). Independent of the trend
// pool so the header count is accurate even beyond the ranked pool. Fails closed to 0.
export async function countGames(): Promise<number> {
  try {
    return await prisma.listing.count({
      where: {
        status: 'PUBLISHED',
        visibility: 'PUBLIC',
        type: 'DIGITAL_GOOD',
        appCategory: 'GAMES',
        bundleCid: { not: null },
      },
    });
  } catch {
    return 0;
  }
}
