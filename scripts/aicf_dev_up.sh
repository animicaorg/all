#!/usr/bin/env bash
set -euo pipefail

# Starts AICF local stack in background terminals if available.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

run_bg() {
  local name="$1"
  local cmd="$2"
  echo "[aicf] starting $name: $cmd"
  nohup bash -lc "$cmd" >"/tmp/${name}.log" 2>&1 &
}

run_bg aicf_api "pnpm --filter @animica/aicf-api dev"
run_bg aicf_web "pnpm --filter @animica/aicf-web dev"
run_bg aicf_docs "pnpm --filter @animica/aicf-docs dev"
run_bg aicf_scheduler "pnpm --filter @animica/aicf-scheduler dev"
run_bg aicf_job_worker "pnpm --filter @animica/aicf-job-worker dev"
run_bg aicf_usage_meter "pnpm --filter @animica/aicf-usage-meter dev"
run_bg aicf_provider_control "pnpm --filter @animica/aicf-provider-control-plane dev"
run_bg aicf_dispute_worker "pnpm --filter @animica/aicf-dispute-worker dev"
run_bg aicf_treasury_worker "pnpm --filter @animica/aicf-treasury-worker dev"
run_bg aicf_contract_job_watcher "pnpm --filter @animica/aicf-contract-job-watcher dev"
run_bg aicf_fulfillment_scheduler "pnpm --filter @animica/aicf-fulfillment-scheduler dev"
run_bg aicf_result_submitter "pnpm --filter @animica/aicf-result-submitter dev"
run_bg aicf_finalization_worker "pnpm --filter @animica/aicf-finalization-worker dev"

echo "[aicf] stack started. logs in /tmp/aicf_*.log"
