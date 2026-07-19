// Client-side helper for Game Lab leaderboards — the browser half of the play surfaces.
//
// A published Game Lab game (DIGITAL_GOOD / GAMES / Listing.bundleCid) runs in an OPAQUE
// sandboxed iframe (sandbox="allow-scripts", no allow-same-origin), so it can only report OUT
// via window.parent.postMessage. The play shell captures those messages, records a play, and
// submits terminal scores here; it also reads the leaderboard for display.
//
// HONESTY / SECURITY POSTURE (must stay true — surfaced in the UI too):
//   * Scores are CLIENT-REPORTED and therefore UNTRUSTED. Leaderboards are cosmetic / "for fun".
//     They are NEVER tied to ANM payouts or any on-chain reward. There is no anti-cheat beyond a
//     rate limit (server) + sane bounds (here AND server). A determined player can post any score.
//   * No wallet signing is ever bridged INTO a game. The game→shell channel is one-way, read-only:
//     the shell reads score reports; it never sends anything back into the frame.
//   * Attribution: an anonymous per-browser playerKey (uuid, localStorage) travels with every
//     request for cross-session "your best/rank". If a store session cookie is present the BACKEND
//     attributes to the wallet address instead (it hashes/ignores the playerKey) — the client just
//     always sends same-origin credentials so the cookie can be used when it exists.
//
// ── Backend contract (implemented by the marketplace store lane) ──────────────────────────────
//   POST /api/mkt/v1/store/games/[slug]/play        body {playerKey}                 -> {plays}
//   POST /api/mkt/v1/store/games/[slug]/score       body {playerKey,score,state}     -> {best,rank,accepted,plays?}
//   GET  /api/mkt/v1/store/games/[slug]/leaderboard?player=<key>&limit=<n>            -> Leaderboard
// All three are public (no auth required) but credential-aware. Responses are read defensively
// below (several field-name variants tolerated) and EVERY call fails closed to null so the play
// experience never breaks if the endpoint is missing, erroring, or rate-limiting.

export const GAME_MSG_TYPE = 'anm-game' as const;

// Score sanity bounds — anything outside is dropped client-side (the server re-checks). Generous
// so real games (idle/clicker scores can be large) aren't clipped, tight enough to reject garbage.
export const SCORE_MIN = 0;
export const SCORE_MAX = 1_000_000_000; // 1e9

export type GameState = 'gameover' | 'win';

export type GameMessage =
  | { event: 'start' }
  | { event: 'score'; score: number; state: GameState };

export interface LeaderboardEntry {
  rank: number;
  name: string;   // display label chosen by the server (shortened wallet addr or anon handle)
  score: number;
  isYou: boolean; // this row is the current player (server-decided by cookie/playerKey)
}

export interface Leaderboard {
  top: LeaderboardEntry[];
  you: { rank: number | null; best: number | null } | null;
  plays: number;   // total play count
  players: number; // distinct players
}

export interface PostScoreResult {
  best: number | null;
  rank: number | null;
  accepted: boolean; // true when this was a new personal best the server kept
  plays: number | null;
}

const PLAYER_KEY_STORAGE = 'anm_game_player';

// ── strict, hostile-input-safe message validation ───────────────────────────────────────────
// The frame is opaque so event.origin is the string "null" — we do NOT trust origin. Trust comes
// from (a) the caller having verified event.source === ourIframe.contentWindow and (b) this strict
// schema. Anything that isn't an exactly-shaped {type:"anm-game", ...} message returns null.
export function parseGameMessage(data: unknown): GameMessage | null {
  if (!data || typeof data !== 'object') return null;
  const m = data as Record<string, unknown>;
  if (m.type !== GAME_MSG_TYPE) return null;

  if (m.event === 'start') return { event: 'start' };

  if (m.event === 'score') {
    const score = m.score;
    if (typeof score !== 'number' || !Number.isFinite(score) || !Number.isInteger(score)) return null;
    if (score < SCORE_MIN || score > SCORE_MAX) return null;
    // state is a label only. Require it to be one of the two if present; default to 'gameover'
    // so a game that reports a valid score but forgets state still counts (leniency here weakens
    // nothing — the security-relevant checks are source identity + score bounds).
    let state: GameState = 'gameover';
    if (m.state !== undefined) {
      if (m.state !== 'gameover' && m.state !== 'win') return null;
      state = m.state;
    }
    return { event: 'score', score, state };
  }

  return null;
}

// ── anonymous, cross-session player key (uuid in localStorage) ──────────────────────────────
export function getPlayerKey(): string {
  if (typeof window === 'undefined') return '';
  try {
    let k = window.localStorage.getItem(PLAYER_KEY_STORAGE);
    if (!k) {
      k = genUuid();
      window.localStorage.setItem(PLAYER_KEY_STORAGE, k);
    }
    return k;
  } catch {
    // Private mode / storage disabled — degrade to an ephemeral (per-page) key; attribution is
    // best-effort and never load-bearing, so this is fine.
    return '';
  }
}

function genUuid(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  } catch {
    /* fall through */
  }
  // RFC-4122-ish fallback (not cryptographically strong — only a random attribution tag).
  const h = () => Math.floor(Math.random() * 0x10000).toString(16).padStart(4, '0');
  return `${h()}${h()}-${h()}-4${h().slice(1)}-a${h().slice(1)}-${h()}${h()}${h()}`;
}

// ── network helpers (same-origin, credential-aware, never throw) ─────────────────────────────
function gamesBase(slug: string): string {
  return `/api/mkt/v1/store/games/${encodeURIComponent(slug)}`;
}

async function postJson(url: string, body: unknown): Promise<any | null> {
  try {
    const r = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    if (!r.ok) return null;
    return await r.json().catch(() => null);
  } catch {
    return null;
  }
}

function num(v: unknown, fallback: number | null = null): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

/** Count a play. Fire-and-forget once per session (the shell guards the once-per-session rule). */
export async function recordPlay(slug: string, playerKey: string): Promise<{ plays: number | null } | null> {
  const data = await postJson(`${gamesBase(slug)}/play`, { playerKey });
  if (!data) return null;
  return { plays: num(data.plays ?? data.playCount ?? data.count) };
}

/** Submit a terminal score. The shell throttles calls; the server keeps the max + rate-limits. */
export async function postScore(
  slug: string,
  args: { score: number; state: GameState; playerKey: string },
): Promise<PostScoreResult | null> {
  const data = await postJson(`${gamesBase(slug)}/score`, {
    playerKey: args.playerKey,
    score: args.score,
    state: args.state,
  });
  if (!data) return null;
  return {
    best: num(data.best ?? data.yourBest ?? data.personalBest),
    rank: num(data.rank ?? data.yourRank),
    accepted: Boolean(data.accepted ?? data.isBest ?? false),
    plays: num(data.plays ?? data.playCount),
  };
}

/** Read the leaderboard (top rows + this player's best/rank + play/player counts). Null on error. */
export async function getLeaderboard(slug: string, playerKey: string, limit = 10): Promise<Leaderboard | null> {
  try {
    const q = new URLSearchParams();
    if (playerKey) q.set('player', playerKey);
    q.set('limit', String(limit));
    const r = await fetch(`${gamesBase(slug)}/leaderboard?${q.toString()}`, {
      credentials: 'same-origin',
      headers: { accept: 'application/json' },
    });
    if (!r.ok) return null;
    const data = await r.json().catch(() => null);
    if (!data || typeof data !== 'object') return null;
    return normalizeLeaderboard(data);
  } catch {
    return null;
  }
}

// Tolerant of a few backend field-name shapes so the two lanes converge even if names drift.
function normalizeLeaderboard(data: any): Leaderboard {
  const rawTop: any[] = Array.isArray(data.top)
    ? data.top
    : Array.isArray(data.entries)
      ? data.entries
      : Array.isArray(data.leaderboard)
        ? data.leaderboard
        : [];
  const top: LeaderboardEntry[] = rawTop.map((e: any, i: number) => ({
    rank: num(e?.rank, i + 1) as number,
    name: String(e?.name ?? e?.label ?? e?.player ?? e?.handle ?? 'Player'),
    score: num(e?.score ?? e?.best, 0) as number,
    isYou: Boolean(e?.isYou ?? e?.you ?? e?.mine ?? false),
  }));

  const rawYou = data.you ?? data.me ?? data.self ?? null;
  const you = rawYou
    ? { rank: num(rawYou.rank ?? rawYou.yourRank), best: num(rawYou.best ?? rawYou.score ?? rawYou.yourBest) }
    : null;

  return {
    top,
    you,
    plays: (num(data.plays ?? data.playCount ?? data.playsTotal, 0) as number) || 0,
    players: (num(data.players ?? data.playerCount ?? data.uniquePlayers, 0) as number) || 0,
  };
}
