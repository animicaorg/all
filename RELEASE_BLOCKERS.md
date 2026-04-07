# Release Blockers

## Cleared In This Pass

- Setup verification no longer relies on undeclared backend dependencies.
- `setup.sh` validates backend/runtime imports that match the actual operator path.
- ENA has a real local daemon path with inference, training, checkpoints, and
  model artifact routes.
- Stratum now has an operator lifecycle CLI and a live smoke handshake harness.
- Clean-install, backend-import, ENA, and Stratum smoke helpers exist and run.

## Remaining Blockers

1. ENA training is still a local mock-worker vertical slice.
   - Good enough for coherent local operator and Studio flows.
   - Not yet a production multi-worker / GPU / queue-backed training network.

2. Stratum payout/accounting is not yet a full operator payout system.
   - Shares are tracked and validated.
   - Pool startup and miner handshake are operator-usable.
   - Automated payout calculation and settlement policy still need hardening.

3. Some long-form docs remain stale outside the updated operator pages.
   - High-signal operator docs were updated in this pass.
   - Older ENA/AICF/Studio narrative docs still contain superseded commands.

4. ENA node code still emits framework deprecation warnings.
   - FastAPI `on_event` should move to lifespan handlers.
   - Pydantic v1-style validators should move to `field_validator`.

5. Source checkout is still the supported runtime shape.
   - `ena/` and `rpc/` are repo-local modules, not separately published packages.

## Recommended Next Release Gates

- Replace ENA mock training with the intended worker/queue execution model.
- Define and implement pool payout accounting and operator settlement outputs.
- Sweep remaining ENA/Studio docs for command drift.
- Remove FastAPI/Pydantic deprecations before strict CI warning gates.
