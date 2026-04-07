#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export ANIMICA_STUDIO_APP_DATA_DIR="${ANIMICA_STUDIO_APP_DATA_DIR:-/tmp/animica-studio-smoke}"

cd "$ROOT"

echo "[smoke_network_flow] pytest node/dashboard/mining smoke"
"$PYTHON" -m pytest -q \
  apps/animica_studio/tests/test_ui_smoke.py \
  apps/animica_studio/tests/test_dashboard_services.py \
  apps/animica_studio/tests/test_mining_page.py \
  apps/animica_studio/tests/test_aicf.py \
  apps/animica_studio/tests/test_da_client.py \
  apps/animica_studio/tests/test_ide_page_workspace.py
