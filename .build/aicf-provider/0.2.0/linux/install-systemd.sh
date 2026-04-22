#!/usr/bin/env bash
set -euo pipefail
TARGET_DIR="/opt/aicf-provider-worker"
sudo mkdir -p "${TARGET_DIR}"
sudo cp -r ./* "${TARGET_DIR}/"
sudo cp aicf-provider-worker.service /etc/systemd/system/aicf-provider-worker.service
sudo systemctl daemon-reload
sudo systemctl enable aicf-provider-worker
sudo systemctl restart aicf-provider-worker
sudo systemctl status aicf-provider-worker --no-pager
