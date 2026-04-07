#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

pytest -q \
  ops/docker/tests/test_compose_port_bindings.py \
  p2p/tests/test_sync_completion_status.py \
  rpc/tests/test_p2p_supervisor.py \
  python/animica/cli/tests/test_node_cli.py \
  python/animica/cli/tests/test_sync_cli.py
