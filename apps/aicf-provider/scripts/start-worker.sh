#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${AICF_PROVIDER_CONFIG:-provider.config.json}"
python3 worker.py start --config "$CONFIG_PATH"
