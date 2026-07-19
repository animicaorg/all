import { NextRequest } from 'next/server';
import { ApiError } from '@/lib/api';
import {
  loadGameListing,
  resolvePlayer,
  coerceScore,
  coerceMeta,
  upsertBest,
  topScores,
  playerStanding,
  decoratePlayers,
  leaderboardEntry,
  clientIp,
  gameRateLimit,
  gameOk,
  gameErr,
  gamePreflight,
  LEADERBOARD_TOP_N,
  SCORE_RL_PER_MIN,
  SCORE_RL_IP_PER_MIN,
} from '@/lib/gameSocial';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/games/[slug]/score  { score, playerKey?, state? }
// Records a web game's client-reported score as the player's PERSONAL BEST and returns their new
// rank + the top of the leaderboard. UNTRUSTED + COSMETIC: the score comes from a sandboxed game
// via postMessage; it is bounded to a sane int and rate-limited, but there is NO anti-cheat and it
// is NEVER tied to ANM. A store session attributes the best to the account; otherwise the client's
// opaque `playerKey` uuid is hashed into an anonymous player (never an address, never stored raw).
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const listing = await loadGameListing(params.slug);
    const body = await req.json().catch(() => ({}));

    const score = coerceScore(body?.score);
    const meta = coerceMeta(body?.state);
    const player = await resolvePlayer(req, body?.playerKey);

    // Rate limit per player AND per ip so neither a single key nor a single host can flood.
    if (!gameRateLimit(`score:${player.playerKey}`, SCORE_RL_PER_MIN) || !gameRateLimit(`score-ip:${clientIp(req)}`, SCORE_RL_IP_PER_MIN)) {
      throw new ApiError(429, 'rate_limited', 'too many score submissions — slow down');
    }

    const { best, improved } = await upsertBest(listing.id, player.playerKey, score, meta);
    const [standing, top] = await Promise.all([
      playerStanding(listing.id, player.playerKey),
      topScores(listing.id, LEADERBOARD_TOP_N),
    ]);
    const display = await decoratePlayers(top);

    return gameOk({
      ok: true,
      submitted: score,
      best,
      isBest: improved,
      rank: standing?.rank ?? null,
      top: top.map((r, i) => leaderboardEntry(r, i + 1, display, player.playerKey)),
      // Honesty note surfaced to clients: leaderboards are cosmetic, never tied to ANM.
      disclaimer: 'Scores are client-reported and cosmetic only — for fun, never tied to ANM.',
    });
  } catch (e) {
    return gameErr(e);
  }
}

export function OPTIONS() {
  return gamePreflight();
}
