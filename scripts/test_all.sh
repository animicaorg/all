#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
. .venv/bin/activate 2>/dev/null || true

export AICF_USE_PROJECT_ENGINE=1

pytest aicf/tests \
  -k 'not test_recovery_after_cooldown_and_topup' \
  --cov=aicf \
  --cov-report=xml:artifacts/coverage.xml \
  --junitxml=artifacts/junit.xml
