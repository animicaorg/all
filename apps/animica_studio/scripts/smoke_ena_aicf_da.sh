#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export ANIMICA_STUDIO_APP_DATA_DIR="${ANIMICA_STUDIO_APP_DATA_DIR:-/tmp/animica-studio-smoke}"

cd "$ROOT"

echo "[smoke_ena_aicf_da] pytest ENA/AICF/DA smoke"
"$PYTHON" -m pytest -q \
  apps/animica_studio/tests/test_ena_guided_automation.py \
  apps/animica_studio/tests/test_ena_contribution_engine.py \
  apps/animica_studio/tests/test_full_auto_da_configure.py \
  apps/animica_studio/tests/test_publish_page.py

"$PYTHON" -m pytest -q \
  apps/animica_studio/tests/test_aicf.py \
  apps/animica_studio/tests/test_da_client.py

"$PYTHON" -m pytest -q \
  apps/animica_studio/tests/test_da_engine.py \
  apps/animica_studio/tests/test_da_status_service.py \
  apps/animica_studio/tests/test_da_contribution.py
