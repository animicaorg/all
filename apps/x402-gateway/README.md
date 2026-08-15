# x402 gateway — agent payments scaffold

> **Status: scaffold, reviewed, NOT deployed.** Nothing here is wired into
> nginx or systemd, and the code refuses to run without `ANM_X402_ENABLED=1`.
> Enabling it for real requires three decisions that are explicitly NOT made
> here:
>
> 1. **CDP facilitator account** for the USDC lane (or PayAI, or staying on
>    the x402.org testnet facilitator) — an operator/business decision.
> 2. **wANM mint + treasury configuration** — the real mint address lives
>    with the solana.animica.org bridge config, and the treasury and fee-payer
>    sponsor keys must be provisioned and funded (the sponsor pays SOL fees).
> 3. **Which endpoints get paid tiers** — an operator decision per service;
>    this repo ships only a demo echo route.

## What this is

[x402](https://x402.org) is the HTTP-402 payments protocol used by agentic
clients (canonical spec: `github.com/x402-foundation/x402`, protocol
version 2). A server answers unpaid requests with `402` + machine-readable
payment requirements; the client pays with a signed payload in a header; a
*facilitator* verifies and settles on-chain; the server then serves the
resource and attaches a settlement receipt header.

This scaffold gives Animica services a drop-in gate with **two lanes** in
every offer:

| Lane | Rail | Facilitator | Why |
|------|------|-------------|-----|
| A | **USDC on Base** (`exact` EVM scheme, CAIP-2 `eip155:8453` / `eip155:84532`) | External, CDP-compatible (`X402_EVM_FACILITATOR_URL`) | Meets agents where they already are; gets endpoints indexed by the Bazaar / x402scan ecosystem |
| B | **wANM** (SPL token from the solana.animica.org bridge, `exact` SVM scheme) | **Local self-facilitator** (`src/facilitator.js`) | ANM utility: agents can pay in wrapped ANM with no third party between payer and treasury |

## Architecture

```
agent client
   │  GET /paid/echo
   ▼
demo-server.js ──► middleware.js (x402 gate)
   │  402 + PAYMENT-REQUIRED header (v2) + v1 JSON body
   │      accepts: [ USDC@Base , wANM@Solana ]
   │
   │  retry with PAYMENT-SIGNATURE (v2) or X-PAYMENT (v1)
   ▼
middleware ──POST /verify──► facilitator
   │                           ├─ EVM lane: external CDP-compatible URL
   │  serve resource           └─ SVM lane: facilitator.js (this repo)
   │  (held, not delivered)         │ static tx checks + Solana JSON-RPC
   ├──POST /settle──────────────────┘ (getAccountInfo / sendTransaction /
   │                                   getSignatureStatuses via SOLANA_RPC_URL)
   ▼
200 + PAYMENT-RESPONSE header (settlement receipt)
```

* **Wire versions.** Spec v2 is in force (headers `PAYMENT-REQUIRED` /
  `PAYMENT-SIGNATURE` / `PAYMENT-RESPONSE`, base64 JSON, CAIP-2 networks).
  The still-common v1 wire (`402` JSON body, `X-PAYMENT` /
  `X-PAYMENT-RESPONSE`) is supported simultaneously: the 402 carries the v2
  object in its header and the v1 rendering in its body, and both inbound
  payment headers are accepted.
* **Flow.** Default `authorization` flow: verify → produce resource → settle
  → deliver. If settlement fails, the resource is not delivered — we eat the
  compute, the payer keeps their money.
* **Self-facilitator** (`src/facilitator.js`) implements the spec §7
  facilitator contract (`POST /verify`, `POST /settle`, `GET /supported`)
  for the `exact` SVM scheme: the accepts entry advertises
  `extra.feePayer` = our sponsor pubkey; the client submits a partially
  signed `TransferChecked` with our sponsor as fee payer; `/verify` does the
  spec's static-layout check (1–7 instructions, only compute-budget ≤5
  lamports/CU, memo, wallet-injected Lighthouse guards, and exactly one
  `TransferChecked`; authority signature cryptographically verified;
  fee-payer isolation — the sponsor may appear nowhere but the fee slot)
  plus RPC checks (destination token account owned by `payTo` with the right
  mint; source funded); `/settle` signs as fee payer, broadcasts, and polls
  for confirmation. Replay protection keys on `sha256(messageBytes)` with an
  atomic check-and-mark before broadcast, closing the spec's
  duplicate-settlement race (in-memory — a real deployment needs a
  persistent store, noted in the source).
* Zero npm dependencies (house rule, same as animica-pay): base58, compact-
  u16, transaction parsing and ed25519 are done with `node:crypto` and
  BigInt. SPL program ids are cross-checked against the bridge's sources.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `ANM_X402_ENABLED` | `0` | Master switch. Anything but `1` → middleware answers 503 and servers refuse to start. |
| `X402_RESOURCE_BASE_URL` | `http://127.0.0.1:4656` | Public base URL used in `resource.url` of the 402 offer. |
| `X402_SERVICE_NAME` | `Animica` | `resource.serviceName` (≤32 ascii). |
| `X402_NETWORK_EVM` | `eip155:84532` (Base Sepolia) | CAIP-2 network for the USDC lane. Mainnet: `eip155:8453`. |
| `X402_USDC_ASSET` | per-network well-known USDC | USDC contract address. Verify against Circle's list before enabling. |
| `X402_BASE_PAYTO` | *(empty — lane off)* | EVM address receiving USDC. |
| `X402_EVM_FACILITATOR_URL` | `https://x402.org/facilitator` | Remote facilitator URL (`X402_FACILITATOR_MODE=remote`). Mainnet remote option: `https://facilitator.payai.network`; production goal is `X402_FACILITATOR_MODE=self` (our own `src/facilitator-evm/`). |
| `X402_NETWORK_SVM` | `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1` (devnet) | CAIP-2 network for the wANM lane. Mainnet: `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`. |
| `WANM_MINT` | *(empty — lane off)* | wANM SPL mint. **The real mint lives with the solana.animica.org bridge config — never hardcode it.** |
| `WANM_TREASURY` | *(empty — lane off)* | Owner wallet that receives wANM (`payTo`). |
| `WANM_DECIMALS` | `9` | wANM decimals (bridge default is 9, matching ANM nano-units). |
| `WANM_USD_PRICE` | *(empty — lane off)* | USD per 1 wANM, decimal string, for pricing USD-quoted routes. |
| `WANM_FEEPAYER_PUBKEY` | *(empty — lane off)* | Sponsor pubkey advertised as `extra.feePayer` (middleware side). |
| `WANM_FEEPAYER_SECRET` | *(empty — ephemeral)* | 32-byte ed25519 seed (hex or base58) for the facilitator's sponsor. Unset → ephemeral keypair + loud warning. |
| `X402_SVM_FACILITATOR_URL` | `http://127.0.0.1:4655` | Where the middleware reaches the local facilitator. |
| `SOLANA_RPC_URL` | *(empty — required by facilitator)* | Solana JSON-RPC. The repo already has a server-side QuikNode endpoint configured elsewhere — inject it via env, never paste it into source. |
| `X402_FACILITATOR_PORT` | `4655` | Facilitator listen port (loopback). |
| `X402_DEMO_PORT` | `4656` | Demo server listen port (loopback). |
| `X402_MAX_TIMEOUT_SECONDS` | `60` | `maxTimeoutSeconds` advertised in offers. |

No variable ever contains a default secret, and nothing network-facing starts
without the flag.

## Run the demo

```sh
cd apps/x402-gateway

# facilitator (wANM lane), terminal 1:
ANM_X402_ENABLED=1 SOLANA_RPC_URL=<injected> node src/facilitator.js

# demo server, terminal 2 (configure the lanes you want offered):
ANM_X402_ENABLED=1 \
  WANM_MINT=<mint> WANM_TREASURY=<owner> WANM_USD_PRICE=0.05 \
  WANM_FEEPAYER_PUBKEY=<sponsor pubkey printed by the facilitator> \
  node src/demo-server.js

curl -i http://127.0.0.1:4656/free/ping     # 200, free
curl -i http://127.0.0.1:4656/paid/echo     # 402 + PAYMENT-REQUIRED offer
```

## Products layer (production entry: `src/server.js`)

`node src/server.js` (binds `127.0.0.1:8742` by default) serves the real
paid catalog on top of the core; `demo-server.js` stays the dev/smoke
entry. Everything is config-driven — see `.env.example` for every knob.

| Product | Route(s) | Price (default) | Notes |
|---|---|---|---|
| `qrng` | `GET /x402/qrng/draw`, `POST /x402/qrng` | `$0.01` (`X402_QRNG_PRICE_USDC`) | wraps the node's real `rand.quantumRandomBytes`; readiness = an actual health-gated randomness fetch BEFORE any payment is requested; fields pass through verbatim (`attested:false` today — honesty enforced) |
| `bulk_chain` | `GET /x402/chain/export`, `/x402/chain/blocks`, `/x402/chain/transactions` | `$0.05` (`X402_BULK_CHAIN_PRICE_USDC`) | batched loopback-node reads, ≤1000 blocks / ≤10000 tx rows / byte+time budgets, cursor pagination, NDJSON/JSON + gzip, BigInt-exact amounts as decimal strings |
| `priority_inference` | `POST /x402/v1/chat/completions` | `$0.10` (`X402_INFERENCE_PRICE_USDC`) | **disabled by default** (`PRIORITY_INFERENCE_ENABLED=0`) and capacity-gated on live `aicf.workerStatus` polling; below the worker floor it never issues a 402 |
| `echo` | `GET/POST /x402/paid/echo` | `$0.005` | development-only settlement smoke test; off when `X402_ENV=production` unless `X402_ENABLE_ECHO=1` |

* **Discovery**: `GET /x402` and `GET /.well-known/x402` — unpaid,
  machine-readable `{name, network, asset, products:[{id, path, price,
  description, available, outputSchema…}]}` with LIVE `available` flags from
  each product's readiness hook.
* **Idempotency**: send `Idempotency-Key` with a paid request; the same
  payment + key that already produced a delivered outcome replays the
  stored result with no facilitator round-trip and no second charge.
* **Payment-then-service failure**: settle-first products (bulk, inference)
  retry once on downstream failure, then return an HMAC-signed error
  receipt (`X402_RECEIPT_HMAC_KEY`) referencing the settled payment and
  open an incident row for reconciliation/refund.
* **Admin CLI**: `bin/animica-x402` reads the DBs directly —
  `settlements list --status failed`, `payment get <id>`, `revenue --since
  24h`, `reconcile` (settled rows vs chain receipts via `X402_RPC_URL`),
  `gas report`, `incidents list|resolve`. `--json` everywhere.

## Tests

```sh
node --test test/
```

No network anywhere: the Solana/EVM/Animica-node RPCs are mocked (the QRNG
mock replays a captured real node response) and all servers run on
loopback. Covered: 402 shape against the v2 spec fields (and
v1 body rendering), verify/settle happy path including a check that the
broadcast transaction carries a valid fee-payer signature, concurrent
settles of the same transaction (exactly one may win), Lighthouse guard
allowance, and rejection paths — wrong amount, wrong mint, replayed
transaction, missing authority signature, foreign fee payer, sponsor
appearing outside the fee slot, unknown program instruction,
non-TransferChecked token instruction, wrong destination owner, insufficient
funds, tampered offer terms, and the kill switch.

## Two-lane strategy (why both)

* **Base-USDC** is the discoverability lane: x402 agent tooling, the
  Bazaar (`GET /discovery/resources`) and x402scan all index Base
  endpoints, and USDC is what agent wallets hold today. Settled either by
  our own `src/facilitator-evm/` (`X402_FACILITATOR_MODE=self`, the
  production goal — no third party) or a remote facilitator such as PayAI.
* **Self-facilitated wANM** is the utility lane: payments land directly in
  the Animica treasury as wrapped ANM with no external facilitator, which
  both dogfoods the solana.animica.org bridge and creates a real ANM demand
  path for agents. It costs us running the facilitator (sponsor SOL, RPC)
  and gives sovereignty.

Offering both in every 402 lets the client pick; the spec is explicitly
multi-rail, so this is idiomatic, not clever.

## Files

```
src/config.js            env names, CAIP-2 constants, BigInt money math,
                         gateway + facilitator config loaders (fail closed)
src/protocol.js          v2 + v1 wire shapes, header codecs, offer validation
src/middleware.js        the scaffold gate + the §7 facilitator HTTP client
src/paywall.js           production gate: availability -> 402 -> verify ->
                         settle -> execute, idempotency, signed error receipts
src/server.js            production entry: catalog + product routes (:8742)
src/products/            registry, qrng, bulk-chain, priority-inference, echo
src/capacity.js          serving-worker gate (aicf.workerStatus polling)
src/animica-node.js      Animica node JSON-RPC client (BigInt-safe, single-flight)
src/bech32m.js           anim1… address -> account digest
src/receipts.js          HMAC-signed machine-readable error receipts
src/store/index.js       facilitator settlement/replay ledger (better-sqlite3)
src/store/gateway.js     gateway idempotency + incident store (own DB file)
src/facilitator-evm/     self-hosted exact-EVM facilitator (Base USDC, EIP-3009)
src/facilitator.js       legacy SVM (wANM) self-facilitator
src/solana.js            base58 / compact-u16 / tx parsing / ed25519 / SPL / RPC
src/demo-server.js       dev/smoke entry: /free/ping + /paid/echo
bin/animica-x402         operator CLI (settlements, revenue, reconcile, gas,
                         incidents) — local DBs only
test/                    node --test suite (mocked RPC, loopback HTTP)
```

Design doc: [`docs/x402.md`](../../docs/x402.md) in the repo root.
