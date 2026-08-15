# x402 gateway + self-hosted facilitator

> **Status (2026-08-15): Base-USDC x402 stack, facilitator SELF-HOSTED —
> no third-party settlement dependency, no Coinbase services anywhere.
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
node --test test/                       # 138 tests, no network anywhere

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
             │  discovery /x402 + /.well-known/x402 (free, live availability)│
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
   └──────────────────────────────────────┘
```

Two separable layers: the **gateway** (products, paywall, discovery) speaks
to any x402-v2-§7 facilitator; the **facilitator** is ours by default
(`X402_FACILITATOR_MODE=self`) and swappable for a remote one
(`remote` + `X402_FACILITATOR_URL`, e.g. PayAI) with zero product changes.

## Products

| id | route(s) | price (default) | mode | notes |
|---|---|---|---|---|
| `qrng` | `GET /x402/qrng/draw`, `POST /x402/qrng` | $0.01 `X402_QRNG_PRICE_USDC` | execute-then-settle | wraps the node's real `rand.quantumRandomBytes`; health-gated readiness probe BEFORE any 402; attestation fields pass through verbatim (`attested:false` today — honesty enforced) |
| `random_int` | `POST /x402/random/int` | $0.01 `X402_RANDOM_INT_PRICE_USDC` | execute-then-settle | uniform ints in `[min,max]`, ≤1,000 per call, **rejection sampling** (documented, no modulo bias); one draw per request, derived |
| `random_shuffle` | `POST /x402/random/shuffle` | $0.02 `X402_RANDOM_SHUFFLE_PRICE_USDC` | execute-then-settle | Fisher-Yates permutation of your list or of `1..N`, ≤10,000 items; returns the index permutation + the shuffled items |
| `random_pick` | `POST /x402/random/pick` | $0.02 `X402_RANDOM_PICK_PRICE_USDC` | execute-then-settle | k picks with/without replacement, optional **integer** weights (cumulative-weight search over one uniform draw) — raffles, sortition, A/B splits |
| `random_bulk` | `POST /x402/qrng/bulk` | $0.05 `X402_RANDOM_BULK_PRICE_USDC` | execute-then-settle | ≤10 segments × ≤1,024 bytes in ONE settlement (5,000 atomic/draw vs 10,000 single = real discount); segments are slices of one attested draw and say so |
| `random_commit` | `POST /x402/random/commit` + **free** `GET /x402/random/reveal/{id}` | $0.02 `X402_RANDOM_COMMIT_PRICE_USDC` | execute-then-settle | commit-reveal for provably-fair games: `sha3_256(secret‖salt)` now, free public idempotent reveal later (425 while sealed) |
| `bulk_chain` | `GET /x402/chain/export\|blocks\|transactions` | $0.05 `X402_BULK_CHAIN_PRICE_USDC` | settle-then-execute | ≤1,000 blocks / ≤10,000 tx rows / byte+time budgets, cursor pagination, NDJSON/JSON, gzip; amounts = decimal strings (nANM); loopback node only, chunked + single-flight |
| `chain_address_history` | `POST /x402/chain/address-history` | $0.05 `X402_CHAIN_HISTORY_PRICE_USDC` | execute-then-settle | full account history from the gateway's OWN head-following sqlite index (`src/chain-index/`); ≤500 rows/call, stable `<as_of>:<height>:<tx_index>` cursor, published direction/ordering/digest derivation; **fails closed (503, no 402) while the index is backfilling, stalled or lagging** |
| `chain_batch_balances` | `POST /x402/chain/balances` | $0.02 `X402_CHAIN_BALANCES_PRICE_USDC` | settle-then-execute | ≤500 addresses in ONE batched RPC (~5 ms/address), deduped, BigInt-exact nANM decimal strings, per-entry errors instead of a poisoned batch; single lookups stay free |
| `priority_inference` | `POST /x402/v1/chat/completions` | $0.10 `X402_INFERENCE_PRICE_USDC` | settle-then-execute | **DISABLED by default** (`PRIORITY_INFERENCE_ENABLED=0`) AND capacity-gated on live `aicf.workerStatus` polling; below the worker floor: catalog `available:false`, clear 503, **never a 402** |
| `echo` | `GET/POST /x402/paid/echo` | $0.005 | execute-then-settle | development-only settlement smoke marker; off when `X402_ENV=production` unless `X402_ENABLE_ECHO=1` |

Cross-product guarantees (all in `src/paywall.js`, tested in
`test/gateway.test.js`): an unavailable product never requests payment;
settle-first products re-check readiness immediately before settlement;
after a settled payment a downstream failure produces an **HMAC-signed
error receipt** + an incident row (never silently kept money);
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
or quantum attestation.

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
| products | `X402_QRNG_*`, `X402_RANDOM_*` (`ENABLED`, the five `*_PRICE_USDC`, `SEED_BYTES`, `MAX_INTS`/`MAX_ITEMS`/`MAX_PICKS`/`MAX_BODY_BYTES`, `BULK_MAX_DRAWS`, `MAX_DRAW_BYTES`, `COMMIT_MAX_DELAY_SECONDS`, `COMMIT_TTL_SECONDS`), `X402_BULK_*`, `PRIORITY_INFERENCE_ENABLED`, `PRIORITY_INFERENCE_MIN_SERVING_WORKERS`, `X402_INFERENCE_*`, `X402_CAPACITY_*` |
| logging/rpc | `X402_LOG_LEVEL`, `X402_LOG_FORMAT`, `X402_RPC_TIMEOUT_MS`, `X402_RPC_RETRIES` |

## Ops / runbook

### What the facilitator wallet is (and is not)

The facilitator's key can do exactly two things: **spend its own ETH on
gas** (bounded by the per-settlement gas cap, the fee-per-gas cap and the
optional daily budget breaker) and **broadcast user-signed USDC
authorizations whose amount and destination it cannot alter** (EIP-3009 —
the payer signed `to = X402_SETTLEMENT_ADDRESS` and the exact value; the
facilitator is only the courier). Revenue never touches it: USDC lands
directly at `X402_SETTLEMENT_ADDRESS`, which needs **no hot key at all** —
keep it a cold address. Compromise of the facilitator key = loss of its gas
ETH balance, nothing more. The key exists only in the 0600 env file and in
process memory (`src/facilitator-evm/key.js` — never logged, never
serialized; only the derived address appears anywhere).

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
0.002 ETH (`X402_MIN_GAS_BALANCE_WEI`) ≈ ~3,500 settlements of headroom;
holding **0.005–0.01 ETH** (~$20-40, roughly 9k-18k settlements) and
topping up on the `x402_facilitator_gas_balance_wei` gauge is comfortable.
Do not park more — the wallet is hot by definition. Margin note: at $0.01
QRNG pricing the gas cost is ~20% of revenue; watch
`x402_gas_spent_wei` vs `x402_revenue_usdc` and the daily budget breaker
(`X402_DAILY_GAS_BUDGET_WEI`) is the stop-loss if Base fees spike.

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
```

Local DBs only, `--json` everywhere. `commitments` is the one command that
touches secret material, and it never discloses what the FREE public reveal
route would still be withholding: while `now < reveal_after` the secret and
salt print as `<sealed>`; `list` never prints them at all. `reconcile` exits 1 if any settled row
lacks a matching on-chain receipt (status + `AuthorizationUsed` +
`Transfer` log check) — run it after every deploy and on a cron.

## Test matrix

`node --test test/` — 138 tests, no network, every key throwaway and
in-process. The spec's minimum list maps line by line onto named tests, so a
missing guarantee shows up as a missing test rather than as prose:

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
| free APIs unaffected | `gateway` free surfaces, `protocol-e2e` free surfaces (unowned paths 404, unpaid traffic shares one cached readiness probe, paid work stays read-only) |
| inference refuses payment w/o capacity | `products` disabled / below floor / capacity dropped between 402 and retry |
| inference activates at threshold | `products` — catalog flips `available:true`, proxies with priority headers |
| **Accounting** — one success = one settlement | `facilitator-evm` invariants (raw SQL over the real DB), `gateway` paywall invariants |
| revenue = stored settled amounts | same — `settledRevenueAtomic()` cross-checked against `SUM` of settled rows |
| failed/replayed never increment revenue | same — reverted + replayed + rejected all proven outside the sum |

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
| `better-sqlite3` install/segfault | must be **12.11.1** on this Node 20 box — 13.x is Node-22-only and segfaults here; the lockfile pins it, don't "upgrade" |
| echo 404s in production | by design (`X402_ENV=production`); `X402_ENABLE_ECHO=1` re-enables the smoke marker |
| catalog shows `available:false` | the `unavailable_reason` field says why: `qrng_entropy_health_failed` / `qrng_rpc_unreachable` (node RPC), `bulk_chain_node_unreachable`, `chain_index_backfilling` / `chain_index_stale` / `chain_index_walker_stalled` (the address index is not current — `animica-x402 index status`; the first backfill takes ~5-7 min), `priority_inference_unavailable` (operator flag off or worker floor unmet — this is the gate working, not a bug) |
| unit fails under systemd with EROFS/EACCES | `state/` doesn't exist — `ReadWritePaths` needs it created before first start (`mkdir -p …/state`, see unit comments) |
| `paid_service_failed` (502) responses | payment settled, downstream kept failing: the body carries a signed receipt + `incident_id`; reconcile with `bin/animica-x402 incidents list` and refund per docs/x402.md |

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
src/demo-server.js       dev/smoke entry (what the live unit runs today)
src/facilitator.js       RETIRED wANM/SVM facilitator (legacy, unconfigured)
src/solana.js            RETIRED SVM primitives (legacy)
bin/animica-x402         operator CLI (settlements, revenue, reconcile, gas, incidents,
                         index status)
systemd/                 hardened example units (NOT installed from here)
nginx/                   animica.dev location set + INSTALL.md (NOT installed from here)
test/                    node --test suite — 138 tests, all RPC mocked, loopback only
test/protocol-e2e.test.js  real gateway → real facilitator over HTTP, real EIP-3009 signatures
test/manual/             base-sepolia.md (real-chain manual pass) + smoke-pay.mjs (payer client)
```

Deps (pinned, audited-crypto house rule): `@noble/secp256k1` 3.1.0,
`@noble/hashes` 2.3.0, `better-sqlite3` 12.11.1. Full protocol/threat/ops
documentation: [`docs/x402.md`](../../docs/x402.md).
