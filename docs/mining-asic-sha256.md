# SHA-256 ASIC Stratum (experimental)

This repository now exposes an **ASIC-friendly Stratum v1 surface** so off-the-shelf SHA-256 miners can connect to an Animica pool. The implementation reuses the existing mining stack while translating to familiar Bitcoin-style semantics.

## What it does

- Implements `miner.get_sha256_job` / `miner.submit_sha256_block` RPC methods so the pool can fetch block templates and return block candidates to the node.
- Adds an ASIC Stratum server (`Sha256StratumServer`) that supports `mining.subscribe`, `mining.authorize`, `mining.notify`, `mining.set_difficulty`, and `mining.submit` in standard Stratum v1 format.
- Provides a CLI profile (`--profile asic_sha256`) that wires the new server into the pool process with extranonce management and difficulty negotiation.

## Running the pool in ASIC mode

```bash
cd /root/animica
python3 -m venv .venv
source .venv/bin/activate
pip install -e python

export ANIMICA_RPC_URL="http://127.0.0.1:8545/rpc"
export ANIMICA_MINING_POOL_DB_URL="sqlite:////root/animica/data/mining_pool.db"
export ANIMICA_MINING_POOL_LOG_LEVEL=info
export ANIMICA_STRATUM_BIND="0.0.0.0:3333"
export ANIMICA_POOL_API_BIND="0.0.0.0:8082"
export ANIMICA_POOL_PROFILE="asic_sha256"
export ANIMICA_STRATUM_EXTRANONCE2_SIZE=4

python -m animica.stratum_pool
```

Point an ASIC at `stratum+tcp://<SERVER_IP>:3333` with the payout Animica address as the username. Passwords are ignored.

## Notes

- The flow is experimental and currently submits blocks via a stub endpoint while preserving share validation logic.
- Difficulty defaults to the pool's minimum difficulty; adjust via environment variables or CLI flags.
