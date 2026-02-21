# ENA End-to-End Smoke Test

This smoke test validates the full ENA lifecycle on CPU in dev/test mode with deterministic settings.

## Commands

```bash
./scripts/ena_smoke_test.sh
pytest -q python/animica/tests/test_ena_e2e_smoke.py
animica ena smoke-test --json
```

## What it verifies

1. Deterministic tiny ENA toy training (fixed seed, single-thread settings).
2. Snapshot packing with `manifest.json`, `weights.bin`, and `tokenizer.json`.
3. Stable `full_snapshot_hash` across two consecutive pack runs.
4. DA push and fetch roundtrip using real DA RPC when discoverable, else local dev stub.
5. Byte-for-byte equality and content hash equality for DA refetch.
6. Manifest fields:
   - `model_name`, `model_version`, `chain_id`, `block_height`, `commit_hash`, `created_at`
   - `params_summary`, `tokenizer_hash`, `weights_hash`, `full_snapshot_hash`, `da_commitment`
   - `determinism`
7. Snapshot reload + one inference.
8. One ENA call submission path using `tx_sendRawTransaction` with OpenRPC-compliant params shape: array with a single hex string.
9. Fee routing and AICF accounting checks.
10. AICF credit flow after a simulated reward-slice mint event.

## Expected output

`animica ena smoke-test --json` emits a report with:
- `ok: true`
- `timings.total_seconds < 120`
- hashes and DA mode (`rpc` or `dev_stub`)
- fee-routing and AICF sections

## Troubleshooting

- If DA methods are not available in RPC discover, test automatically falls back to a local DA stub.
- On failure, inspect debug bundle:
  - `<work_dir>/ena_smoke_debug_bundle.zip`
  - includes manifests, report, RPC trace, and env diagnostics.
- If CLI command `animica` is not on PATH, run with python module entrypoint in your virtualenv after `pip install -e python`.
