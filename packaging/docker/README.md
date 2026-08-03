# Animica slim node image

`Dockerfile.node` builds a **slim public distribution image** of the Animica
mainnet node from the PyPI `animica` package (9.0.8):

* base `python:3.12-slim`
* JSON-RPC on **8545** (`/rpc`, `/ws`, `/openrpc.json`, `/healthz`, `/metrics`)
* P2P TCP on **30333** (the real mainnet port — same as the canonical node;
  QUIC 30334/udp and WS 30335/tcp exist in the stack but are optional)
* runs as non-root uid 10001, chain state in the `/data` volume
* mainnet genesis ships inside the package and is auto-selected via
  `ANIMICA_NETWORK=mainnet` — no genesis file mount needed
* healthcheck polls `/healthz`

## Build & run

```sh
docker build -f Dockerfile.node -t animicaorg/node:9.0.8 .
docker run -d --name animica-node \
  -p 127.0.0.1:8545:8545 -p 30333:30333 \
  -v animica-data:/data \
  animicaorg/node:9.0.8
# a few seconds later:
curl -s http://127.0.0.1:8545/healthz
curl -s http://127.0.0.1:8545/rpc -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}'
```

Or `docker compose -f compose.example.yml up -d`.

## IMPORTANT: do NOT build the monorepo image

The Animica monorepo also contains the operator's full node/ops image (the
one behind the `animica-mainnet-node` container). That image is **multi-GB**
(full AI stack, by design) and its build has caused production outages when
run in place. **This directory's Dockerfile is the only thing third parties
should build.** It does not touch the monorepo at all — everything comes from
PyPI.

## Why the image stays slim (the --no-deps trick)

The PyPI `animica` package *intentionally* lists torch, transformers,
diffusers and friends as base dependencies (one package = node + miner + AI
provider). A plain `pip install animica` is therefore a multi-GB download. A
public RPC/P2P node needs none of it, so the Dockerfile does:

1. `pip install -r requirements-node.txt` — 36 pinned packages, the minimal
   set **empirically verified** (2026-08-03, Python 3.12/Linux) to boot
   `python -m rpc` on mainnet: genesis loads (chainId=1), ML-DSA-65 backend
   self-test passes, core P2P + snapshot orchestrator + mempool start,
   uvicorn serves.
2. `pip install --no-deps animica==9.0.8` — the verified sdist/wheel
   (sdist sha256
   `429a4c270f33847cb1d0954dae2dea2e13b3830199a55eddb76e14d36e1fc712`).

Consequence: AI-flavoured CLI subcommands (`animica ai`, `animica media`,
ENA training, the batched-torch GPU miner) will raise ImportError in this
image. That is the point — it is a *node* image. Miners and AI providers
should `pip install animica` (full) on their own hardware instead.

When bumping to a new animica release: update `ANIMICA_VERSION` (build arg /
Dockerfile default), rebuild, and re-verify boot — a new release may move an
import into the boot path, requiring an addition to `requirements-node.txt`
(the failure mode is an obvious ImportError in `docker logs`).

## Sync expectations

A fresh node starts at height 0 and syncs from P2P peers (and can bootstrap
from snapshots if `ANIMICA_SNAPSHOT_*` manifest sources are configured —
without them it logs "Snapshot bootstrap skipped" and syncs from the network).
Public RPC for comparison while you wait: `https://rpc.animica.org/rpc`.
