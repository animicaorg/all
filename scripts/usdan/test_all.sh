#!/usr/bin/env bash
set -euo pipefail

pytest -q contracts/tests/test_usdan_contracts.py
pnpm --filter @animica/usdan-api test
pnpm --filter @animica/usdan-web test
