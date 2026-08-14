import { NextRequest, NextResponse } from 'next/server';
import { createHmac } from 'node:crypto';
import { prisma } from './db';
import { config } from './config';
import { authenticate, ApiError } from './api';
import { jsonSafe } from './nanm';
import { STORE_TYPES } from './storeCatalog';

// ── Web-game social layer (leaderboards + play counts) ───────────────────────────────────────
// COSMETIC / "for fun" ONLY. A sandboxed game reports its score with the tiny convention
//   window.parent.postMessage({ type:"anm-game", event:"score", score:<int>, state:"gameover"|"win" }, "*")
// which the OPAQUE-origin play shell (event.origin === "null") validates and forwards here. Because
// the game runs sandbox="allow-scripts" with NO allow-same-origin, everything it sends is fully
// attacker-controllable — so client-reported scores are UNTRUSTED. These leaderboards are NEVER
// tied to ANM, balances, or payouts, and there is NO anti-cheat beyond rate-limiting + sane integer
// bounds. That is by design and is documented as such on every surface.
//
// A `playerKey` identifies a player WITHOUT storing anything reversible or spoofable:
//   • authenticated store session -> "acct:<accountId>" (our own id; a client can never claim it)
//   • anonymous, client uuid       -> "anon:<hmac(secret, uuid)>"
//   • no session, no uuid          -> "ip:<hmac(secret, ip)>"  (best-effort guest identity)
// A client-supplied token is ALWAYS hashed into the anon namespace — it can never land in "acct:"
// and can never impersonate another account. Only the play-shell session cookie yields "acct:".

export const SCORE_MAX = 1_000_000_000; // Postgres Int is 32-bit; cap well under it and keep scores sane.
export const LEADERBOARD_TOP_N = 10;
const PLAY_DEDUP_WINDOW_MS = 60_000; // one counted play per player per minute
const SCORE_RL_PER_MIN = 30; // per playerKey
const SCORE_RL_IP_PER_MIN = 120; // per ip (family/NAT headroom)
const PLAY_RL_PER_MIN = 20; // per playerKey
const PLAY_RL_IP_PER_MIN = 60; // per ip

// ── CORS ─────────────────────────────────────────────────────────────────────────────────────
// The play shells that submit here (store detail panel, /play/[slug] standalone, wallet WebView)
// may sit on a different origin than this API. These endpoints are anonymous + cosmetic: no
// credentials are required and nothing touches ANM, so a wildcard read/write CORS is safe. A
// cross-origin submit carries NO cookie (this is NOT a credentialed surface), so it attributes to a
// hashed anonymous playerKey only; a same-origin submit still carries the session cookie and
// attributes to the account. NEVER add access-control-allow-credentials here.
export const GAME_CORS: Record<string, string> = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, POST, OPTIONS',
  'access-control-allow-headers': 'content-type',
  'access-control-max-age': '600',
  vary: 'origin',
};

export function gameOk(data: any, init?: ResponseInit) {
  return NextResponse.json(jsonSafe(data), {
    ...init,
    headers: { ...GAME_CORS, ...((init?.headers as Record<string, string>) ?? {}) },
  });
}

export function gamePreflight() {
  return new NextResponse(null, { status: 204, headers: GAME_CORS });
}

export function gameErr(e: unknown) {
  const status = e instanceof ApiError ? e.status : 500;
  const body =
    e instanceof ApiError
      ? { error: { code: e.code, message: e.message } }
      : { error: { code: 'internal', message: e instanceof Error ? e.message : 'internal error' } };
  return NextResponse.json(body, { status, headers: GAME_CORS });
}

// ── In-memory fixed-window rate limiter (per-process; matches lib/apikey.ts rateLimit) ─────────
const rlBuckets = new Map<string, { count: number; resetAt: number }>();
export function gameRateLimit(key: string, perMin: number): boolean {
  const now = Date.now();
  if (rlBuckets.size > 20_000) {
    for (const [k, b] of rlBuckets) if (now >= b.resetAt) rlBuckets.delete(k); // opportunistic prune
  }
  const b = rlBuckets.get(key);
  if (!b || now >= b.resetAt) {
    rlBuckets.set(key, { count: 1, resetAt: now + 60_000 });
    return true;
  }
  if (b.count >= perMin) return false;
  b.count += 1;
  return true;
}

// ── Identity helpers ───────────────────────────────────────────────────────────────────────────
function hmac(input: string): string {
  return createHmac('sha256', config.sessionSecret).update(input).digest('hex');
}

export function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0].trim();
  return req.headers.get('x-real-ip')?.trim() || 'unknown';
}

// A stable, opaque, NON-reversible public handle for a playerKey — safe to expose in leaderboard
// JSON so a client can dedupe rows across calls without ever seeing the accountId or the ip/uuid.
export function publicHandle(playerKey: string): string {
  return hmac('gplayer-pub:' + playerKey).slice(0, 16);
}

export type PlayerKind = 'account' | 'anon' | 'ip';
export interface ResolvedPlayer {
  playerKey: string;
  kind: PlayerKind;
  accountId?: string;
}

// Resolve who is acting. A store session cookie (or bearer key) -> "acct:"; otherwise a
// client-supplied opaque token is hashed into "anon:"; otherwise the ip is hashed into "ip:".
// `token` is the client's persistent uuid (localStorage) — from the POST body or the ?playerKey
// query param. It is NEVER trusted as an address and NEVER stored raw.
export async function resolvePlayer(req: NextRequest, token?: unknown): Promise<ResolvedPlayer> {
  let ctx = null;
  try {
    ctx = await authenticate(req);
  } catch {
    ctx = null; // a rate-limited/invalid bearer just degrades to anonymous — this surface is cosmetic
  }
  if (ctx?.accountId) return { playerKey: 'acct:' + ctx.accountId, kind: 'account', accountId: ctx.accountId };
  const t = typeof token === 'string' ? token.trim() : '';
  if (t.length >= 8 && t.length <= 200) return { playerKey: 'anon:' + hmac('game-anon:' + t).slice(0, 40), kind: 'anon' };
  return { playerKey: 'ip:' + hmac('game-ip:' + clientIp(req)).slice(0, 40), kind: 'ip' };
}

// ── Listing lookup ───────────────────────────────────────────────────────────────────────────
export interface GameListing {
  id: string;
  ownerId: string;
  status: string;
  playCount: number;
  name: string;
}

// Load a web-game listing by slug or throw the standard store 404/410. A "web game" is a store
// listing (APP | DIGITAL_GOOD) that carries a play bundle (Listing.bundleCid). DELISTED games are
// gone for everyone; any other status is readable (cosmetic — the public play shell only ever
// loads PUBLISHED games, and a DRAFT owner testing their own game is harmless).
export async function loadGameListing(slug: string): Promise<GameListing> {
  const listing = await prisma.listing.findUnique({
    where: { slug },
    select: { id: true, ownerId: true, type: true, status: true, bundleCid: true, playCount: true, name: true },
  });
  if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
    throw new ApiError(404, 'not_found', 'game not found');
  }
  if (!listing.bundleCid) throw new ApiError(404, 'not_a_game', 'listing is not a web game');
  if (listing.status === 'DELISTED') throw new ApiError(410, 'not_available', 'game is no longer available');
  return { id: listing.id, ownerId: listing.ownerId, status: listing.status, playCount: listing.playCount, name: listing.name };
}

// ── Score bounds + meta ──────────────────────────────────────────────────────────────────────
export function coerceScore(v: unknown): number {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) throw new ApiError(400, 'bad_score', 'score must be a finite number');
  const i = Math.floor(n);
  if (i < 0) throw new ApiError(400, 'bad_score', 'score must be >= 0');
  return Math.min(i, SCORE_MAX);
}

// Only the whitelisted `state` is persisted (as a tiny JSON blob) — never a free-form client blob.
export function coerceMeta(state: unknown): string | null {
  const st = state === 'win' || state === 'gameover' ? state : undefined;
  if (!st) return null;
  return JSON.stringify({ state: st }).slice(0, 512);
}

// ── Score persistence + leaderboard queries ────────────────────────────────────────────────────
// Upsert a player's PERSONAL BEST. One row per (listing, player); we only ever raise the stored
// score. Handles the create/create race (P2002) by falling back to the raise-if-higher update.
export async function upsertBest(
  listingId: string,
  playerKey: string,
  score: number,
  meta: string | null,
): Promise<{ best: number; improved: boolean }> {
  const where = { listingId_playerKey: { listingId, playerKey } };
  const existing = await prisma.gameScore.findUnique({ where, select: { score: true } });
  if (!existing) {
    try {
      await prisma.gameScore.create({ data: { listingId, playerKey, score, meta } });
      return { best: score, improved: true };
    } catch (e: any) {
      if (e?.code !== 'P2002') throw e; // concurrent create won the row -> fall through to update
    }
  }
  const cur = existing ?? (await prisma.gameScore.findUnique({ where, select: { score: true } }));
  if (cur && score > cur.score) {
    await prisma.gameScore.update({ where, data: { score, meta, achievedAt: new Date() } });
    return { best: score, improved: true };
  }
  return { best: cur?.score ?? score, improved: false };
}

export interface ScoreRow {
  playerKey: string;
  score: number;
  achievedAt: Date;
}

export async function topScores(listingId: string, n: number): Promise<ScoreRow[]> {
  return prisma.gameScore.findMany({
    where: { listingId },
    orderBy: [{ score: 'desc' }, { achievedAt: 'asc' }], // ties: whoever reached it first ranks higher
    take: n,
    select: { playerKey: true, score: true, achievedAt: true },
  });
}

// A player's standing: their stored best + dense-ish rank (1-based). Rank counts strictly-higher
// scores, plus equal scores achieved earlier (matching the leaderboard tie-break).
export async function playerStanding(
  listingId: string,
  playerKey: string,
): Promise<{ best: number; rank: number } | null> {
  const row = await prisma.gameScore.findUnique({
    where: { listingId_playerKey: { listingId, playerKey } },
    select: { score: true, achievedAt: true },
  });
  if (!row) return null;
  const [greater, equalEarlier] = await Promise.all([
    prisma.gameScore.count({ where: { listingId, score: { gt: row.score } } }),
    prisma.gameScore.count({ where: { listingId, score: row.score, achievedAt: { lt: row.achievedAt } } }),
  ]);
  return { best: row.score, rank: greater + equalEarlier + 1 };
}

// Max score per listing in ONE query — for the catalog/trending "top score" badge (no N+1).
export async function topScoresByListing(ids: string[]): Promise<Map<string, number>> {
  if (!ids.length) return new Map();
  const rows = await prisma.gameScore.groupBy({ by: ['listingId'], where: { listingId: { in: ids } }, _max: { score: true } });
  return new Map(rows.map((r) => [r.listingId, r._max.score ?? 0]));
}

// ── Display decoration ──────────────────────────────────────────────────────────────────────
export interface PlayerDisplay {
  name: string;
  address?: string;
  kind: PlayerKind;
}

function shortAddr(a: string): string {
  return a.length > 12 ? `${a.slice(0, 7)}…${a.slice(-4)}` : a;
}

// Batch-resolve display names for a set of leaderboard rows: "acct:" -> account displayName /
// truncated address; anonymous/ip -> "Guest". Returns a per-key lookup so the route can build
// entries without leaking the raw playerKey.
export async function decoratePlayers(rows: { playerKey: string }[]): Promise<(playerKey: string) => PlayerDisplay> {
  const accountIds = [...new Set(rows.filter((r) => r.playerKey.startsWith('acct:')).map((r) => r.playerKey.slice(5)))];
  const accounts = accountIds.length
    ? await prisma.account.findMany({ where: { id: { in: accountIds } }, select: { id: true, displayName: true, address: true } })
    : [];
  const byId = new Map(accounts.map((a) => [a.id, a]));
  return (playerKey: string): PlayerDisplay => {
    if (playerKey.startsWith('acct:')) {
      const a = byId.get(playerKey.slice(5));
      if (a) return { name: a.displayName || shortAddr(a.address), address: a.address, kind: 'account' };
      return { name: 'Player', kind: 'account' };
    }
    return { name: 'Guest', kind: playerKey.startsWith('anon:') ? 'anon' : 'ip' };
  };
}

// Build the public leaderboard entry for a row (no raw playerKey exposed).
export function leaderboardEntry(
  row: ScoreRow,
  rank: number,
  display: (playerKey: string) => PlayerDisplay,
  mePlayerKey?: string,
) {
  const d = display(row.playerKey);
  return {
    rank,
    id: publicHandle(row.playerKey),
    name: d.name,
    address: d.address ?? null,
    kind: d.kind,
    score: row.score,
    achievedAt: row.achievedAt,
    you: mePlayerKey ? row.playerKey === mePlayerKey : false,
  };
}

export { PLAY_DEDUP_WINDOW_MS, SCORE_RL_PER_MIN, SCORE_RL_IP_PER_MIN, PLAY_RL_PER_MIN, PLAY_RL_IP_PER_MIN };
