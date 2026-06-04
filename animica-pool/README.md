# Animica Pool

Unified **mining + AI inference + compute rental + worker + provider-routing + payout redistribution** platform for `pool.animica.org`.

A useful-work compute economy: mine ANM/XMR/dual, rent compute, serve AI inference, buy AI credits, connect worker machines, and receive crypto payouts — all tracked in a central ledger.

## Monorepo layout
```
apps/web              Next.js 14 frontend (TS, Tailwind, TanStack Query, Zustand, Recharts)
apps/api              NestJS API (TS, Prisma, JWT auth, RBAC)
packages/shared       Shared types/enums + provider-router contracts
packages/provider-router  AI provider adapters (mock, bittensor, runpod, akash, …)  [P2/P7]
packages/worker-agent     Worker install/agent  [P5]
prisma                PostgreSQL schema + seed
docker                Dockerfiles + compose
docs                  Documentation
```

## Quick start (local)
```bash
cp .env.example .env          # fill JWT_SECRET (>=32 chars), ADMIN_EMAILS, etc.
docker compose up -d postgres redis
pnpm install
pnpm prisma:generate
pnpm prisma:migrate           # creates tables
SEED_ADMIN_EMAIL=you@x.com SEED_ADMIN_PASSWORD=secret pnpm db:seed
pnpm dev                      # api on :4000, web on :3000
```
Open http://localhost:3000. The web app proxies `/api/*` and `/v1/*` to the API.

## Full stack via Docker
```bash
cp .env.example .env
docker compose up -d --build
```

## Status (phased build)
- **P1 ✅** monorepo, full Prisma schema, email/password auth (bcrypt + JWT cookie, RBAC), API keys (hashed, shown once), admin overview shell, landing + dashboard + auth + api-keys + admin pages.
- P2 OpenAI `/v1/*` + provider router + credit deduction
- P3 NOWPayments checkout + webhook
- P4 mining dashboard (ANM/XMR/dual) + payout preference
- P5 workers (register/heartbeat/benchmark)
- P6 compute rental marketplace
- P7 provider adapters + health
- P8 payout requests + admin approval + mass payouts
- P9 revenue redistribution ledger + referrals

## Security / safety defaults
- Passwords hashed (bcrypt, cost 12); API keys stored only as SHA-256 hashes and shown once.
- `AUTO_PAYOUTS_ENABLED=false` and `NOWPAYMENTS_PAYOUTS_ENABLED=false` by default — payouts require manual admin approval.
- Helmet, CORS allowlist, env validation at startup, RBAC guard on every route (opt-out via `@Public`), audit log on admin/auth actions.
- No private keys or provider API keys exposed to the frontend.

## Seed admin
Set `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD` and run `pnpm db:seed`, or add your email to `ADMIN_EMAILS` — you're auto-promoted to ADMIN on register/login.

## Reuse existing Animica infrastructure (don't reinvent)
This box already runs services the platform should integrate with rather than duplicate:
- **Live mining pool API** at `http://127.0.0.1:8550` (ANM + XMR dual, per-miner stats, payouts). Point `ANM_POOL_API_URL` / `XMR_POOL_API_URL` at it; the P4 mining module is an adapter over this (set `MINING_USE_MOCK=false` once wired). Endpoints: `/api/pool/summary`, `/api/miners`, `/api/miners/{id}`, `/api/pool/xmr/summary`, `/api/blocks/recent`.
- **NOWPayments credentials** already on disk in `/etc/animica/animica-gateway.env` (API key, merchant email/password). Copy into this platform's `.env` for P3/P8 — the IPN secret there is still a dashboard placeholder and must be set for webhooks to verify.
- **Gmail SMTP** (`animicaorg@gmail.com`) in `/etc/animica/chat.env` if email sending is added.
- **ANM price** `0.00125` matches the `buy.animica.org` gateway setting.
- **Port map (avoid collisions)**: taken → 3010 buy, 3020 rig-rental app, 3333 pool stratum, 4400 chat, 4510 buy-sol, 8550 pool API, 5433–5438 Postgres, 6379/6381 Redis. This platform uses **3000 web / 4000 api / 5439 pg / 6380 redis** (all free).
- A **rig-rental marketplace** (`pool.animica.org/app`) and an **AI-broker** already exist in `pool.animica.org-app`; this greenfield platform supersedes them per the chosen architecture — plan to migrate/redirect rather than run both long-term.
