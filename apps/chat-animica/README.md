# Animica Studio Web IDE

Production-ready Next.js web IDE for generating Animica smart contracts, running guarded compile/simulate/deploy jobs, and enforcing paid subscription access.

## Stack
- Next.js 14 (App Router), TypeScript, Tailwind
- Prisma + Postgres
- Redis + BullMQ
- JWT magic link auth via SMTP/dev logging
- PayPal subscriptions ($20/mo, pre-created PLAN_ID)
- Modal Python LLM service (`modal/app.py`)

## Folder Layout

```text
apps/chat-animica/
  app/
  src/
    server/
    shared/
    modal/
  modal/
  prisma/
  scripts/
  docker/
  README.md
```

## Environment
Copy `.env.example` to `.env` and fill credentials.

## Local Run
1. `docker compose -f docker/docker-compose.yml up -d`
2. `npm install`
3. `npx prisma migrate dev`
4. `npm run dev`
5. `npm run worker`

Alternative helper:
- `bash scripts/dev.sh`

## Key features
- `/api/auth/request-link`, `/api/auth/verify` for magic link auth.
- `/pricing` PayPal subscribe CTA, `/api/paypal/checkout` checkout creation, `/api/paypal/webhook` signature-verified event ingestion.
- `/api/chat` server-side subscription + daily rate-limit checks before Modal call.
- Defensive Animica RPC pipeline in `src/server/rpc/animicaRpc.ts`:
  - Discovery + Redis caching
  - Send-method variant resolution
  - Param encoding fallback on `-32602`
  - Optional explain-reject enrichment
- BullMQ jobs for compile/simulate/deploy/status tracking.

## Modal deploy
From `apps/chat-animica/modal`:
- `modal deploy app.py`

Set `MODAL_CHAT_URL` to the deployed `/chat` endpoint.
