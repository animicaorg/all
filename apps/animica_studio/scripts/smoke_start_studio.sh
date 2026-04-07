#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export ANIMICA_STUDIO_APP_DATA_DIR="${ANIMICA_STUDIO_APP_DATA_DIR:-/tmp/animica-studio-smoke}"

cd "$ROOT"

echo "[smoke_start_studio] main-window constructor"
timeout 20s "$PYTHON" -u -c "from PySide6.QtWidgets import QApplication; from animica_studio.storage.config import Config; from animica_studio.services.profile_service import ProfileService; from animica_studio.ui.main_window import MainWindow; app = QApplication([]); cfg = Config(); svc = ProfileService(cfg); win = MainWindow(cfg, svc); assert win.windowTitle() == 'Animica Studio'; win.close(); app.quit()"

echo "[smoke_start_studio] pytest UI smoke"
"$PYTHON" -m pytest -q \
  apps/animica_studio/tests/test_smoke_main_window.py \
  apps/animica_studio/tests/test_ui_smoke.py
