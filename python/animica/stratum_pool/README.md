# Animica Stratum Pool

> **Miners:** you don't run this — just `pip install --upgrade animica && animica up`.
> That one command joins the pool and runs mining + AI (useful-work, training,
> serving, Bittensor on qualified GPUs), all paid out in ANM. The pool enforces a
> **minimum miner version (1.0.0)** — set `ANIMICA_POOL_MIN_MINER_VERSION=1.0.0`
> and `ANIMICA_POOL_REQUIRE_MIN_VERSION=true` to reject older miners. This README
> is for **pool operators**.

This package runs the managed Animica Stratum pool. It is wired to the same canonical mining path as `animica miner mine-blocks`:

- Work comes from `miner.getBlockTemplate`.
- Jobs carry the real block header template, network target, and template id.
- Share validation recomputes the canonical header hash from that template.
- Full-target shares are submitted through `miner.submitBlock`.
- Accepted blocks therefore follow the same mempool selection, header serialization, and chain import path as local CLI mining.

## Job Mapping

The pool does not invent a fake share-only job format. A Stratum job maps to a cached node block template:

1. The pool fetches a fresh template from the node RPC.
2. The template header, target, tx set, and `templateId` are stored behind the Stratum `jobId`.
3. Workers search over the real candidate nonce domain.
4. Share validation checks the same canonical header hash the node uses.
5. If the nonce also meets the full network target, the pool reconstructs the solved block and submits it with `miner.submitBlock`.

That means a full-difficulty share can become a real chain block without rebuilding a second candidate format.

## Local Quickstart

Start a local node:

```bash
animica node up --network devnet --rpc
```

Create a starter pool env file:

```bash
animica pool init --path animica-pool.env
```

Start the managed pool:

```bash
animica pool up --daemon \
  --rpc-url http://127.0.0.1:8545/rpc \
  --pool-address anim1...
```

Check the live setup:

```bash
animica pool doctor
animica pool status
animica pool test-job
animica pool list-workers
```

Connect with the Animica CLI:

```bash
python3 -m pip install --upgrade animica
animica miner setup
animica miner mine-blocks --count 0 --pool-stratum stratum+tcp://pool.animica.org:3333
```

Connect the reference Stratum miner:

```bash
./animica-miner \
  --pool-url stratum+tcp://127.0.0.1:3333 \
  --address anim1... \
  --worker local-dev \
  --pool-mode pps
```

Build standalone miner executables (for website downloads / bundles):

```bash
./scripts/build_miner_executables.sh
```

This writes platform binaries under `artifacts/miners/bin/<platform>/`. The bundle builder will
embed `animica-miner` / `animica-miner.exe` when present and fall back to `animica_cpu_miner.py`
otherwise.

Run the end-to-end smoke suite (block acceptance + PPS/SOLO accounting + CLI wiring):

```bash
bash scripts/smoke_pool_miner_e2e.sh
```

## Manual Smoke (Server)

Start node:

```bash
animica node up --network testnet --rpc
```

Start pool:

```bash
animica miner run-pool \
  --mode pps \
  --pool-address anim1... \
  --rpc-url http://127.0.0.1:8545/rpc
```

Run miner (Linux/macOS binary):

```bash
./animica-miner \
  --pool-url stratum+tcp://127.0.0.1:3333 \
  --address anim1... \
  --worker rig-01 \
  --threads 4 \
  --pool-mode pps
```

Verify chain head and accounting:

```bash
curl -s http://127.0.0.1:8550/api/pool/summary | jq '.height,.pool_hashrate,.blocks_found_total'
curl -s http://127.0.0.1:8550/api/pool/accounting | jq
curl -s http://127.0.0.1:8545/rpc -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' -H 'content-type: application/json'
```

## Operator Commands

- `animica pool up` / `animica stratum up`: start the managed pool.
- `animica pool down`: stop it.
- `animica pool status`: inspect process and API health.
- `animica pool doctor`: verify node RPC, template generation, and pool API state.
- `animica pool test-job`: fetch and validate a real node block template.
- `animica pool list-workers`: show worker stats from the pool API.
- `animica pool show-config`: print the resolved config.

`animica stratum ...` remains supported; `animica pool ...` is the operator alias.

## Troubleshooting

- `Pool payout address is not configured`: set `ANIMICA_POOL_ADDRESS` or pass `--pool-address`.
- `Template probe failed`: the node RPC is reachable but `miner.getBlockTemplate` is failing or returning malformed data.
- `managed pool is not running`: start it with `animica pool up --daemon`.
- `Failed to query pool API`: the pool process is up but the API bind/port is unreachable.
- Frequent stale shares: check for chain tip churn or a node that is not refreshing templates promptly.
