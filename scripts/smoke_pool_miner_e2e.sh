#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/python:$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

if [[ -x ".venv/bin/pytest" ]]; then
  PYTEST=(.venv/bin/pytest)
else
  PYTEST=(python -m pytest)
fi

echo "[smoke] pool/miner e2e block acceptance"
"${PYTEST[@]}" -q \
  python/animica/stratum_pool/tests/test_pool_stratum_e2e.py::test_pool_mines_real_block_via_stratum

echo "[smoke] pps and solo accounting invariants"
"${PYTEST[@]}" -q \
  python/animica/stratum_pool/tests/test_metrics.py::test_pps_accounting_credits_accepted_shares \
  python/animica/stratum_pool/tests/test_metrics.py::test_solo_accounting_only_credits_blocks

echo "[smoke] run-pool cli behavior"
"${PYTEST[@]}" -q \
  python/animica/cli/tests/test_mining_cli.py::test_run_pool_sets_env \
  python/animica/cli/tests/test_mining_cli.py::test_run_pool_sets_solo_mode

echo "[smoke] reference miner cli flags"
"${PYTEST[@]}" -q \
  python/animica/stratum_pool/tests/test_reference_cpu_miner.py::test_resolve_config_accepts_pool_url_override

echo "[smoke] complete"

