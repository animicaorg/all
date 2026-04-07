#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

export PYTHONPATH="$ROOT/python:$ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "[smoke-backend] checking core runtime imports"

"$PYTHON_BIN" - <<'PY'
from importlib.metadata import version

import fastapi
import prometheus_client
import rpc.server
import ena.services.ena_node.main
import animica.stratum_pool.cli

print("fastapi", version("fastapi"))
print("prometheus_client", version("prometheus-client"))
print("backend-imports-ok")
PY

"$PYTHON_BIN" -m animica --help >/dev/null

echo "[smoke-backend] success"
