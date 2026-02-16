# Animica Studio (chat-animica)

Production-ready Next.js app for Animica contract generation + deploy.

## Dev Quickstart
1. `docker compose -f docker/docker-compose.yml up -d`
2. `pnpm install`
3. `cp .env.example .env`
4. `pnpm prisma:migrate`
5. `pnpm dev`
6. `pnpm worker`

## Modal Setup (1 minute)
1. Copy env template: `cp .env.example .env`
2. Set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `.env`
3. Run `pnpm dev`

What happens on first run:
- `scripts/modal-bootstrap.ts` auto-creates `.venv-modal`
- installs pinned Modal runtime deps
- deploys `modal/modal_app.py`
- writes discovered endpoint to `.modal-endpoint`
- app starts using Modal endpoint automatically

If Modal credentials are missing or deploy fails, chat falls back to a local stub provider with clear logs.

## Feature flags
- `ENABLE_WALLET_PROD_SIGNING=1`: allows wallet session signer path.
- `DEV_SIGNER_KEY=<secret>`: enables local key fallback signer for development.

## Deploy flow (dev signer vs wallet signer)
1. Build deploy transaction payload as CBOR-ish map (`buildDeployCborTx`).
2. Select signer:
   - `dev`: signs server-side with HMAC key (`DEV_SIGNER_KEY`) for local testing.
   - `wallet`: requires wallet session and production signing flag.
   - `extension`: accepts externally signed raw transaction.
3. Submit with RPC auto-negotiation (`tx.sendRawTransaction` variants).
4. Poll `tx_getTransactionReceipt` / `tx.getTransactionReceipt` until receipt or timeout.
5. Display hash, receipt, explorer link.

## Troubleshooting
- **Modal auth errors (401/403)**: verify `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `.env`. Re-run `pnpm modal:deploy`.
- **Deploy failure / endpoint missing**: run `pnpm modal:deploy` and inspect output; ensure Python 3 + network access are available. Endpoint should land in `.modal-endpoint`.
- **Endpoint override needed**: set `MODAL_ENDPOINT_URL=https://...` in `.env` to bypass auto-discovery.
- **Timeouts from LLM calls**: check `pnpm modal:logs`; app auto-falls back locally if Modal is unreachable.
- **Local fallback unexpectedly active**: delete stale `.modal-endpoint`, verify endpoint URL is HTTPS, then redeploy.
- **Compiler failures**: install `animica-compiler` binary in PATH for worker compile jobs.

## Included capabilities
- Strict/Possibility mode persisted per-user.
- Project memory persistence (server-side JSON file) with latest + last 10 revisions.
- Knowledge pack build button with status + last built timestamp.
- Diagnostics drawer (mode, topK, validator status, rewrite count, request id).
- Copy actions for tx hash, rawTx, receipt.
