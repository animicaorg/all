# Game Lab — Social layer: scores, leaderboards, play counts, Trending (marketplace × Forge × wallet)

Adds a **social / creator** layer over the LIVE Game Lab: generated & template games **report a
score**, the play shells **capture** it, the store **persists** high scores + play counts and serves
**leaderboards**, and the storefront gains a **Trending Games** surface, play/score **badges**, and
per‑game **creator metrics**. Fully **additive** to the existing store / purchase / play flows.

> **HONESTY — read this first.** Client‑reported scores are **UNTRUSTED**. A game runs
> `sandbox="allow-scripts"` with **no** `allow-same-origin`, so everything it posts out is fully
> attacker‑controllable. Leaderboards are **cosmetic / "for fun" only**. They are **NEVER** tied to
> ANM, balances, payouts, or any on‑chain reward. There is **NO anti‑cheat** beyond a **rate limit**
> (server) + **sane integer bounds** (client *and* server). A determined player can post any score;
> that is accepted and by design. The game→shell channel is **one‑way, read‑only** — no wallet
> signing is ever bridged *into* a game, and the shell never posts anything back into the frame.

A "web game" is a store `Listing` under `AppCategory = GAMES` (type `DIGITAL_GOOD`) whose play bundle
is a single self‑contained sandboxed HTML document at `Listing.bundleCid`.

---

## 1. The score convention (the contract every layer agrees on)

A game reports out **only** via `window.parent.postMessage`, with this exact shape:

```js
// on run start (optional but recommended — lets the shell count a play early)
window.parent.postMessage({ type: "anm-game", event: "start" }, "*");

// on game-over OR win (score-change messages are also allowed)
window.parent.postMessage({
  type:  "anm-game",
  event: "score",
  score: 1234,                    // INTEGER, 0 … 1_000_000_000
  state: "gameover"               // "gameover" | "win"
}, "*");
```

Rules the shell enforces strictly (anything else is silently ignored):
- `type` must equal `"anm-game"`.
- `event` must be `"start"` or `"score"`.
- `score` must be a **finite integer** in `[0, 1e9]`.
- `state`, if present, must be `"gameover"` or `"win"` (a valid score with no `state` defaults to
  `"gameover"`; a *present‑but‑invalid* `state` rejects the whole message).

**Emission (Forge, generation side)** — both paths already emit this:
- **LLM path**: `lib/generator/game-prompts.ts` → `GAME_SYSTEM_PROMPT` has a `SCORE REPORTING`
  paragraph instructing generated games to post `start` + `score` with an integer score, wrapped in
  `try/catch`, one‑way, "cosmetic leaderboards only, do NOT tie to any reward, do NOT read back".
- **Template path**: all 5 genre templates (`templates/game-{platformer,shooter,puzzle,runner,board}.ts`)
  carry a tiny shared inline helper `anmGame(ev, sc, st)` (wraps `window.parent.postMessage` in
  `try/catch`, coerces `sc | 0`). Natural per‑genre score: platformer = coins, shooter/puzzle =
  score, runner = `floor(distance)`, board (Tri Tactics) = cumulative `wins` (fires every round).

---

## 2. Capture (marketplace play shells)

Two surfaces load the same bundle in an **opaque** sandboxed iframe and share one hook:
- `app/play/[slug]/PlayStandalone.tsx` — the hosted `/play/[slug]` standalone shell (also what the
  wallet WebView loads).
- `components/GamePlay.tsx` — the in‑page play panel on the storefront detail page.

Shared wiring:
- `lib/useGameLeaderboard.ts` — the `window "message"` listener. **Trust anchor** =
  `event.source === ourIframe.contentWindow`. The opaque frame means `event.origin` is the string
  `"null"`, so origin is **explicitly not trusted**; source identity + the strict schema
  (`parseGameMessage`) are the whole trust model. On the first `anm-game` message it records a play
  once/session; on a terminal `score` it submits (throttled ≥ 1500 ms) and refreshes the board.
- `lib/gameClient.ts` — plain client helpers: `parseGameMessage`, `getPlayerKey` (anonymous uuid in
  `localStorage['anm_game_player']`), `recordPlay`, `postScore`, `getLeaderboard`. All fetches are
  same‑origin, credential‑aware, and **fail closed to `null`** (never throw) so a missing/erroring
  endpoint never breaks the game.
- `components/GameLeaderboard.tsx` — presentational board (top rows, "your best", play/player
  counts, footnote *"Player‑reported — just for fun, not tied to ANM."*).

---

## 3. Persistence + APIs (marketplace store)

Backend helper `lib/gameSocial.ts` + routes under `app/api/mkt/v1/store/games/…`:

| Method & path | Body / query | Returns |
|---|---|---|
| `POST /games/[slug]/play` | `{ playerKey? }` | `{ ok, counted, playCount }` |
| `POST /games/[slug]/score` | `{ score, playerKey?, state? }` | `{ ok, submitted, best, isBest, rank, top[10], disclaimer }` |
| `GET  /games/[slug]/leaderboard` | `?player=&playerKey=&limit=` | `{ total, plays, players, leaderboard[], me:{rank,best}, disclaimer }` |
| `GET  /games/[slug]/stats` | — | `{ playCount, uniquePlayers, scoredPlayers, topScore, disclaimer }` |
| `GET  /games/trending` | `?limit=` | `{ games[] }` (playCount‑ranked, topScore badge) |
| `GET  /me/games` | *(publish scope)* | owner's games + plays/players/topScore + revenue |

Key behaviours:
- **Personal best**, raise‑only: `GameScore` is one row per `(listingId, playerKey)`
  (`@@unique`), P2002‑race‑safe.
- **Play counts**: denormalized `Listing.playCount` incremented in the same `$transaction` as the
  `GamePlay` insert; deduped per player per 60 s; `GamePlay` rows remain the source of truth for
  `uniquePlayers`.
- **playerKey** (never stores anything reversible/spoofable): store session/bearer →
  `acct:<accountId>`; else a client uuid → `anon:<hmac(secret,uuid)>`; else `ip:<hmac(secret,ip)>`.
  A client‑supplied token is **always** hashed into `anon:` — it can never claim an account or an
  address. Leaderboard JSON exposes only a display name (`displayName` / short addr / "Guest") + an
  opaque non‑reversible `id`; the raw `playerKey`/`accountId` is never leaked.
- **Rate limits** (in‑memory fixed window): score 30/min/player + 120/min/ip; play 20/min/player +
  60/min/ip. Scores floored + clamped `0…1e9`; only the whitelisted `state` is persisted.
- Every response carries a `disclaimer` field restating the cosmetic/no‑ANM posture.

Discovery / badges / creator metrics: `GET /games/trending` + `topScore` on the existing
`GET /store/apps` (grouped, no N+1) and `GET /store/apps/[slug]` power the **Trending Games** row
(`/marketplace/games`), the `▶ plays` / `★ topScore` card badges, and the dev dashboard
(`/dev/games` via `GET /me/games`).

---

## 4. Schema + migration (additive, NOT applied to live)

`prisma/schema.prisma` adds two models + one column (see file for the full definition):
- `GamePlay(id, listingId, playerKey, startedAt)` — `@@index([listingId,startedAt])`,
  `@@index([listingId,playerKey,startedAt])`, FK → `Listing` `ON DELETE CASCADE`.
- `GameScore(id, listingId, playerKey, score, achievedAt, meta?)` —
  `@@unique([listingId,playerKey])` (= personal best), `@@index([listingId,score])`, FK CASCADE.
- `Listing.playCount Int @default(0)` + two back‑relations.

`prisma/store-migration-social.sql` is **purely additive** — verified: **0 `DROP`**, and only:
`1 × ADD COLUMN` (`playCount` `DEFAULT 0`), `2 × CREATE TABLE`, `4 × CREATE INDEX`, `2 × ADD FK`.
It is **NOT applied to the live DB** by any lane — the orchestrator applies it. Until then every
stats read **fails closed** (badges/trending/metrics show `0`/`na`) and all pages still render.

---

## 5. Deploy order

**Store (marketplace):**
1. **Apply the migration** to the live Postgres (`:5443`):
   `psql "$DATABASE_URL" -f prisma/store-migration-social.sql` (or `prisma migrate deploy`). Additive
   only — safe on a live table; the `ADD COLUMN … DEFAULT 0` and new tables take no destructive lock.
2. **Rebuild** the app: `npm run build` (runs `prisma generate` + `next build`).
3. **Restart** the marketplace service so it picks up the new client + `.next`.

> Order matters only to avoid benign log noise: the shared `lib/db.ts` client (`log:['error']`)
> emits harmless `prisma:error` lines on stats reads **until** the migration is applied. Applying the
> migration before the restart closes that window. Functionality is unaffected either way.

**Forge:**
4. **Rebuild + restart** Forge (`/root/animica-forge`) so `GAME_SYSTEM_PROMPT` + the 5 updated
   templates ship. No migration (Forge owns no tables here) and no DB change.

Nothing else changes — no nginx edit, no new env var, no wallet rebuild required for the marketplace
surfaces (the wallet WebView already loads `animica.dev/play/[slug]`, so it inherits the shell
changes automatically).

---

## 6. Verification performed (integration lane)

- **marketplace** `tsc --noEmit` → **exit 0**; `next build` into an **alternate distDir**
  (`NEXT_VERIFY_DIST_DIR=.next-verify`, reverted, dir removed) → **exit 0**; live `.next` untouched.
  All new routes register: `/games/[slug]/{score,leaderboard,play,stats}`, `/games/trending`,
  `/me/games`, `/marketplace/games`, `/dev/games`.
- **forge** `tsc --noEmit` → **exit 0**; alt‑distDir `next build` → **exit 0**; `npm test` →
  **59/59 pass**. The built server bundle contains `anm-game` + `window.parent.postMessage`; each of
  the 5 templates, run through `sanitizeGeneratedApp`, still emits `start` + `score` (`win` +
  `gameover`) via the helper.
- **migration** re‑scanned: purely additive (0 DROP), **not** applied to live.
- **end‑to‑end (static)**: template JS posts the score → shell listener validates
  (`source`‑identity + strict schema + `0…1e9` bounds) → `POST /score` upserts the personal best +
  returns rank/top → `GET /leaderboard` returns the board (with `plays`/`players`).

**Integration fix applied this lane** (`app/api/mkt/v1/store/games/[slug]/leaderboard/route.ts`,
additive): the client sends the anonymous uuid as `?player=`, but the route only read `?playerKey=`,
so an anon player's own `me` rank/best silently fell back to their ip‑hashed key and never matched
their score. The route now accepts **both** param names and additionally returns `plays`
(`Listing.playCount`) + `players` (distinct `GamePlay` players), which the play‑shell leaderboard UI
already expected — aligning the two lanes' documented contract.

---

## 7. Known limitations & follow‑ups

- **No anti‑cheat** beyond rate‑limit + bounds — restated everywhere on purpose. Do **not** build
  any ANM reward on these numbers.
- **Rate limiter is per‑process, in‑memory** — resets on restart and isn't shared across replicas.
  Fine for cosmetic abuse‑throttling; move to a shared store if the marketplace ever scales out.
- **Trending ranks in‑process over a bounded candidate pool** — fine for the current catalog; a
  denormalized `ORDER BY playCount` index is the scaling follow‑up.
- **Score submit is not entitlement‑gated** even for paid games (cosmetic; the shell only loads a
  paid bundle if the player is entitled anyway).
- **Wallet leaderboard UI** — the wallet WebView already captures + submits via the shared
  `/play/[slug]` shell, but a **native** leaderboard panel inside the Flutter Store tab is not built
  here. Ship it as a **wallet 0.2.2** follow‑up.
