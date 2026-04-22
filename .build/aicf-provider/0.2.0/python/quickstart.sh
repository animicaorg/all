#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python worker.py init-config --config provider.config.json
python worker.py benchmark --config provider.config.json
python worker.py start --config provider.config.json
