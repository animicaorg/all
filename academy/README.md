# Animica Academy

Interactive tutorials for the Animica stack with on-chain ANIMICA rewards.

The site has two halves:

- `web/` — Astro + Tailwind static site (academy.animica.org). Tutorial
  content lives in `web/src/content/tutorials/*.md` and is rendered as
  interactive step blocks with attestation buttons.
- `api/` — Express + better-sqlite3 backend (api.academy.animica.org).
  Tracks per-wallet progress, verifies signed attestations, dispatches
  payouts from the reward-pool address via the connected node's
  `animica.sendFromKeystore` RPC.

## How rewards flow

1. The user installs the [wallet extension](https://animica.org/wallet) and
   visits academy.animica.org. The site detects `window.animica` and shows
   a connect pill in the header.
2. The user works through a tutorial. Each step has a **Mark complete**
   button; client-side state lives in localStorage and the backend's
   `/progress` table.
3. When every step is done, the user clicks **Claim reward**. The flow is:
   - The frontend calls `/claim/start` to get a one-shot nonce.
   - The wallet extension signs a deterministic message containing the
     tutorial ID, step ID, address, and nonce.
   - The frontend submits the signed payload to `/claim/finalize`.
   - The backend verifies the signature via `animica.verifyMessage`,
     debits the in-memory pool stat, and asks the node to send the payout
     via `animica.sendFromKeystore`.
4. Completing every tutorial in a track auto-awards a track badge with a
   bonus payout from the same pool.

## How the reward pool gets funded

Anyone with ANIMICA can deposit into the pool from `/rewards`:

- The frontend renders a deposit form pre-filled with the public pool
  address.
- The wallet extension renders the standard confirmation UI (destination,
  amount, fee). The extension is the only thing that holds the key; the
  academy never asks for it.
- After broadcast, the frontend records the tx via `/pool/deposit` so the
  contributor leaderboard reflects the deposit promptly. The chain
  indexer reconciles asynchronously regardless of whether this endpoint
  is called.

## Running locally

Requires Node 20+, pnpm 9+, and a running animica node on the configured
RPC endpoint.

```bash
# in two terminals:
cd academy/api && pnpm install && cp ../.env.example .env && pnpm dev
cd academy/web && pnpm install && pnpm dev
```

The frontend dev server runs on `:4323`, the backend on `:4324`. The web
build embeds the API URL from `ACADEMY_API_BASE_URL` at build time.

## Production deployment notes

- Run the backend behind a TLS-terminating reverse proxy. The Express
  server itself binds plaintext and trusts the proxy's `X-Forwarded-*`.
- `ACADEMY_PAYOUTS_ENABLED=1` only when a funded keystore is on the node.
  Leave it `0` in staging — claims still succeed (`paid:true, txHash:null`)
  so you can verify the flow without depleting real ANIMICA.
- The CORS allowlist is enforced; production must include
  `https://academy.animica.org` (and only that domain) under
  `ACADEMY_CORS_ORIGINS`.
- The chain consensus is unaffected by the academy. It is purely a
  client-side overlay that records progress and dispatches transfers from
  a regular address.
