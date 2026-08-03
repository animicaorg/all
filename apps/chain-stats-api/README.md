# Animica Chain Stats + Supply API

Public, read-only HTTP API purpose-built for market-data and mining
aggregators (CoinGecko, CoinMarketCap, WhatToMine, MiningPoolStats, …).
It serves Animica (ANM) supply, emission, difficulty/hashrate, block, price
and pool data from the live mainnet node.

* Binds `127.0.0.1:8560`; nginx exposes it on `https://animica.org` and
  `https://explorer.animica.org` (see `deploy/nginx-locations.conf`).
* Every response carries `Access-Control-Allow-Origin: *`.
* Data refreshes at most every 30 s (in-memory TTL cache). Every successful
  refresh is persisted to `/var/lib/animica-stats/last_good.json`; if the node
  is unreachable, the API keeps serving that last-good snapshot with an
  `X-Animica-Stale: true` header instead of failing. All upstream timeouts
  are ≤ 10 s.
* Nothing is estimated or fabricated: any datum that cannot be read live is
  `null`.

## Endpoints

| Path | Default format | Purpose |
|---|---|---|
| `GET /api/supply/circulating` | `text/plain` decimal | Circulating supply, whole ANM |
| `GET /api/supply/total` | `text/plain` decimal | Total supply (on-chain sum of balances) |
| `GET /api/supply/max` | `text/plain` decimal | Hard cap: `900000000` |
| `GET /api/supply` | JSON | Supply + per-address non-circulating breakdown + methodology |
| `GET /api/emission` | JSON | Full emission schedule (halvings, splits, cap math) |
| `GET /api/stats` | JSON | Rich chain/network/pool/market stats |
| `GET /api/whattomine` | JSON | Flat WhatToMine-style fields |
| `GET /healthz` | JSON | `{ok, node_reachable, price_fresh}` (public as `/healthz-stats`) |

### Supply endpoints (CoinGecko / CoinMarketCap format)

The three `/api/supply/{circulating,total,max}` endpoints return, by default,
a **plain-text decimal number in whole-ANM units with decimals applied** —
full precision, never scientific notation, never thousands separators:

```
$ curl https://animica.org/api/supply/circulating
105188707.293574736
```

Append `?format=json` for
`{"value": "...", "value_base_units": "...", "unit": "ANM", "height": ..., "updated_at": "..."}`
(the value is a decimal **string** to preserve full precision).

These URLs are safe to poll every 30 minutes (or faster); responses are cached
30 s server-side. No auth, no API key, HTTPS, stable paths.

## Circulating-supply methodology (for aggregator reviewers)

* **Total supply** is read live from the node's JSON-RPC
  `state.getTotalSupply` — the exact sum of every account balance in the
  chain state at the current head. It is the authoritative on-chain number
  (1 ANM = 10⁹ nANM base units; the RPC returns base units, this API applies
  the 9 decimals).
* **Circulating supply = total supply − the live on-chain balances of the
  known non-circulating addresses**, fetched from `state.getBalance` on every
  refresh:
  1. `anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga` —
     Animica Foundation treasury. This address is hard-coded in consensus
     (`consensus/rewards.py`) as the recipient of 15 % of every block subsidy
     since height 42,001.
  2. The four protocol-reserved system addresses reported by
     `chain.getParams` (`treasury`, `aicf_treasury`, `foundation_lockup`,
     `coinbase_default`). They hold 0 today and are included so that any
     future protocol allocation to them is excluded automatically.
* The address list can be extended without a code change via the
  `ANIMICA_NON_CIRCULATING` environment variable (comma-separated bech32m
  addresses). If the Foundation designates additional operational wallets as
  non-circulating, they will be added there and the change is reflected
  immediately in `/api/supply` (the per-address breakdown keeps the
  computation auditable).
* **Max supply** is `900,000,000 ANM` — a code-enforced hard cap
  (`MAX_MONEY` in `consensus/rewards.py`: block rewards are capped to
  `MAX_MONEY − premine − cumulative subsidy` and drop to 0 at the cap).
  See `/api/emission` for the complete cap math (81 M premine +
  ~810 M halving-era emission + tail).

Everything in `/api/supply` is verifiable by anyone against the public RPC
(`https://rpc.animica.org/rpc`, methods `state.getTotalSupply`,
`state.getBalance`) and the public source repo
(`https://github.com/animicaorg/all`).

## Difficulty & network-hashrate semantics

Animica's difficulty parameter is **`thetaMicro`** (Θ, the PoIES acceptance
threshold in micro-nats, from `chain.getHead`). A block is accepted when its
score `S = H(u) + Σψ ≥ Θ`, where `u` maps the SHA3-256 block hash to a
uniform `(0,1]` and `H(u) = −ln(u)`. Consequently:

```
expected hashes per block  E = e^(thetaMicro / 1e6)
network hashrate (raw H/s) = Σ e^(thetaMicro_i / 1e6) over a block window
                             ÷ (ts_end − ts_start)
```

The API reads this from the node RPC `chain.getNetworkHashrate`
(window = 120 blocks), which returns the same quantity divided by 2³²
("HashShare/s"); the API multiplies back by 2³² and publishes **raw SHA3-256
H/s** as `network_hashrate_hs`. Source of the formula in the repo:
`mining/share_target.py` (`theta_to_expected_trials`), `rpc/hashrate.py`
(`difficulty_to_work`, `HASHSHARE_TRIALS = 2**32`), and
`rpc/methods/chain.py` (`chain.getNetworkHashrate`).

`/api/stats` additionally publishes `pool_observed_hashrate_hs` — the
official pool's share-work estimate (only miners pointed at the pool), which
is a lower bound and can differ from the theta-derived network figure.
`/api/whattomine` maps `difficulty` → expected hashes per block and
`nethash` → theta-derived raw H/s, so `nethash ≈ difficulty / block_time`.

## Other data sources

* **Price / volume**: `/var/www/animica.org/anm-price.json` (NonKYC ANM/USDT
  feed refreshed by a systemd timer; public copy at
  `https://animica.org/anm-price.json`), file read first, HTTPS fallback.
  `price_fresh` in `/healthz` = feed timestamp within 30 min. `price_btc` is
  `null` (no BTC pair exists).
* **Pool**: the official pool's local API (`127.0.0.1:8550`) —
  `connected_miners`, blocks found, pool hashrate. Pool fees are static pool
  configuration (PPS fee 0 bps, solo port fee 500 bps), not fetched.
* **Block reward**: computed with the exact consensus integer math
  (`floor(300e9 × 50^e / 100^e)` nANM, `e = (height−1)//1,350,000`,
  0.0001 ANM tail; 85 % miner / 15 % foundation since height 42,001) and
  matches the live node.

## Running

```
cd /root/animica/apps/chain-stats-api
/root/animica/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8560
```

Systemd unit: `deploy/animica-stats-api.service` (not auto-installed).
Dependencies: fastapi / starlette / httpx / uvicorn — already present in
`/root/animica/.venv` (starlette is version-pinned there; no new packages
required).

### Environment variables

| Var | Default | Meaning |
|---|---|---|
| `ANIMICA_STATS_RPC_URL` | `http://127.0.0.1:8545/rpc` | node JSON-RPC |
| `ANIMICA_STATS_POOL_API_URL` | `http://127.0.0.1:8550` | pool API base |
| `ANIMICA_STATS_PRICE_FILE` | `/var/www/animica.org/anm-price.json` | price feed file |
| `ANIMICA_STATS_PRICE_URL` | `https://animica.org/anm-price.json` | price fallback URL |
| `ANIMICA_STATS_SNAPSHOT_PATH` | `/var/lib/animica-stats/last_good.json` | last-good snapshot |
| `ANIMICA_STATS_CACHE_TTL` | `30` | aggregate cache TTL (s) |
| `ANIMICA_NON_CIRCULATING` | (defaults above) | comma-separated non-circulating addresses |
| `ANIMICA_STATS_HTTP_TIMEOUT` | `8` | upstream timeout (s, hard-capped at 10) |
| `ANIMICA_STATS_POOL_FEE_BPS` | `0` | published PPS pool fee |

## Tests

Fully offline (httpx MockTransport + tmp price file/snapshot):

```
cd /root/animica/apps/chain-stats-api
/root/animica/.venv/bin/python -m pytest tests/ -q
```
