# Stratum Status And Fixes

## What Was Broken

- The docs and CLI referenced `animica[stratum]`, but the extra did not exist in
  `python/pyproject.toml`.
- Operators only had the lower-level `animica miner run-pool` launcher; there
  was no managed `up/down/status` lifecycle for pool operations.
- There was no repo-shipped runtime smoke path proving:
  - the pool starts,
  - the API comes up,
  - a miner-style subscribe/authorize handshake works.
- In sandboxed environments the live bind/connect path failed, which made it
  easy to confuse real runtime defects with sandbox limitations.

## Fixes Applied

- Added backend runtime deps to the base Python package and restored the
  compatibility `stratum` extra.
- Added `animica stratum` operator commands:
  - `animica stratum up`
  - `animica stratum status`
  - `animica stratum down`
  - `animica stratum config`
- Added shared managed-service state handling via `ANIMICA_SERVICE_STATE_DIR`.
- Added `scripts/smoke_stratum.sh`, which:
  - launches a stub JSON-RPC node,
  - starts the real Stratum pool in `asic_sha256` mode,
  - probes the pool API,
  - performs a real `mining.subscribe` / `mining.authorize` handshake over TCP.
- Updated operator docs to prefer `animica stratum up --daemon`.

## Files Changed

- `python/pyproject.toml`
- `python/animica/cli/main.py`
- `python/animica/cli/stratum.py`
- `python/animica/cli/service_runtime.py`
- `scripts/smoke_stratum.sh`
- `docs/stratum-asic.md`
- `docs/mining-asic-sha256.md`
- `docs/cli-commands.md`
- `README.md`
- `QUICKSTART.md`

## Validation

```bash
./scripts/smoke_stratum.sh
./scripts/smoke_backend_imports.sh
```

The live smoke test validated:

- pool daemon start/stop
- pool API `/healthz`
- pool API `/summary`
- ASIC-compatible Stratum v1 subscribe/authorize handshake

## Remaining Risks

- Share accounting and metrics storage are present, but payout accounting and
  operator settlement policy are still not a full production payout engine.
- Final block acceptance still depends on the node-side
  `miner.get_sha256_job` / `miner.submit_sha256_block` RPC behavior.
- Sandbox-restricted environments still require unsandboxed validation for live
  bind/connect checks.
