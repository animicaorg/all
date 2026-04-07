# ENA Status And Fixes

## What Was Broken

- Importing `ena.services.ena_node.main` could fail before startup because the
  log directory was not created before `logging.FileHandler(...)`.
- `animica ena serve start` targeted a nonexistent module: `ena.server`.
- `animica ena infer --local` posted to `/v1/inference`, but the service only
  implemented `/v1/infer`.
- ENA CLI groups advertised training, checkpoints, and model artifact actions,
  but the ENA node did not implement the corresponding HTTP routes.
- Studio-compatible `/health` and `/chat` surfaces were missing from the ENA
  node service.
- Local training submission forced wallet lookup even when the caller was using
  a local dev service.
- Managed ENA daemon state always wrote under `$HOME/.animica/services`, which
  is not writable in all environments.

## Fixes Applied

- `Config.ensure_dirs()` now runs before ENA logging setup.
- Added explicit ENA storage paths:
  - `ENA_TRAINING_DIR`
  - `ENA_CHECKPOINTS_DIR`
- Reworked the ENA node into a real local vertical slice:
  - `/health`, `/healthz`, `/v1/health`
  - `/v1/inference` for local/dev no-payment inference
  - `/chat` SSE stream for Studio-compatible local chat
  - `/training/submit`, `/v1/training/submit`
  - `/training/list`, `/v1/training/list`
  - `/training/status/{job_id}`, `/v1/training/status/{job_id}`
  - `/checkpoints/list`, `/v1/checkpoints/list`
  - `/checkpoints/publish`, `/v1/checkpoints/publish`
  - `/checkpoints/fetch/{version}`, `/v1/checkpoints/fetch/{version}`
  - `/v1/models/pull/{model}`
  - `/v1/models/export`
- Wired training jobs to the existing mock `TrainingWorker` so submissions
  produce real local artifacts and a checkpoint bundle instead of a placeholder.
- Added ENA daemon lifecycle commands:
  - `animica ena serve start`
  - `animica ena serve status`
  - `animica ena serve stop`
- Added `ANIMICA_SERVICE_STATE_DIR` support for writable daemon pid/log state.
- Updated `animica ena train submit` so local/dev flows can use `--payer
  local-dev` without forcing wallet lookup.

## Useful-Work / AICF / ENA Coherence

- Paid inference still keeps the AICF split verification path intact.
- Local training now creates checkpoint bundles tied to a job record, budget,
  and artifact set instead of disconnected stubs.
- Studio-compatible `/health` and `/chat` endpoints give ENA a concrete local
  path for developer tools.
- The existing ENA smoke/e2e path in `python/animica/tests/test_ena_e2e_smoke.py`
  continues to validate deterministic model packing, DA roundtrip behavior, and
  AICF credit accounting.

## Files Changed

- `ena/services/ena_node/config.py`
- `ena/services/ena_node/database.py`
- `ena/services/ena_node/main.py`
- `python/animica/cli/ena.py`
- `python/animica/cli/service_runtime.py`
- `scripts/smoke_ena.sh`
- `ena/tests/test_ena_node_service.py`

## Validation

```bash
./scripts/smoke_ena.sh
PYTHONPATH=/root/animica/python:/root/animica .venv/bin/pytest -q \
  ena/tests/test_ena_node_service.py \
  python/animica/tests/test_ena_e2e_smoke.py \
  ena/tests/test_model_registry.py \
  ena/tests/test_rate_limiter.py
```

## Remaining Risks

- Training is currently a local mock-worker vertical slice, not a distributed
  GPU-backed production training fabric.
- Payment-gated production inference still depends on live Animica RPC methods
  and real transaction verification.
- Several older long-form ENA docs outside the updated operator pages still
  describe superseded flows and should be reconciled before release.
