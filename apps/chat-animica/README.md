# Animica Studio (chat-animica)

Production-ready Next.js app for Animica contract generation + deploy.

## Dev Quickstart
1. `docker compose -f docker/docker-compose.yml up -d`
2. `npm install`
3. `npx prisma migrate dev`
4. `npm run dev`
5. `npm run worker`

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
- **LLM errors**: ensure `MODAL_CHAT_URL` is set and reachable.
- **Deploy signer blocked**: verify selected signer + feature flags.
- **RPC submit failures**: use “Send Test Tx” utility and diagnostics drawer to inspect attempted method + params.
- **Compiler failures**: install `animica-compiler` binary in PATH for worker compile jobs.

## Included capabilities
- Strict/Possibility mode persisted per-user.
- Project memory persistence (server-side JSON file) with latest + last 10 revisions.
- Knowledge pack build button with status + last built timestamp.
- Diagnostics drawer (mode, topK, validator status, rewrite count, request id).
- Copy actions for tx hash, rawTx, receipt.
