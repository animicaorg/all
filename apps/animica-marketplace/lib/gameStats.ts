import { Prisma } from '@prisma/client';
import { prisma } from './db';

// Server-side reader for the Game Lab SOCIAL layer (play counts, unique players, top scores,
// recent activity). It is the read half of the schema the play-capture lane writes:
//   • Listing.playCount      — denormalized lifetime play counter (cheap; badge + trending base)
//   • GamePlay(playerKey,startedAt)      — one row per recorded play session (unique players, recency)
//   • GameScore(playerKey,score,achievedAt) — one personal-best row per (game, player) (top score)
//
// HONESTY: every number here is CLIENT-REPORTED and UNTRUSTED. It powers cosmetic, "for fun"
// leaderboards / trending / play-count badges ONLY — never an ANM payout or any on-chain reward.
// No anti-cheat beyond the submit-side rate limit + sane integer bounds.
//
// FAILS CLOSED: every query is wrapped so a not-yet-migrated database (the additive migration is
// applied by the orchestrator, not here) or any transient error degrades to zero/na stats instead
// of breaking the storefront, the game detail page, or the dev dashboard. These reads are never
// load-bearing for anything that gates money or entitlement.

export interface GameStat {
  plays: number; // Listing.playCount — denormalized lifetime plays (canonical badge number)
  players: number; // distinct playerKey across GamePlay
  recentPlays: number; // GamePlay in the trailing trend window
  recentScores: number; // GameScore submissions in the trailing trend window
  topScore: number | null; // MAX(GameScore.score); null when no score has ever been posted
}

export const TREND_WINDOW_DAYS = 7;

export function emptyGameStat(): GameStat {
  return { plays: 0, players: 0, recentPlays: 0, recentScores: 0, topScore: null };
}

// A single blended "trending" heat number: recent plays weighted above recent score activity.
// Used only to rank the Trending Games surface; not shown to users.
export function trendHeat(s: GameStat): number {
  return s.recentPlays * 2 + s.recentScores;
}

function sinceDate(days: number): Date {
  return new Date(Date.now() - Math.max(days, 0) * 24 * 60 * 60 * 1000);
}

// Batch social stats for a set of game listing ids. One map entry per requested id (always
// present, defaulting to empty) so callers can index without null-checks.
export async function gameStatsFor(
  listingIds: string[],
  opts: { windowDays?: number } = {},
): Promise<Map<string, GameStat>> {
  const out = new Map<string, GameStat>();
  const ids = Array.from(new Set(listingIds.filter(Boolean)));
  if (ids.length === 0) return out;
  for (const id of ids) out.set(id, emptyGameStat());

  const since = sinceDate(opts.windowDays ?? TREND_WINDOW_DAYS);
  const idList = Prisma.join(ids);

  // plays = denormalized Listing.playCount (kept isolated from the storefront's main selects so a
  // pre-migration column-missing error can never break the page that renders the cards).
  try {
    const rows = await prisma.listing.findMany({
      where: { id: { in: ids } },
      select: { id: true, playCount: true },
    });
    for (const r of rows) {
      const s = out.get(r.id);
      if (s) s.plays = r.playCount ?? 0;
    }
  } catch {
    /* not migrated / transient — leave plays at 0 */
  }

  // players (distinct) + recent plays, one grouped pass over GamePlay.
  try {
    const rows = await prisma.$queryRaw<Array<{ listingId: string; players: number; recent: number }>>(Prisma.sql`
      SELECT "listingId",
             COUNT(DISTINCT "playerKey")::int AS players,
             COUNT(*) FILTER (WHERE "startedAt" >= ${since})::int AS recent
      FROM "GamePlay"
      WHERE "listingId" IN (${idList})
      GROUP BY "listingId"
    `);
    for (const r of rows) {
      const s = out.get(r.listingId);
      if (s) {
        s.players = Number(r.players) || 0;
        s.recentPlays = Number(r.recent) || 0;
      }
    }
  } catch {
    /* leave players / recentPlays at 0 */
  }

  // top score + recent score activity, one grouped pass over GameScore.
  try {
    const rows = await prisma.$queryRaw<Array<{ listingId: string; top: number | null; recent: number }>>(Prisma.sql`
      SELECT "listingId",
             MAX(score)::int AS top,
             COUNT(*) FILTER (WHERE "achievedAt" >= ${since})::int AS recent
      FROM "GameScore"
      WHERE "listingId" IN (${idList})
      GROUP BY "listingId"
    `);
    for (const r of rows) {
      const s = out.get(r.listingId);
      if (s) {
        s.topScore = r.top == null ? null : Number(r.top);
        s.recentScores = Number(r.recent) || 0;
      }
    }
  } catch {
    /* leave topScore=null / recentScores=0 */
  }

  return out;
}

// Single-listing convenience (game detail + /play header). Always resolves to a GameStat.
export async function gameStatFor(listingId: string, opts: { windowDays?: number } = {}): Promise<GameStat> {
  const map = await gameStatsFor([listingId], opts);
  return map.get(listingId) ?? emptyGameStat();
}
