# Game social layer — leaderboards, play counts, trending (store backend)

The **STORE** side of the Game Lab social/creator layer. Web games are `DIGITAL_GOOD`
listings (`appCategory GAMES`) with a self-contained sandboxed HTML play bundle at
`Listing.bundleCid`. This layer lets those games report a **cosmetic** high score + play
count, and serves leaderboards, badges, and a "Trending Games" surface.

## HONESTY / THREAT MODEL — read this first

- **Scores are UNTRUSTED.** A game runs in an iframe with `sandbox="allow-scripts"` and
  **no** `allow-same-origin`, so it lives on an OPAQUE origin and can only report out via
  `window.parent.postMessage(...)`. Everything it sends is fully attacker-controllable.
- **Leaderboards are cosmetic — "for fun" only. They are NEVER tied to ANM, balances,
  purchases, or payouts.** Nothing in this layer touches money. Every API response also
  carries a `disclaimer` string saying so.
- **There is NO anti-cheat** beyond (a) per-player + per-IP **rate limiting** and (b)
  clamping the score to a **sane integer range** (`0 .. 1_000_000_000`). A determined
  player can trivially post any score they like; that is acceptable because the board is
  decorative. Do not build anything of value on top of these numbers.
- **No wallet signing is ever bridged into a game.** The score is a one-way, read-only
  value out of the sandbox. The shell never sends anything *into* the game.

## The reporting convention (game → shell)

A generated / template game emits, on game over or win:

```js
window.parent.postMessage(
  { type: "anm-game", event: "score", score: <int>, state: "gameover" | "win" },
  "*"
);
```

The **play shell** (store `/play/[slug]`, the in-page `GamePlay` panel, the wallet WebView)
is the trusted party. It MUST validate `event.origin === "null"` (the opaque sandbox origin)
and the strict message shape before forwarding to the store. That shell→store capture is a
separate lane; this doc covers the store endpoints it calls.

## Player identity (`playerKey`)

Scores/plays are attributed to a `playerKey`, chosen server-side so a client can never
impersonate anyone:

| Caller state                         | Stored `playerKey`            |
|--------------------------------------|-------------------------------|
| Store session cookie (or bearer key) | `acct:<accountId>`            |
| Anonymous, client-supplied uuid      | `anon:<hmac(secret, uuid)>`   |
| Anonymous, no uuid                   | `ip:<hmac(secret, ip)>`       |

- A **client-supplied token is always hashed into the `anon:` namespace** — it can never
  land in `acct:` and can never claim an address. Only the session cookie yields `acct:`.
- Raw uuids / IPs are **never stored** (HMAC-only). The public leaderboard JSON exposes only
  a display name (account displayName / truncated address, or `"Guest"`) plus an opaque,
  non-reversible `id = hmac("gplayer-pub:" + playerKey)` for client-side row dedup — never
  the raw `playerKey` or `accountId`.

## Endpoints — `app/api/mkt/v1/store/games/`

All are **wildcard-CORS, credential-free** (a cross-origin play shell submits anonymously;
a same-origin shell carries the cookie → account attribution). All carry the `disclaimer`.

### `POST /games/[slug]/score` — body `{ score, playerKey?, state? }`
Upserts the caller's **personal best** (one `GameScore` row per `(listing, player)`; only
ever raised). Rate-limited 30/min per player + 120/min per IP; `score` floored + clamped to
`0..1e9`; `state` whitelisted to `win|gameover` (stored in `meta`). Returns
`{ ok, submitted, best, isBest, rank, top:[…10], disclaimer }`.

### `GET /games/[slug]/leaderboard?limit=&playerKey=`
Top-N personal bests (`ORDER BY score DESC, achievedAt ASC` tie-break). If the caller
identifies (cookie or `?playerKey` uuid), `me:{rank,best}` gives "around me". Returns
`{ slug, total, leaderboard:[…], me, disclaimer }`.

### `GET /games/[slug]/stats`
`{ slug, playCount, uniquePlayers, scoredPlayers, topScore, disclaimer }`. Powers card
badges + the creator dashboard's per-game metrics. `playCount` is the denormalized counter;
`uniquePlayers` is `COUNT(DISTINCT playerKey)` over `GamePlay`.

### `POST /games/[slug]/play` — body `{ playerKey? }`
Records a `GamePlay` and increments `Listing.playCount` atomically. **Deduped** per player
per 60s window (a refresh / iframe re-mount does not double-count); rate-limited 20/min per
player + 60/min per IP. Returns `{ ok, counted, playCount }`.

### `GET /games/trending?limit=`
Public "Trending Games" discovery surface: published `GAMES` web-game listings ordered by
`playCount DESC` (newest tie-break), each with `playCount` + `topScore` badge. Returns
`{ games:[…] }`. (Static `trending` segment sits beside the `[slug]` routes with no
conflict — see the route comment.)

### Additive badges on existing catalog routes
`GET /store/apps` and `GET /store/apps/[slug]` now also return `playCount` (and `topScore`
for games — one grouped/aggregate query, no N+1), purely additive fields for card badges +
the creator dashboard.

## Data model (additive, migration NOT applied to live)

- `Listing.playCount Int @default(0)` — denormalized cheap counter for badges/trending.
- `model GamePlay { id, listingId, playerKey, startedAt }` — one row per counted play;
  feeds `uniquePlayers` + dedup.
- `model GameScore { id, listingId, playerKey, score Int, achievedAt, meta? }` —
  `@@unique([listingId, playerKey])` (personal best), `@@index([listingId, score])`.

**Decision — counter column vs derived:** we keep a denormalized `Listing.playCount`
(incremented in the same transaction as the `GamePlay` insert) rather than deriving
`COUNT(*)` on every read. Rationale: card badges + the Trending surface read play counts on
hot list pages; a cheap indexed integer column avoids an aggregate per card. The `GamePlay`
rows remain the source of truth for `uniquePlayers` and dedup, so the two can be reconciled
if they ever drift.

Migration SQL: `prisma/store-migration-social.sql` (generated read-only via
`prisma migrate diff --from-url $DATABASE_URL --to-schema-datamodel`; **not applied** — the
orchestrator applies it). It is purely additive: 2 `CREATE TABLE`, 4 indexes, 2 FKs
(`ON DELETE CASCADE`), 1 `ADD COLUMN … DEFAULT 0`.

## Files

- `lib/gameSocial.ts` — CORS/error helpers, in-memory rate limiter, `playerKey` resolution +
  hashing, score bounds/meta, leaderboard queries, display decoration.
- `app/api/mkt/v1/store/games/[slug]/{score,leaderboard,stats,play}/route.ts`
- `app/api/mkt/v1/store/games/trending/route.ts`
- `prisma/schema.prisma` (+2 models, +`Listing.playCount`), `prisma/store-migration-social.sql`
