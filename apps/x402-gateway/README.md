# x402 gateway + self-hosted facilitator

> **Status (2026-08-15): Base-USDC x402 stack, facilitator SELF-HOSTED by
> default (`X402_FACILITATOR_MODE` defaults to `self`; `remote` requires an
> explicit URL and has no fallback) — no third-party settlement dependency,
> no Coinbase services anywhere.
> The wANM/Solana lane is RETIRED** (code kept in-tree as legacy, never
> configured; see "Retired wANM lane" below). Deployment is a separate
> human-approved runbook step: the live `animica-x402.service` still runs
> the dev entry (`src/demo-server.js`), and the systemd/nginx files in this
> repo are **examples, not installed state**. Everything refuses to run
> without `ANM_X402_ENABLED=1`.

Sells Animica capabilities per-call over the open
[x402 protocol](https://github.com/x402-foundation/x402) (v2 wire + v1
compat): the server answers `402` with machine-readable payment
requirements, the client signs a USDC EIP-3009 authorization locally,
retries with a header, our facilitator settles it on Base and the resource
is delivered with a settlement receipt header. Payers never spend gas and
never send us a key.

## Quickstart

```sh
cd apps/x402-gateway
node --test test/                       # the full suite, no network anywhere

# dev demo (what the live unit runs): /free/ping + /paid/echo on :4656
ANM_X402_ENABLED=1 node src/demo-server.js

# production gateway (product catalog on 127.0.0.1:8742)
ANM_X402_ENABLED=1 node src/server.js
curl -s http://127.0.0.1:8742/x402 | python3 -m json.tool     # discovery
curl -si http://127.0.0.1:8742/x402/qrng/draw | head -5       # 402 offer

# full local loop incl. the self-hosted facilitator + a REAL testnet
# settlement: follow test/manual/base-sepolia.md, then pay with
#   SMOKE_PRIVATE_KEY=… node test/manual/smoke-pay.mjs <url>
```

## Architecture

```
                    https://animica.dev/x402/…       (nginx/animica-dev-x402.conf:
                              │                       per-product locations, rate
                              ▼                       limits, request-id forwarding)
             ┌─ x402 GATEWAY  127.0.0.1:8742 ── src/server.js ──────────────┐
             │  discovery: /x402 (catalog or landing page), /.well-known/    │
             │  x402, /x402/openapi.json, /x402/stats — free, generated from │
             │  the registry, live availability                              │
             │  paywall (src/paywall.js): validate → availability (503, no   │
             │  402 when down) → 402 offer → tamper-proof match → idempotency│
             │  replay → verify → [preSettle readiness] → settle → execute   │
             │  products/: echo(dev) qrng($0.01) bulk_chain($0.05)           │
             │             chain_address_history($0.05) balances($0.02)      │
             │             priority_inference($0.10, capacity-gated, OFF)    │
             │  chain-index/: head-following address index + walker          │
             │  stores: state/x402-gateway.db (idempotency + incidents)      │
             │          state/x402-chain-index.db (address index, own file)  │
             └───┬───────────────────────────────┬───────────────────────────┘
                 │ x402 v2 §7: /verify /settle    │ JSON-RPC (single-flight,
                 ▼                                ▼  BigInt-safe)
   ┌─ FACILITATOR 127.0.0.1:8743 ─────────┐   Animica node 127.0.0.1:8545/rpc
   │ src/facilitator-evm/server.js        │   (qrng draws, block exports,
   │ EIP-3009 verify: sig recovery, chain │    aicf.workerStatus capacity)
   │ time window, balance, nonce unused   │
   │ (chain + DB); settle: atomic claim → │        Base RPC (X402_RPC_URL)
   │ estimateGas(=simulation) → sign      │◄──────► eth_call / estimateGas /
   │ EIP-1559 → persist → broadcast →     │         sendRawTransaction /
   │ receipt + log match + confirmations  │         receipts
   │ ledger: state/x402.db (UNIQUE        │
   │ authorization_hash = replay arbiter) │
   │ treasury/ (single-wallet mode, OFF   │   Uniswap v3 on Base
   │ by default): own timer + fire-and-   │◄──────► QuoterV2 quote,
   │ forget post-settlement trigger; ETH  │         SwapRouter02 multicall
   │ < floor → adaptive USDC→ETH sip;     │         (swap+unwrap), USDC
   │ USDC > ceiling → sweep to COLD       │         approve/transfer
   └──────────────────────────────────────┘
```

Two separable layers: the **gateway** (products, paywall, discovery) speaks
to any x402-v2-§7 facilitator; the **facilitator** is ours by default
(`X402_FACILITATOR_MODE=self` — the default with nothing configured, and
set explicitly in `systemd/animica-x402.service`) and swappable for a
remote one (`remote` + `X402_FACILITATOR_URL`, e.g. PayAI) with zero
product changes. There is **no default remote URL**: `mode=remote` without
an explicit URL refuses to start, because a fallback endpoint would route
real payments through whoever it named. `X402_NETWORK_EVM` likewise
defaults to Base **mainnet** (`eip155:8453`) and must agree with the
facilitator's `X402_NETWORK` in self mode. `test/honesty-guards.test.js`
asserts these claims against the code that has to make them true.

## Products

| id | route(s) | price (default) | mode | notes |
|---|---|---|---|---|
| `qrng` | `GET /x402/qrng/draw`, `POST /x402/qrng` | $0.01 `X402_QRNG_PRICE_USDC` | execute-then-settle | wraps the node's real `rand.quantumRandomBytes`; health-gated readiness probe BEFORE any 402; attestation fields pass through verbatim (`attested:false` today — honesty enforced, and published UNPAID in the catalog + 402 offer); the POST form reads its JSON body (a body parameter is never silently discarded) |
| `random_int` | `POST /x402/random/int` | $0.01 `X402_RANDOM_INT_PRICE_USDC` | execute-then-settle | uniform ints in `[min,max]`, ≤1,000 per call, **rejection sampling** (documented, no modulo bias); one draw per request, derived |
| `random_shuffle` | `POST /x402/random/shuffle` | $0.02 `X402_RANDOM_SHUFFLE_PRICE_USDC` | execute-then-settle | Fisher-Yates permutation of your list or of `1..N`, ≤10,000 items; returns the index permutation + the shuffled items |
| `random_pick` | `POST /x402/random/pick` | $0.02 `X402_RANDOM_PICK_PRICE_USDC` | execute-then-settle | k picks with/without replacement, optional **integer** weights (cumulative-weight search over one uniform draw) — raffles, sortition, A/B splits; the estimated RESPONSE size is capped pre-settlement (`X402_RANDOM_MAX_RESPONSE_BYTES`), with `indices_only` for large items |
| `random_bulk` | `POST /x402/qrng/bulk` | $0.05 `X402_RANDOM_BULK_PRICE_USDC` | execute-then-settle | 6–10 **INDEPENDENT** draws (one node call + one attestation each) × ≤1,024 bytes in ONE settlement. The minimum draw count is derived from the price table so the batch always beats the same number of single draws; below it a 400 names the cheaper endpoint, and a price that can never be a discount makes the product `available:false` |
| `random_commit` | `POST /x402/random/commit` + **free** `GET /x402/random/reveal/{id}` | $0.02 `X402_RANDOM_COMMIT_PRICE_USDC` | execute-then-settle | commit-reveal: `sha3_256(secret‖salt)` now, free public idempotent reveal later (425 while sealed). Ships its own trust model — the commitment binds from publication; it does NOT prove the operator discarded no draws |
| `bulk_chain` | `GET /x402/chain/export\|blocks\|transactions` | $0.05 `X402_BULK_CHAIN_PRICE_USDC` | settle-then-execute | ≤1,000 blocks / ≤10,000 tx rows / byte+time budgets, cursor pagination, NDJSON/JSON, gzip; amounts = decimal strings (nANM); loopback node only, chunked + single-flight; a window above `head − margin` is refused pre-settlement (`window_not_yet_final`) instead of sold empty |
| `chain_address_history` | `POST /x402/chain/address-history` | $0.05 `X402_CHAIN_HISTORY_PRICE_USDC` | execute-then-settle | full account history from the gateway's OWN head-following sqlite index (`src/chain-index/`); ≤500 rows/call, stable `<as_of>:<height>:<tx_index>` cursor, published direction/ordering/digest derivation; **fails closed (503, no 402) while the index is backfilling, stalled or lagging** |
| `chain_batch_balances` | `POST /x402/chain/balances` | $0.02 `X402_CHAIN_BALANCES_PRICE_USDC` | settle-then-execute | ≤500 addresses in ONE batched RPC (~5 ms/address), deduped, BigInt-exact nANM decimal strings, per-entry errors instead of a poisoned batch; single lookups stay free |
| `priority_inference` | `POST /x402/v1/chat/completions` | $0.10 `X402_INFERENCE_PRICE_USDC` | settle-then-execute | **DISABLED by default** (`PRIORITY_INFERENCE_ENABLED=0`) AND capacity-gated on live `aicf.workerStatus` polling; below the worker floor: catalog `available:false`, clear 503, **never a 402** |
| `echo` | `GET/POST /x402/paid/echo` | $0.005 | execute-then-settle | development-only settlement smoke marker; off when `X402_ENV=production` unless `X402_ENABLE_ECHO=1` |

Cross-product guarantees (all in `src/paywall.js`, tested in
`test/gateway.test.js`): an unavailable product never requests payment;
settle-first products re-check readiness immediately before settlement;
after a settled payment **anything** that fails — the downstream service,
the response serialization, the idempotency write — produces an HMAC-signed
error receipt + an incident row and a 502 (`downstream_failed`,
`delivery_failed`, `settle_unknown`), never a bare 500 that reads as "you
were not charged"; execute-then-settle products serialize the response
BEFORE settling, so an unserializable answer charges nobody;
`Idempotency-Key` + same payment replays the stored result with no second
charge; one delivered success == exactly one settlement; failed/replayed
payments never count as revenue.

### The randomness family (`src/products/random.js` + `derive.js`)

All five derive from **one** verified `rand.quantumRandomBytes` draw per
request (never one node call per output item) through the shared source in
`src/products/qrng.js`, and they share its health-gated readiness probe — a
sick entropy source refuses the whole family with a 503 and **no 402**.

Every response carries the raw `randomness` bytes, the node's
`source`/`health`/`attestation` verbatim, the `verification` rules, and a
`derivation` block with the exact rule, the ordered steps and the stream
byte-consumption. The derivations are byte-for-byte the canonical Animica
DRNG (`randomness/qrng/public.py` and its dependency-free JS twin
`randomness/beacon_api/static/verify.js`), so a buyer recomputes offline —
and where a stock `verify.js` kind applies, `derivation.recompute` drops
straight into `AnimicaBeacon.verifyResult()`. `test/random.test.js` pins
this with golden vectors *and* a live cross-check against that file.

Honesty, unchanged from `qrng`: the source is `software-fallback`
(os.urandom) with a software signer, so `attested:false` and
`is_quantum:false` — no description, example or doc here implies hardware
or quantum attestation, and `test/honesty-guards.test.js` enforces it.
Crucially the trust model is knowable **before paying**: the readiness
probe's observation is published unpaid as `entropy {source, is_hardware,
is_quantum, attested, health_passed, min_entropy_per_byte, observed_at}` on
every randomness entry in `GET /x402` and inside every 402 offer's
`extensions.bazaar.info`. `random_bulk` is the one product that makes N
node calls instead of one — independence is what it sells, and its
break-even minimum is enforced rather than claimed.

### The address index (`src/chain-index/`)

`chain_address_history` is the only chain SKU with a real advantage over the
free surfaces, and the advantage IS the index. Measured 2026-08-15: the node
RPC has **no** history method at all, and
`explorer.animica.org/api/address/:bech32` is a live reverse block scan
capped at 250 blocks / 3.5 s per call — it took 3.54 s to return **zero**
txs for the chain's most active sender (whose txs sit ~3,000 blocks back).
So the gateway owns one:

- **store** (`store.js`) — its own sqlite file. `blocks(height PK, hash,
  parent_hash, timestamp, tx_count)` exists for exact reorg rollback;
  `address_tx(digest, height, tx_index PK, direction, tx_hash,
  counterparty, value, tip, gas, kind, block_hash, timestamp)` is the
  product's page order, so both `asc` and `desc` paging are index scans.
  Amounts are decimal TEXT — never a JS Number, anywhere.
- **walker** (`walker.js`) — backfills from genesis (~5–7 min for the
  ~73k-block chain at the measured 170–230 blocks/s) then follows the head.
  Politeness is enforced here, not asked of callers: the SAME single-flight
  node client the bulk export uses (so a backfill can never run concurrently
  with a paid export), 100-block batches (~0.43 s; a 1,000-block batch would
  hold the node's single event loop 5.8–8.6 s against miner getwork and
  wallets), a pause between chunks, an `unref()`'d timer, and **start() only
  from `main()`** — `createGateway()` never walks, which is why the test
  suite drives `tick()` explicitly and touches no real node.
- **gate** (`index.js::createIndexHealth`) — `chain_index_disabled`,
  `chain_index_node_unreachable`, `chain_index_never_ran`,
  `chain_index_walker_stalled`, `chain_index_backfilling`,
  `chain_index_stale`. All are 503 with progress and **never a 402**: a
  history page from an incomplete index is not stale, it is wrong.

Only blocks at or below `head − X402_CHAIN_INDEX_HEAD_MARGIN` are indexed,
so `X402_CHAIN_INDEX_MAX_LAG_BLOCKS` must exceed that margin — the config
refuses to load otherwise, because the gate could never open. A `parentHash`
break rewinds `X402_CHAIN_INDEX_REORG_REWIND` blocks and re-indexes instead
of stitching two histories together. Watch it with
`animica-x402 index status`.

## Discovery surfaces

Everything an agent, an x402 indexer, a crawler or a human needs to find
and understand these products, all **generated from the live product
registry** at request time — there is no second copy of a price, a path or
an availability flag anywhere in this app (`test/discovery.test.js` proves
it by moving a price and asserting every surface moves with it).

| route | what it is |
|---|---|
| `GET /x402` | content-negotiated: `Accept: text/html` → the landing page, anything else → the JSON catalog. `?format=json\|html` forces either (monitoring). A `*/*` client is NOT treated as wanting a web page |
| `GET /.well-known/x402` | the same catalog, **always JSON**, at the ecosystem-conventional location |
| `GET /x402/openapi.json` | OpenAPI 3.1 (`src/discovery/openapi.js`): every product route with its real input schema, the **402 challenge documented as a first-class response**, `x-payment-protocol: x402` and a per-route `x-payment-info` (price, atomic amount, network, chain id, asset, payTo, availability, documentation URL). No `securitySchemes`: there is no API key to document |
| `GET /x402/stats` | aggregate settlement counts from the facilitator's ledger, opened **read-only** (`src/discovery/stats.js`): settled total, last 24 h, per-product counts, network, asset. No payer addresses, no transaction hashes, no amounts. An unrecognised `resource` is bucketed as `other`, never echoed. Absent ledger (or `mode=remote`) reports `available:false` with a reason instead of a confident zero |
| `GET /x402/healthz`, `GET /metrics` | liveness and Prometheus text (loopback bind) |

The **catalog** (`src/products/registry.js::catalog`) carries the discovery
spec's identity block (`name`, `provider`, `homepage`, `gateway`,
`payment_protocol`, `network` slug + `network_caip2`, `chain_id`, `asset`
symbol + `asset_address`, `discovery.{catalog,well_known,openapi,stats}`)
and, per product, `{id, name, method, url, documentation, price,
price_atomic, currency, description, available (+ `unavailable_reason` /
`unavailable_detail`), endpoints, free_endpoints, outputSchema}` plus the
live `entropy` disclosure for the randomness family.

The **landing page** (`src/discovery/landing.js`) is self-contained: inline
CSS, no scripts beyond two JSON-LD blocks (`WebAPI` + `FAQPage`), no
external requests, served with a `default-src 'none'` CSP, light/dark
aware. Its prices, availability, endpoints and entropy disclosure come from
the catalog; its response examples are REAL captured responses
(`src/discovery/samples.js`, captured against the live node with a mocked
facilitator — the `payment` block is stripped rather than shown with a fake
Base transaction hash). The development `echo` marker is filtered out of
the landing page and the OpenAPI document in **every** environment, and out
of the catalog entirely when `X402_ENV=production`.

Every **402** additionally carries optional descriptive metadata under
`extensions.animica` (product id, name, description, price, currency,
content type, documentation URL, catalog + OpenAPI URLs, and a `terms`
block copied from the accepts entry being offered, so the descriptive price
can never disagree with the one being charged). The open-spec
`extensions.bazaar.info` discovery block that indexers read is untouched.
None of this is a mandatory protocol field: a client that ignores
`extensions` pays exactly as before.

## Environment

Template with every knob commented: [`.env.example`](.env.example) — copy
to a root-owned **0600** file (`/etc/animica-x402.env`); both systemd units
read it. The load is fail-closed: contradictions (chain id vs network,
non-allowlisted asset, malformed key, garbage price) refuse to boot.

| group | variables |
|---|---|
| master | `ANM_X402_ENABLED` (=1 or nothing runs), `X402_ENV` (`production` disables echo) |
| gateway | `X402_GATEWAY_BIND`/`PORT` (127.0.0.1:8742), `X402_GATEWAY_DB_PATH`, `X402_RESOURCE_BASE_URL`, `X402_SERVICE_NAME`, `X402_RECEIPT_HMAC_KEY` (**required in prod** — signs error receipts), `X402_IDEMPOTENCY_MAX_BODY_BYTES`/`TTL_SECONDS`, `X402_ANIMICA_RPC_URL` (loopback node only) |
| offers (EVM lane) | `X402_NETWORK_EVM` (CAIP-2), `X402_BASE_PAYTO`, `X402_USDC_ASSET` (defaults to canonical per-network USDC), `X402_MAX_TIMEOUT_SECONDS` |
| facilitator selection | `X402_FACILITATOR_MODE` (`self`\|`remote`), `X402_FACILITATOR_URL` (+ legacy alias `X402_EVM_FACILITATOR_URL`, still honored — the live unit's env uses it) |
| self-hosted facilitator | `X402_NETWORK` (`base`\|`base-sepolia` allowlist), `X402_CHAIN_ID` (must agree), `X402_ASSET` (`USDC` or the exact allowlisted address), `X402_RPC_URL` (+`_FALLBACK_URL`), `X402_SETTLEMENT_ADDRESS` (payTo — server config, never client input), `X402_FACILITATOR_PRIVATE_KEY`, `X402_FACILITATOR_BIND`/`X402_EVM_FACILITATOR_PORT` (127.0.0.1:8743), `X402_DB_PATH` |
| gas policy | `X402_MAX_GAS_PER_SETTLEMENT` (150k), `X402_MAX_FEE_PER_GAS_WEI` (1 gwei), `X402_DAILY_GAS_BUDGET_WEI` (0=off breaker), `X402_MIN_GAS_BALANCE_WEI` (readyz floor) |
| confirmation | `X402_CONFIRMATIONS` (2), `X402_RECEIPT_TIMEOUT_MS`, `X402_RECEIPT_POLL_MS`, `X402_EXPIRY_MARGIN_SECONDS` |
| treasury (sweep+sip) | `X402_TREASURY_ENABLED` (0), `X402_TREASURY_COLD_ADDRESS` (**required** in single-wallet mode; exactly EIP-55 checksummed, not in the reserved low-address range, not a contract unless `X402_TREASURY_COLD_ALLOW_CONTRACT`=1), `X402_TREASURY_ETH_FLOOR_WEI` (5e14, must be ≥3× `X402_MIN_GAS_BALANCE_WEI`), `X402_TREASURY_SIP_USDC` (5.00), `X402_TREASURY_SIP_MIN_USDC` (0.50), `X402_TREASURY_MAX_SLIPPAGE_BPS` (100), `X402_TREASURY_MAX_QUOTE_DEVIATION_BPS` (5000), `X402_TREASURY_RATE_REFERENCE_MAX_AGE_S` (7d), `X402_TREASURY_SIP_COOLDOWN_S` (86400), `X402_TREASURY_RETRY_COOLDOWN_S` (900), `X402_TREASURY_DAILY_SWAP_BUDGET_USDC` (10.00), `X402_TREASURY_USDC_CEILING` (20.00), `X402_TREASURY_MAX_SWEEPS_PER_DAY` (24), `X402_TREASURY_CHECK_INTERVAL_S` (300), `X402_TREASURY_MIN_SWEEP_USDC` (0.10), `X402_TREASURY_POOL_FEES` (500,100), `X402_TREASURY_SWAP_DEADLINE_S` (180), `X402_TREASURY_MAX_{SWAP,APPROVE,SWEEP}_GAS`, `X402_TREASURY_MIN_ETH_OUT_GAS_RATIO` (4), `X402_TREASURY_MAX_CONSECUTIVE_FAILURES` (2), `X402_TREASURY_STUCK_TX_S` (180), `X402_TREASURY_MAX_TX_BUMPS` (3), `X402_TREASURY_REFUEL_ALERT_TICKS` (3), `X402_TREASURY_LEASE_TTL_S` (900) |
| products | `X402_QRNG_*`, `X402_RANDOM_*` (`ENABLED`, the five `*_PRICE_USDC`, `SEED_BYTES`, `MAX_INTS`/`MAX_ITEMS`/`MAX_PICKS`/`MAX_BODY_BYTES`/`MAX_RESPONSE_BYTES`, `BULK_MAX_DRAWS`, `MAX_DRAW_BYTES`, `COMMIT_MAX_DELAY_SECONDS`, `COMMIT_TTL_SECONDS`), `X402_BULK_*`, `PRIORITY_INFERENCE_ENABLED`, `PRIORITY_INFERENCE_MIN_SERVING_WORKERS`, `X402_INFERENCE_*`, `X402_CAPACITY_*` |
| logging/rpc | `X402_LOG_LEVEL`, `X402_LOG_FORMAT`, `X402_RPC_TIMEOUT_MS`, `X402_RPC_RETRIES` |

## Ops / runbook

### What the facilitator wallet is (and is not)

The facilitator's key can do exactly two things: **spend its own ETH on
gas** (bounded by the per-settlement gas cap, the fee-per-gas cap and the
optional daily budget breaker) and **broadcast user-signed USDC
authorizations whose amount and destination it cannot alter** (EIP-3009 —
the payer signed `to = X402_SETTLEMENT_ADDRESS` and the exact value; the
facilitator is only the courier). In the **two-wallet posture** revenue
never touches it: USDC lands directly at `X402_SETTLEMENT_ADDRESS`, which
needs **no hot key at all** — keep it a cold address. Compromise of the
facilitator key = loss of its gas ETH balance, nothing more. The key exists
only in the 0600 env file and in process memory
(`src/facilitator-evm/key.js` — never logged, never serialized; only the
derived address appears anywhere).

In **single-wallet mode** (`X402_SETTLEMENT_ADDRESS` = the facilitator's own
address, the operator's chosen deployment) that last sentence changes:
revenue *does* transit the hot key, and compromise costs whatever is sitting
on it at that moment. The compensating control is the treasury module, which
drains everything above a small ceiling to a cold address — see [Treasury:
single-wallet mode + sweep and sip](#treasury-single-wallet-mode--sweep-and-sip)
below. The facilitator **refuses to start** in single-wallet mode without it.

### Key generation + funding

```sh
# generate (on the host, output goes straight into the 0600 env file):
node -e 'const s=require("@noble/secp256k1");console.log(Buffer.from(s.utils.randomSecretKey()).toString("hex"))'
# derive the address to fund:
X402_FACILITATOR_PRIVATE_KEY=<hex> node -e '
  const {loadSigner}=require("./src/facilitator-evm/key");
  console.log(loadSigner(process.env.X402_FACILITATOR_PRIVATE_KEY).address)'
```

**How much ETH-on-Base to hold:** one settlement costs ≈ 5.4×10¹¹ wei
(≈$0.002: ~86k gas at Base's ~0.006 gwei effective price + the OP-stack L1
data fee — both are summed into `x402_gas_spent_wei` and the ledger's
`gas_spent_wei`, measured live 2026-08-15). The `readyz` floor defaults to
0.0001 ETH (`X402_MIN_GAS_BALANCE_WEI`) ≈ ~185 settlements of headroom
(single-wallet mode additionally requires the treasury's refuel trigger to be
≥3× that, so refuelling starts while the facilitator is still ready);
holding **0.005–0.01 ETH** (~$20-40, roughly 9k-18k settlements) and
topping up on the `x402_facilitator_gas_balance_wei` gauge is comfortable.
Do not park more — the wallet is hot by definition. Margin note: at $0.01
QRNG pricing the gas cost is ~20% of revenue; watch
`x402_gas_spent_wei` vs `x402_revenue_usdc` and the daily budget breaker
(`X402_DAILY_GAS_BUDGET_WEI`) is the stop-loss if Base fees spike.

### Treasury: single-wallet mode + sweep and sip

`src/treasury/` keeps a single-wallet facilitator self-refuelling and nearly
empty. Full reference — policy, failure classes, MEV stance, cost tables,
verified contract set — in [`docs/x402.md`](../../docs/x402.md#treasury-single-wallet-mode--sweep-and-sip).
The short version:

* **Trade-off.** Single-wallet mode buys a loop that funds itself (each
  settlement collects ~10× the gas it costs at $0.01 pricing, so ~$2 of ETH
  once is enough forever) and costs the separation between gas float and
  revenue. Startup is **refused** unless `X402_TREASURY_ENABLED=1` and a
  checksummed `X402_TREASURY_COLD_ADDRESS` is set, because the sweep is the
  only thing that makes the trade defensible. Prefer the two-wallet posture?
  Point `X402_SETTLEMENT_ADDRESS` at a wallet the facilitator does not
  control and leave the treasury off — nothing else changes.
* **Sip.** ETH below `X402_TREASURY_ETH_FLOOR_WEI` (0.0005 ETH ≈ 900
  settlements of runway, deliberately well above the `/readyz` gas floor so a
  refuel never begins with the facilitator advertising itself unhealthy) →
  swap `min($5, balance)` but never under `$0.50`
  of USDC to ETH in one atomic SwapRouter02 `multicall(deadline,
  [exactInputSingle, unwrapWETH9])`, `amountOutMinimum` from a QuoterV2
  quote minus ≤1%, exact-amount approve (no standing allowance), one sip per
  24 h (halved below floor/2), hard $10/day budget.
  **Adaptive sizing is the point:** a fixed $5 floor deadlocks after a gas
  spike (ETH gone at ~75 settlements with only ~$0.75 accrued); a $0.50 sip
  buys ~490 settlements. That scenario is an explicit test.
* **Sweep.** USDC above `X402_TREASURY_USDC_CEILING` ($20) → ERC-20 transfer
  of `balance − ceiling` to the cold address, at most
  `X402_TREASURY_MAX_SWEEPS_PER_DAY` times a day. The destination is captured
  from server config at construction and is unreachable from any argument,
  request or later config mutation (proved against the *signed bytes*); the
  AMOUNT comes from a balance the sweep reads itself, never from a caller.
* **Failure policy.** Two consecutive swap failures disable sipping and
  raise a `/readyz` **WARNING that does not fail readiness** — settlements
  keep running on the ETH that is left. Price-movement reverts (`Too little
  received`) are not strikes; `STE` hard-disables immediately.
* **Never on the settlement path.** Own timer + a fire-and-forget
  post-settlement trigger; the shared nonce lane is held for one
  sign+broadcast and released before receipt polling. Both writers draw from
  ONE nonce allocator (`src/facilitator-evm/nonce.js`), so a lagging RPC
  cannot hand a sip and a settlement the same nonce; a treasury transaction
  with an unknown outcome blocks further treasury signing and is resolved
  (rebroadcast / fee-bumped on the same nonce) by `treasury.recover()` at
  startup and on every tick; and `treasury sip|sweep|reconcile` must take an
  exclusive DB lease the running service holds — one signer per lane.
* **Gas budget.** Treasury gas counts against `X402_DAILY_GAS_BUDGET_WEI`
  (the breaker sums both ledgers) and the treasury stops spending while it is
  open.

```sh
animica-x402 treasury status                # policy, balances, breaker, totals
animica-x402 treasury history --kind sip    # tx-by-tx ledger
animica-x402 treasury reconcile             # resolve in-flight txs from chain truth
animica-x402 treasury sip --confirm         # manual (bypasses floor+cooldown, not the budget)
animica-x402 treasury sweep --confirm
animica-x402 treasury resume --confirm      # re-arm after a two-strike disable
```

### Key rotation (no user-facing change)

1. Generate + fund the NEW key (previous section).
2. Edit `/etc/animica-x402.env`: replace `X402_FACILITATOR_PRIVATE_KEY`.
3. `systemctl restart animica-x402-facilitator` — crash recovery resolves
   any in-flight settlement of the old key from chain truth before serving.
4. Verify: `curl -s 127.0.0.1:8743/supported` shows the new signer address;
   `readyz` all-true.
5. Sweep the old address's leftover ETH at leisure; retire the old key.

Nothing user-facing references the facilitator address — offers only carry
`payTo`. Rotating `X402_SETTLEMENT_ADDRESS` is a different operation:
change it in the env file and restart BOTH units; authorizations signed
against the old payTo will (correctly) stop verifying, so expect a brief
window of client re-402s.

### Upgrade note: the facilitator default changed to `self`

`X402_FACILITATOR_MODE` used to default to `remote`, which meant an env file
that only set `X402_EVM_FACILITATOR_URL` (the legacy alias) silently sent
`/verify` and `/settle` to whatever that URL named. It now defaults to
`self` — the loopback facilitator on `127.0.0.1:8743`. **On the next restart
of an existing deployment**, an env file with a remote URL but no explicit
mode will switch to the self-hosted facilitator. Decide deliberately:

* keep it self-hosted (recommended, and what the docs claim): make sure
  `animica-x402-facilitator.service` is running and `/readyz` is all-true
  before restarting the gateway. The shipped unit sets
  `Environment=X402_FACILITATOR_MODE=self` explicitly, and the legacy
  `X402_EVM_FACILITATOR_URL` line can then be deleted;
* or keep the remote facilitator: set **both**
  `X402_FACILITATOR_MODE=remote` and `X402_FACILITATOR_URL=<url>` (the mode
  now fails closed without an explicit URL — no endpoint is ever chosen for
  you).

### Deployment files (in this repo; installing them is the runbook step)

* `systemd/animica-x402.service` + `systemd/animica-x402-facilitator.service`
  — hardened units (NoNewPrivileges, PrivateTmp, ProtectSystem=strict with
  `ReadWritePaths=state/`, ProtectHome=read-only, ProtectKernel*/
  ProtectControlGroups, RestrictSUIDSGID, Restart=on-failure with a
  StartLimit so a misconfig can't hot-loop). The node path in `ExecStart`
  must match the host (this box: `/root/.nvm/versions/node/v20.20.2/bin/node`).
* `nginx/animica-dev-x402.conf` + `nginx/INSTALL.md` — the
  `animica.dev/x402/` location set (per-product timeouts, request-size
  caps, three `limit_req` zones, request-id forwarding, no buffering on the
  inference route). **Installing it replaces the current simple `/x402/`
  location that fronts the demo server** — read `nginx/INSTALL.md`.
  `test/nginx-conf.test.js` walks the live registry against this file with
  nginx's own location-selection rules and fails if any product route falls
  into the catch-all, if a body cap sits below the gateway's own (nginx
  would 413 a request the catalog advertises as valid), or if a paid route's
  read timeout is under 120 s (a settlement running past it is charged and
  the response discarded). Add a product, add its location.
* Order: Sepolia manual pass (`test/manual/base-sepolia.md`) → mainnet env
  file → facilitator unit → `readyz` all-true → gateway unit → nginx cutover
  → `smoke-pay.mjs` against production → `animica-x402 reconcile`.

### Admin CLI

```
bin/animica-x402 settlements list [--status failed] [--limit N]
bin/animica-x402 payment get <payment_id|auth_hash|tx_hash>
bin/animica-x402 revenue [--since 24h]        # settled sums per product
bin/animica-x402 reconcile [--limit N]        # settled rows vs chain receipts (X402_RPC_URL)
bin/animica-x402 gas report [--since 7d]      # incl. OP-stack L1 data fees
bin/animica-x402 incidents list [--status open]
bin/animica-x402 incidents resolve <id> --status refunded|resolved
bin/animica-x402 index status                 # address-index backfill / lag
bin/animica-x402 commitments list [--state sealed|open|revealed]
bin/animica-x402 commitments get <commit_id>  # secret withheld while sealed
bin/animica-x402 commitments prune [--older-than 90d]
bin/animica-x402 treasury status               # sweep-and-sip policy, breaker, totals
bin/animica-x402 treasury history [--kind sip|sweep|approve]
bin/animica-x402 treasury reconcile            # chain-truth pass over in-flight txs
bin/animica-x402 treasury sip --confirm        # moves real money (see the runbook note)
bin/animica-x402 treasury sweep --confirm
bin/animica-x402 treasury resume --confirm
```

Local DBs only except the three `treasury` mutators, which need the
facilitator's own env (key + RPC) and are gated behind `--confirm`;
`--json` everywhere. `commitments` is the one command that
touches secret material, and it never discloses what the FREE public reveal
route would still be withholding: while `now < reveal_after` the secret and
salt print as `<sealed>`; `list` never prints them at all. `reconcile` exits 1 if any settled row
lacks a matching on-chain receipt (status + `AuthorizationUsed` +
`Transfer` log check) — run it after every deploy and on a cron.

## Test matrix

`node --test test/` — 334 tests at this commit, no network, every key
throwaway and in-process. The spec's minimum list maps line by line onto
named tests, so a missing guarantee shows up as a missing test rather than
as prose:

| spec line | where it is proved |
|---|---|
| **Protocol** — 402 w/o payment | `protocol-e2e` §1 (whole gateway), `products` (qrng 402 terms), `middleware` (scaffold gate) |
| valid payment unlocks | `protocol-e2e` §2 — real EIP-3009 signature, real facilitator, settled row + `payment-response` |
| insufficient | `protocol-e2e` §3 (signed short / quoted short / payer broke), `facilitator-evm` value-mismatch + `insufficient_funds` |
| wrong token | `protocol-e2e` §4 (gateway refuses pre-verify; facilitator refuses again), `facilitator-evm` |
| wrong chain | `protocol-e2e` §5 (+ signature over the wrong EIP-712 domain), `facilitator-evm` |
| wrong recipient | `protocol-e2e` §6 (quoted `payTo` and signed payee), `facilitator-evm` |
| expired | `protocol-e2e` §7 (expired / not-yet-valid / expiring inside the settle margin) |
| malformed | `protocol-e2e` §8 (11 shapes over HTTP), `protocol` (parser raises `PaymentParseError`, never a 500), `facilitator-evm` |
| **Replay** — reuse blocked | `protocol-e2e` replay, `facilitator-evm` post-settlement replay |
| simultaneous double submission settles once | `facilitator-evm` (4× `Promise.all`, counting mock RPC: 1 broadcast, 1 row), `protocol-e2e` concurrency (3 concurrent paid requests, 1 delivery) |
| restart doesn't allow replay | `facilitator-evm` — fresh facilitator instance **and** a fresh OS process re-claiming against the same DB file |
| **Settlement** — success receipt recognized | `facilitator-evm` happy path (status + `AuthorizationUsed`/`Transfer` logs + confirmations) |
| reverted rejected | `facilitator-evm` reverted tx → `failed` row, gas accounted, zero revenue |
| RPC timeout → recoverable | `facilitator-evm` receipt timeout leaves the row `submitting`, never rebroadcasts |
| known tx checked before rebroadcast | `facilitator-evm` — send times out, tx actually mined, recovery settles from chain truth with `rebroadcast: 0` |
| gas limits enforced | `facilitator-evm` gas cap / fee cap / simulation revert / daily-budget breaker (refuses **before** claiming) |
| **Products** — QRNG requires payment | `products` 402 with $0.01 terms, `protocol-e2e` §1 |
| QRNG returns real proof fields | `products` — verbatim `source`/`health`/`attestation` from the live-captured RPC fixture, no fabricated `proof`/`beacon` |
| bulk limits enforced | `products` block cap, tx-record cap, byte budget, head-margin clamp, cursors |
| **Address index** — walker is polite and correct | `chain-index` — batches ≤ chunk size with a yield between them, stops at `head − margin`, a failed batch resumes at the last COMMITTED height (no holes), a `parentHash` break rewinds and re-indexes, and nothing touches the node until `tick()` is called |
| history never sold from a stale index | `chain-index` — `chain_index_never_ran` / `chain_index_stale` / `chain_index_walker_stalled` / `chain_index_node_unreachable` each answer 503 with progress, zero facilitator calls |
| history cursor correctness | `chain-index` — full desc + asc paging returns every row exactly once, cursor pins its `as_of` snapshot, a cursor ahead of the tip is a 400 pre-payment |
| history derivation is recomputable | `chain-index` — direction rule (`out`/`in`, a SINGLE `self` row), digest join from `anim1…`, ordering, and the published `derivation` block |
| balances caps + BigInt safety | `chain-index` — 501 addresses / bad address 400 before any 402; duplicates deduped to one RPC; amounts stay exact decimal strings; one rejected account is per-entry data, not a poisoned settled batch |
| **Randomness family** — payment required | `random` — all five routes 402 at their registry price with the Bazaar input schema |
| caps enforced pre-settlement | `random` — int count/range, item caps, weight shape, bulk draws×bytes: 400, no `payment-required`, zero facilitator calls |
| derivation is recomputable | `random` — golden vectors from `verify.js` + a live cross-check against it + per-response recomputation from the published bytes |
| rejection sampling is unbiased | `random` — 3,000 draws over a range that does not divide 2^16, plus the documented byte-consumption rule |
| one draw per request | `random` — 500 integers come from exactly ONE `rand.quantumRandomBytes` call, taken before settlement |
| honesty fields present | `random` — `source`/`health`/`attestation`/`verification` on every response; `is_quantum:false`, `attested:false` |
| reveal is free and opens the commitment | `random` — no payment/402/facilitator call, idempotent, `sha3_256(secret‖salt) == commitment`, secret re-derived from the signed draw, 425 while sealed |
| the volume product is really a discount | `random` + `poc-new-products` F6 — below the derived break-even it 400s with the cheaper endpoint; at/above it the call is cheaper than the same number of single draws; a price that can never win makes the product unavailable |
| bulk draws are genuinely independent | `random` + `poc` F15 — one node call and one attestation per draw, distinct bytes, digest over each draw's own bytes, no concatenation published |
| response size is capped BEFORE settlement | `poc` F1a/F2a — the `replace:true` amplifier is a 400 with `caps`, zero facilitator calls; `indices_only` is the bounded path |
| money never sticks on a delivery failure | `poc` F2b — a throw after settlement yields 502 + verifying signed receipt + `delivery_failed` incident + metric, not a bare 500; F2c — an unserializable body is caught pre-settlement and charges nobody |
| the entropy trust model is free to read | `honesty-guards` — `entropy{source,is_quantum,attested,…}` in the free catalog AND in the 402's bazaar extension; no description promises hardware/quantum |
| no third-party settlement dependency | `honesty-guards` — no Coinbase name, no third-party facilitator URL and no legacy provider abbreviation anywhere in src, nginx, systemd, bin, env template or docs; `mode` defaults to `self`; `remote` without an explicit URL fails closed (`evm`) |
| nginx cannot strand a paid route | `nginx-conf` — every registry route (paid and free) resolves to a non-catch-all location whose body cap ≥ the gateway's own and whose read timeout ≥ 120 s (> the 75 s settle budget) |
| a window that cannot pay off is not sold | `products` — `bulk_chain` refuses `from > head − margin` pre-settlement and never emits a backward `next_cursor` |
| a POST body is never silently ignored | `products` — `POST /x402/qrng {"bytes":256}` returns 256 bytes; unknown/conflicting fields are 400s |
| free APIs unaffected | `gateway` free surfaces, `protocol-e2e` free surfaces (unowned paths 404, unpaid traffic shares one cached readiness probe, paid work stays read-only) |
| inference refuses payment w/o capacity | `products` disabled / below floor / capacity dropped between 402 and retry |
| inference activates at threshold | `products` — catalog flips `available:true`, proxies with priority headers |
| **Accounting** — one success = one settlement | `facilitator-evm` invariants (raw SQL over the real DB), `gateway` paywall invariants |
| revenue = stored settled amounts | same — `settledRevenueAtomic()` cross-checked against `SUM` of settled rows |
| failed/replayed never increment revenue | same — reverted + replayed + rejected all proven outside the sum |
| **Treasury** — single-wallet mode is gated | `treasury` — `payTo == facilitator` refuses startup without `X402_TREASURY_ENABLED=1` + a checksummed cold address (both in `assertTreasuryPolicy` and in the real `createEvmFacilitator`); a partial env cannot downgrade past it |
| the calldata is the recon's calldata | `treasury` — every selector recomputed from its signature, every Base address re-checksummed, and the 548-byte sip multicall reproduced field by field from the live simulation |
| floor crossing sips exactly once | `treasury` — one sip, then ten triggers of every kind held by the cooldown; halved cooldown below floor/2 proved at the second boundary |
| BOOTSTRAP STALL is survivable | `treasury` — ETH exhausted at $0.50 accrued still sips and recovers >400 settlements of runway; the fixed-$5-minimum variant is proved to deadlock |
| the daily budget is a hard cap | `treasury` — 50 settlement-triggered attempts with zero cooldown convert exactly $10.00, then refill after 24 h; a partial fit clamps rather than skips |
| slippage bound is real and BigInt-exact | `treasury` — `amountOutMinimum == quote × (10000−bps)/10000` read back out of the signed calldata; best allowlisted fee tier wins |
| ceiling crossing sweeps `balance − ceiling` | `treasury` — exact surplus to the cold address, float left behind, dust surplus skipped |
| the sweep destination is immutable | `treasury` — no parameter, config mutation or env change retargets it; asserted over every ERC-20 transfer the module ever signed |
| two failures disable sipping, readyz WARNs | `treasury` — breaker opens, `/readyz` stays `ready:true` with a warning, no further gas spent, state survives a restart, `resume` re-arms; price-movement reverts are not strikes and `STE` hard-disables |
| settlements are never blocked or delayed | `treasury` — the hook returns synchronously and swallows a throwing callback (a real `settle()` still succeeds); a settlement queued mid-broadcast runs while the sip is still polling for its receipt |
| treasury accounting reconciles | `treasury` — sipped + swept + remaining == starting USDC, ETH gained == Σ `Withdrawal` logs, metrics == ledger, treasury gas kept out of `x402_gas_spent_wei` |
| **Treasury (adversarial fixes)** — one nonce lane, two writers | `adv-treasury-nonce` — a frozen `pending` count no longer hands a sip and the next settlement the same nonce (N1); a rejected send gives its nonce back so the lane cannot gap (N1b); a stale high-water mark defers to chain truth (N1c) |
| a stuck treasury tx is not a settlement outage | `adv-treasury-nonce` — the stuck tx is fee-bumped on the SAME nonce, then confirmed from chain truth; a vanished one is rebroadcast byte-for-byte; nothing new is signed meanwhile and `/readyz` warns (N2, N2b) |
| one signer per lane, across processes | `adv-treasury-nonce` — the CLI is refused the lease while the service holds it, a started service that lost the lease signs nothing, an expired lease is reclaimable (N3, N3b, N3c) |
| the refuel is not vetoed by its own guard | `adv-treasury-econ` — the spec's $0.75/0.025 gwei bootstrap case sips, a $0.50 sip works at 0.17 gwei, and the guard still refuses a dust sip in an expensive market (E1a-E1e) |
| "cannot refuel" is visible | `adv-treasury-econ` — 3 blocked checks under the floor raise the `/readyz` warning, `x402_treasury_refuel_blocked` and an error log, without firing a breaker (E2) |
| an unknown outcome reconciles | `adv-treasury-accounting` — the ledger catches up with the chain on the next pass; recovery is wired into startup and every tick (A1, A1b) |
| the sweep never sizes from a stale balance | `adv-treasury-accounting` — no over-drain past the float, no revert-driven self-disable (A2a, A2b); a caller-supplied balance is ignored entirely (`adv-treasury-config` C2) |
| the daily ETH ceiling bounds the whole account | `adv-treasury-accounting` — treasury gas counts against `X402_DAILY_GAS_BUDGET_WEI` and an open breaker stops sips and sweeps; sweeps also carry a per-day cap (A3, A3b) |
| no standing allowance survives a failed swap | `adv-treasury-accounting` — the allowance is reset to zero in the same tick, and NOT while the outcome is unknown (A4, A4b) |
| the cold address is more than a checksum | `adv-treasury-config` — the zero address, the precompiles and burn addresses refuse startup; a contract destination refuses to be swept to unless declared (C1a-C1d) |
| refuelling starts while still ready | `adv-treasury-config` — the sip trigger is ≥3× the readyz floor and the dead-band configuration refuses to boot (C3, C3b, C3c) |
| the budget is claimed, not merely read | `adv-treasury-drain` — a second caller inside the approve window gets nothing; the check and the intent row are one `BEGIN IMMEDIATE` (D1, D1a) |
| a manipulated quote is refused | `adv-treasury-drain` — a quote 10× below an independent reference (price knob or our own last realised rate) is skipped, not "settled"; a stale reference expires rather than deadlocking (D2, D2b) |

Real-chain proof is a runbook step, not a unit test: `test/manual/base-sepolia.md`
plus `test/manual/smoke-pay.mjs` do the funded end-to-end settlement.

## Metrics reference

Prometheus text on two loopback endpoints — gateway
`127.0.0.1:8742/metrics`, facilitator `127.0.0.1:8743/metrics` (nginx never
exposes either):

| metric | type | where | meaning |
|---|---|---|---|
| `x402_settlements_total` | counter | both (gateway: per `product`) | successfully settled payments |
| `x402_settlement_failures_total` | counter | both, by `reason`/`product` | failed settlement attempts |
| `x402_replays_rejected_total` | counter | facilitator | authorization already used/claimed |
| `x402_verifications_total` | counter | both, by `outcome` | verify calls |
| `x402_gas_spent_wei` | counter (BigInt-exact) | facilitator | gas incl. L1 data fee |
| `x402_revenue_usdc` | counter (BigInt-exact, per `product`) | both | settled revenue (facilitator labels by resource) |
| `x402_payment_latency_seconds` | histogram | both | end-to-end settle latency |
| `x402_ready` | gauge | facilitator | 1 when `/readyz` passes |
| `x402_facilitator_gas_balance_wei` | gauge | facilitator | wallet gas balance (float rendering — exact value in logs) |
| `x402_idempotent_replays_total` | counter | gateway | answers served from the Idempotency-Key store |
| `x402_incidents_total` | counter | gateway, by `kind` | settled-but-failed payments (signed receipt issued) |
| `x402_inference_serving_workers` | gauge | gateway | live capacity-gate worker count |
| `x402_random_commitments_stored` | gauge | gateway | commit-reveal commitments held in the gateway DB (retention `X402_RANDOM_COMMIT_TTL_SECONDS`) |
| `x402_treasury_sips_total` | counter, by `result` | facilitator | adaptive USDC→ETH refuels (`ok`/`failed`/`unknown`) |
| `x402_treasury_sweeps_total` | counter, by `result` | facilitator | drains to the cold address |
| `x402_treasury_swept_usdc_total` | counter (BigInt-exact) | facilitator | total USDC moved to cold |
| `x402_treasury_sipped_usdc_total` | counter (BigInt-exact) | facilitator | total USDC converted to gas |
| `x402_treasury_sip_eth_received_wei` | counter (BigInt-exact) | facilitator | wei received, from WETH9 `Withdrawal` logs (chain truth) |
| `x402_treasury_gas_spent_wei` | counter, by `kind` | facilitator | treasury's own gas — kept OUT of `x402_gas_spent_wei` so settlement economics stay readable |
| `x402_treasury_eth_balance_wei` | gauge | facilitator | ETH balance at the last watcher tick |
| `x402_treasury_usdc_balance` | gauge | facilitator | USDC atomic units at the last watcher tick |
| `x402_treasury_sipping_enabled` | gauge | facilitator | **alarm on this** — 0 means the two-strike breaker opened and the wallet needs a manual top-up |
| `x402_treasury_sweeping_enabled` | gauge | facilitator | 0 means sweeps are disabled after repeated failures |
| `x402_treasury_refuel_blocked` | gauge | facilitator | **alarm on this** — 1 means the wallet is under the ETH floor and the sip keeps being skipped (no breaker fires: a skip is not a failure, but this is what ends in an empty wallet) |
| `x402_treasury_unresolved_actions` | gauge | facilitator | treasury transactions whose on-chain outcome is unknown; they sit in the settlement nonce lane, so anything >0 for long is an outage risk |

Money metrics accumulate BigInt atomic units internally and render as exact
decimal strings — no floats anywhere near amounts.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| everything answers `503 x402_disabled` | `ANM_X402_ENABLED` is not `1` (deliberate kill switch) |
| `503 x402_unconfigured` on paid routes | no payment lane configured — set `X402_BASE_PAYTO` (offers are only made when the lane is complete) |
| `402` again after paying, `invalid_exact_evm_payload_signature` | wrong EIP-712 domain. Base **mainnet** USDC's domain name is `"USD Coin"`; Base **Sepolia**'s is `"USDC"` (live-verified — the x402 spec examples show Sepolia). Also note: the gateway's accepts entries currently omit `extra.{name,version}`, so clients that refuse to sign without it (e.g. `@x402/evm` 2.22.0 throws) need the domain from their own table — `test/manual/smoke-pay.mjs` carries the verified one. See docs/x402.md "Known gaps". |
| `402` with `insufficient_funds` | payer holds less USDC than the offer amount on that network (fund at faucet.circle.com on Sepolia) |
| `402` with `invalid_transaction_state` | replay: that authorization nonce is already used/claimed (chain or ledger). Sign a fresh nonce. |
| `502 facilitator_unreachable` | facilitator down or wrong `X402_FACILITATOR_URL`/mode; check `curl 127.0.0.1:8743/healthz` |
| facilitator refuses to start | fail-closed config: read the printed reason (chain-id contradiction, non-allowlisted asset, malformed key, `locally computed domain separator != allowlisted`) — every one is a real misconfiguration, never bypass |
| `/readyz` false | the `checks` object names the failing leg: `rpc`, `chain_id`, `usdc_domain` (live `DOMAIN_SEPARATOR()` mismatch = wrong token/chain/name), `db`, `gas_balance` (fund the wallet or lower `X402_MIN_GAS_BALANCE_WEI`) |
| `/readyz` ready with a treasury WARNING | non-fatal by design. `cannot refuel` = the sip keeps being skipped under the floor (check `treasury status` for the reason: quote, budget, economics); `unresolved on-chain` = a treasury tx is in flight — `animica-x402 treasury reconcile` after the RPC recovers; `contract code` = the cold address is a contract (set `X402_TREASURY_COLD_ALLOW_CONTRACT=1` if that is intended, then `animica-x402 treasury resume --confirm` to clear the disable) |
| `better-sqlite3` install/segfault | must be **12.11.1** on this Node 20 box — 13.x is Node-22-only and segfaults here; the lockfile pins it, don't "upgrade" |
| echo 404s in production | by design (`X402_ENV=production`); `X402_ENABLE_ECHO=1` re-enables the smoke marker |
| catalog shows `available:false` | the `unavailable_reason` field says why: `qrng_entropy_health_failed` / `qrng_rpc_unreachable` (node RPC), `bulk_chain_node_unreachable`, `chain_index_backfilling` / `chain_index_stale` / `chain_index_walker_stalled` (the address index is not current — `animica-x402 index status`; the first backfill takes ~5-7 min), `priority_inference_unavailable` (operator flag off or worker floor unmet — this is the gate working, not a bug) |
| unit fails under systemd with EROFS/EACCES | `state/` doesn't exist — `ReadWritePaths` needs it created before first start (`mkdir -p …/state`, see unit comments) |
| `paid_service_failed` (502) responses | payment settled, downstream kept failing: the body carries a signed receipt + `incident_id`; reconcile with `bin/animica-x402 incidents list` and refund per docs/x402.md |

## Known limitations (open, documented, tested)

Everything here is asserted by a test in `test/poc-new-products.test.js`, so
the behaviour is pinned rather than remembered. These are open findings from
the adversarial review that were NOT fixed in the products pass:

| # | limitation | today's behaviour | why it is survivable / what fixes it |
|---|---|---|---|
| F3 | a commit-reveal row is written during the execute phase, i.e. **before** settlement | a payer whose settlement then fails leaves one sealed row behind (id never disclosed to anyone) | rows are small and pruned by TTL; a real fix moves the write after settlement or reference-counts orphans |
| F4 | commitments are pruned once, in `main()` | a gateway that never restarts never prunes, so rows can outlive `X402_RANDOM_COMMIT_TTL_SECONDS` and the reveal-404 text is optimistic | `bin/animica-x402 commitments prune --older-than 90d` on a cron until the walker does it |
| F5 | payments are **fungible across products that share a price** | a payment signed for `random_shuffle` also unlocks `chain_batch_balances` ($0.02 each) — the payer is never overcharged, but per-product accounting can be attributed to the wrong SKU | bind `resource` into the accepts entry and compare it at verify time |
| F7 | the index walker only checks parent continuity at chunk boundaries | a reorg landing mid-chunk is committed silently (boundary reorgs ARE caught and rewound) | check `parentHash` for every block in the chunk, not just the first |
| F8 | `chain_batch_balances` treats per-address RPC failures as data | a batch where every lookup failed is still a settled HTTP 200 with `failed_lookups == count` and no incident | refuse (or receipt) when `failed_lookups == count`; the readiness re-check also reads a 5 s head cache |
| F9 | `chain_address_history` accepts a `from_height`/`to_height` window the index cannot answer | it settles and returns `count: 0` with a clamped window | apply the same pre-settlement finality guard `bulk_chain` now has |

Also deliberate, not defects: `random_bulk` is priced per *independent
draw*, so it is usually **more expensive per byte** than a single
1,024-byte `qrng` draw — the response says which is cheaper for the call
you made; and `priority_inference` ships disabled because the capacity to
back it does not exist.

## Retired wANM lane

The original scaffold shipped a second lane — wANM (SPL token) settled by a
local Solana facilitator (`src/facilitator.js`, `src/solana.js`). It is
**retired** as of the 2026-08 self-hosted-facilitator spec: not configured
in any environment, not part of the product scope, not offered in any 402
(a lane is only offered when fully configured, and its variables stay
empty). The code and its tests remain in-tree as the reference
implementation of the SVM `exact` scheme; the paywall would still route a
configured `solana:*` accepts entry to it, so leaving the `WANM_*`
variables empty **is** the retirement mechanism.

## Files

```
src/config.js            env loaders (fail-closed), CAIP-2 + USDC allowlist, BigInt money math
src/protocol.js          x402 v2 + v1 wire shapes, header codecs, canonical compare
src/middleware.js        §7 facilitator HTTP client, accepts/402 builders (+ legacy scaffold gate)
src/paywall.js           production gate: availability → 402 → verify → settle → execute,
                         idempotency, signed error receipts, incidents
src/server.js            gateway entry (127.0.0.1:8742): catalog + product routes + /metrics
src/products/            registry, qrng, bulk-chain, chain-address-history,
                         chain-balances, priority-inference, echo, errors
src/capacity.js          serving-worker gate (aicf.workerStatus polling, fail-closed)
src/animica-node.js      node JSON-RPC client (BigInt-safe parse, single-flight)
src/bech32m.js           anim1… address → account digest
src/receipts.js          HMAC-SHA256 machine-readable error receipts
src/store/index.js       facilitator settlement/replay ledger (better-sqlite3, WAL)
src/store/gateway.js     gateway idempotency + incident store (own DB file)
src/chain-index/         address index: store.js (blocks + address_tx, reorg-safe),
                         walker.js (head-following, chunked, single-flight, started
                         only by main()), index.js (freshness gate for the product)
src/facilitator-evm/     self-hosted exact-EVM facilitator: evm.js (noble crypto, RLP,
                         EIP-1559), usdc.js (EIP-3009 calldata + receipt-log checks),
                         verify.js, settlement.js (claim→sign→persist→broadcast→confirm,
                         crash recovery), gas.js (caps + budget breaker), key.js, rpc.js,
                         server.js (/verify /settle /supported /healthz /readyz /metrics)
src/treasury/            "sweep and sip" (single-wallet mode): uniswap.js (verified Base
                         contract set, QuoterV2/SwapRouter02/ERC-20 calldata, revert
                         classes), treasury.js (watcher, adaptive sip, sweep, two-strike
                         breaker), store.js (treasury_actions ledger + durable state,
                         same DB file as the payments ledger), index.js (single-wallet
                         startup gate)
src/demo-server.js       dev/smoke entry (what the live unit runs today)
src/facilitator.js       RETIRED wANM/SVM facilitator (legacy, unconfigured)
src/solana.js            RETIRED SVM primitives (legacy)
bin/animica-x402         operator CLI (settlements, revenue, reconcile, gas, incidents,
                         index status, commitments, treasury)
systemd/                 hardened example units (NOT installed from here)
nginx/                   animica.dev location set + INSTALL.md (NOT installed from here)
test/                    node --test suite (266 tests), all RPC mocked, loopback only
test/nginx-conf.test.js    walks the registry against nginx/animica-dev-x402.conf
test/honesty-guards.test.js claims in README/docs asserted against the code
test/protocol-e2e.test.js  real gateway → real facilitator over HTTP, real EIP-3009 signatures
test/manual/             base-sepolia.md (real-chain manual pass) + smoke-pay.mjs (payer client)
```

Deps (pinned, audited-crypto house rule): `@noble/secp256k1` 3.1.0,
`@noble/hashes` 2.3.0, `better-sqlite3` 12.11.1. Full protocol/threat/ops
documentation: [`docs/x402.md`](../../docs/x402.md).
