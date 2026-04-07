#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

pytest -q \
  python/animica/cli/tests/test_wallet_show_output.py \
  python/animica/cli/tests/test_wallet_serialization.py \
  python/animica/cli/tests/test_tx_value_conversion.py \
  rpc/tests/test_tx_canonical_serialization.py \
  rpc/tests/test_tx_sendraw_params.py
