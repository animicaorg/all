# Animica × Bittensor — compute / mining / inference layer

> Status 2026-06-04 (evening): Track 1 (demand side) **LIVE** — Chutes key
> installed, `bittensor` provider enabled, first paid completions served at
> ~83% gross margin. Track 2 (supply side) **built + PAUSED** behind
> `BITTENSOR_MINING_ENABLED=false` until the treasury accumulator funds the
> SN51 registration burn. On-chain earnings poller built (taostats
> alpha-stake deltas; dormant until `TAOSTATS_API_KEY` + a registered
> hotkey). Public dashboard at **pool.animica.org/bittensor**; rig-owner CLI
> shipped in **`animica` 0.4.0 on PyPI** (`animica bittensor
> status|enroll|up`). Zero upfront capital — Track 1 margin funds Track 2.

## Positioning

There is no turnkey "plug your GPU in, we split the Bittensor emissions" pool
product (verified June 2026 — Celium SN51 is the closest native primitive,
Crunch the closest white-glove service). Animica Pool already is that product
for ANM/XMR: workers, share accounting, revenue ledger, multi-asset payouts.

**Animica = the pool/aggregation layer between hardware owners and Bittensor.**

Two sides, one ledger:

1. **Demand (Track 1, live-ready):** buy emission-subsidized inference from
   Bittensor subnets (Chutes/SN64 sells inference at a 22–40:1
   emissions-to-revenue subsidy), resell at `anm-*` catalog prices through the
   existing OpenAI-compatible API. Customers prepay credits → no working
   capital needed.
2. **Supply (Track 2, built+paused):** user GPUs from the rig-rental fleet
   back **our** SN51 (Celium) miner hotkey as executors. Pool earns
   TAO/rental fees, rig owners get `BITTENSOR_OWNER_SHARE_PERCENT` (70%),
   pool keeps 30%, all flowing through the normal payout engine
   (ANM/XMR/SOL/BTC/USDT).

## Verified economics (mid-2026)

| Fact | Number | Source |
|---|---|---|
| TAO price | ~$220 (volatile, $217–234 band) | CoinGecko 2026-06 |
| Network emission (post Dec-2025 halving) | 0.5 TAO/block ≈ 3,600 TAO/day | learnbittensor docs |
| Per-subnet emission split | 18% owner / 41% miners / 41% validators | learnbittensor docs |
| Own-subnet registration | ~2,500 TAO burn (~$550K, sunk) + 128-cap displacement | **parked — not viable** |
| SN51 slots | 256 UIDs (≤64 validators / ~192 miners), dereg = lowest emission | taostats docs |
| SN51 collateral | per-executor, on Subtensor EVM, = 7 days of rental fees, **BURNED on slash** | celium-collateral contracts |
| SN51 demand | rental revenue **exceeds** emissions (only such subnet); H200 ~$1.74/hr ≈ $30–40/day/GPU | subnetalpha/lium docs |
| Chutes prices (live `/v1/models`) | Mistral-Nemo $0.0245/$0.0978, Qwen3-32B $0.104/$0.416, DeepSeek-V3.2 $0.28/$0.42 per **1M** tok | verified 2026-06-04 |
| CPU/xmrig fleet on Bittensor | not viable on compute subnets; only SN13 data-scraping (low $) | subnet docs |

### Track 1 margin (per 1M tokens, customer price vs Chutes cost)

| Model | We charge | Chutes costs | Gross margin |
|---|---|---|---|
| anm-fast-8b | $0.20 / $0.60 | $0.0245 / $0.0978 | ~85–88% |
| anm-code-7b | $0.30 / $0.90 | $0.104 / $0.416 | ~54–65% |
| anm-pro-70b | $1.50 / $3.00 | $0.28 / $0.42 | ~81–86% |
| anm-bittensor-router | $0.80 / $1.60 | $0.28 / $0.42 | ~65–74% |

Live Chutes pricing is polled from `/v1/models` every 10 min so recorded
margin tracks reality; static fallbacks are the 2026-06-04 rates.

## How the money flows

```
Track 1:  customer credits (prepaid) ──► inference ──► Chutes cost
                                          │
                                          ▼ margin → RevenueLedger(inference)
                                              net × SPLIT_TREASURY_PERCENT (20%)
                                              ──► treasury accumulator
                                                  ──► funds SN51 burn + collateral
                                                       (BITTENSOR_REG_TARGET_USD)

Track 2:  SN51 emissions/rentals ──► BittensorEarning (TAO→USD at record time)
            ├─ 70% owner share ── 7-day HOLDBACK ──► payable balance ──► payouts
            └─ 30% pool share ──► RevenueLedger(bittensor) ──► splits/treasury
```

**Slash protection chain:** SN51 burns our per-executor collateral if a rented
GPU drops → so (1) only rigs with ≥97% uptime over ≥7 days may enroll
(`WorkerHeartbeatDay` rollup), (2) each rig's last 7 days of owner earnings
are held back and **forfeited first** to absorb a slash
(`POST /api/admin/bittensor/executors/:id/slash`), (3) the worker agent
reports executor-container health every 30s so a dying rig is pulled from
inventory before it gets rented.

## Go-live runbook

### Track 1 — today

1. Sign up at chutes.ai (self-serve), create an **invoke-scoped** `cpk_` key,
   deposit a few dollars (TAO or fiat, pay-as-you-go, no minimum).
2. `BITTENSOR_API_KEY=cpk_… BITTENSOR_ENABLED=true` → restart API.
3. Enable the `bittensor` row in ProviderConfig (admin → providers).
4. Optional fallback: OpenRouter key + `OPENROUTER_ENABLED=true` (pinned to
   the `chutes` provider; fiat billing).
5. Watch margin: `GET /api/revenue/summary` (grossMarginPct) and
   `GET /api/bittensor/overview` (treasury progress).

### Track 2 — when treasury ≥ target

1. Pull live numbers (they're dynamic, JS-gated on dashboards):
   ```bash
   pip install bittensor-cli
   btcli subnets show --netuid 51        # current UID recycle/burn cost
   btcli subnet list                      # sanity: SN51 alive, emission share
   ```
   Collateral table: extract `REQUIRED_DEPOSIT_AMOUNT[GPU_TYPE]` from the
   `celium-collateral-contracts` package (PyPI ≥1.0.62) — sizes locked TAO
   per executor (≈7 days of that GPU's rental fees).
2. Create wallet + register: `btcli wallet new_coldkey/new_hotkey`, then
   `btcli subnet register --netuid 51` (burn is **sunk** — registers the UID).
3. Stand up the central miner (4 cores/8GB, no GPU — fits this box):
   Datura `compute-subnet/neurons/miners` docker compose, with our
   hotkey + open `EXTERNAL_PORT`.
4. Record the miner: `POST /api/admin/bittensor/miners
   { hotkeySs58, status: "registered", registrationBurnTao }` — this replaces
   the auto-created PENDING placeholder and unblocks provisioning scripts.
5. Flip `POST /api/admin/bittensor/flags { miningEnabled: true }`.
6. Enrolled rigs fetch `GET /api/bittensor/executors/:id/provision` and run
   the script (sysbox + Datura executor container with our hotkey). Rig sets
   `BITTENSOR_EXECUTOR=1` in the worker agent env.
7. Add each verified executor to inventory + post collateral:
   `lium provider node add --gpu-type … --ip … --port 8080 --price …`, then
   contract `deposit(executorUuid)`; mirror with
   `PATCH /api/admin/bittensor/executors/:id { executorUuid, collateralTao }`.
8. Record earnings (until automated polling lands):
   `POST /api/admin/bittensor/earnings { kind: "emission"|"rental",
   taoAmount, periodStart, periodEnd [, executorId] }` — converts at live TAO
   price, splits owner/pool, feeds the ledger.

## Risks (ranked)

1. **TAO/alpha volatility** — earnings recorded in USD at record time; pool
   share converts via existing NOWPayments rails; don't hold TAO beyond ops
   float.
2. **Collateral slash on third-party rigs** — mitigated by the
   uptime gate + holdback forfeiture; pool still eats slashes exceeding a
   rig's 7-day holdback (poolLossUsd is reported per slash).
3. **Deregistration** — SN51 kicks the lowest-emission UID when full;
   registration burn is sunk. Keep executors performant; budget for
   1 re-registration in the target.
4. **Subnet governance risk** — Covenant AI exit (Apr 2026) cratered three
   subnets −55–70% in a day. SN51 (Datura) and SN64 (Rayon) carry the same
   key-person risk; Rayon alone holds 23.7% of all emissions. Demand side is
   resilient (OpenRouter fallback can route to non-Bittensor providers);
   supply side is exposed — diversify subnets later (SN64 bare-metal nodes).
5. **Chutes repricing** — the 22–40:1 subsidy will compress; live price feed
   means our recorded costs follow automatically, but margins will thin.
   Customer prices are ours to adjust in `MODEL_PRICING_USD_PER_1K`.
6. **Whitelisting/attestation drift** — SN51 has no documented ban on
   operator-managed third-party rigs today (trust unit = hotkey + executor
   UUID + collateral), but validator-side rules are owner-set; re-verify
   before scaling past a handful of executors.

## Open items (flagged unknowns)

- Live SN51 UID burn cost + `REQUIRED_DEPOSIT_AMOUNT` table → step 1 of the
  runbook (on-chain, dynamic).
- SN51 exact scoring formula + offline-grace window → read
  `neurons/validators` source before enrolling flaky-ish rigs.
- Targon pricing (auth-gated) → keep `TARGON_ENABLED=false` until confirmed.
- Automated earnings polling (taostats API / chain queries) → replaces the
  admin earnings endpoint; build when Track 2 goes live.
- Executor cap per miner hotkey → undocumented; assume none, verify at ~10.
