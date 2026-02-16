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

### Wallet connect env vars
- `WALLET_CONNECT_SIGNING_KEY`: server-side HMAC key for signing connect payloads and nonce proofs.
- `WALLET_CONNECT_CALLBACK_URL` (optional): explicit callback URL exposed to wallet.
- `NEXT_PUBLIC_APP_ORIGIN` (optional): canonical app origin for deep link payload.
- `WALLET_MOCK=1`: enables developer mock approval endpoint (`/api/wallet/mock-approve`).
- `DEV_SIGNER_KEY` (optional): enables gated dev signer fallback for local tests.

## Local Run
1. `docker compose -f docker/docker-compose.yml up -d`
2. `npm install`
3. `npx prisma migrate dev`
4. `npm run dev`
5. `npm run worker`

Alternative helper:
- `bash scripts/dev.sh`

## Deep link wallet flow
- Start endpoint: `POST /api/wallet/connect/start`
  - creates `WalletConnectRequest` in DB
  - signs `ConnectRequest` payload with HMAC
  - returns:
    - `animicawallet://connect?request=<base64url>`
    - `https://wallet.animica.org/connect?request=<base64url>`
- Callback endpoint: `POST /api/wallet/callback`
  - validates request expiry and signed nonce proof
  - verifies stored payload signature
  - stores `WalletSession` when approved
- Poll endpoint: `GET /api/wallet/connect/status?requestId=<id>`
- Mock mode: `POST /api/wallet/mock-approve`
  - simulates wallet approval roundtrip for end-to-end local tests when `WALLET_MOCK=1`

## Key features
- Mobile-first app shell (bottom tabs on mobile, sidebar/header on desktop).
- Wallet connect modal with extension + deep link + mock approval path.
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
