#!/usr/bin/env bash
# mine.sh — mine 1 block per loop with the given payout address

set -euo pipefail

log()  { echo "[mine] $(date -u +%FT%TZ) $*"; }
warn() { echo "[mine][WARN] $(date -u +%FT%TZ) $*" >&2; }
die()  { echo "[mine][ERROR] $(date -u +%FT%TZ) $*" >&2; exit 1; }

ADDR="${1:-${MINING_ADDRESS:-}}"
RPC_URL="${RPC_URL:-http://127.0.0.1:8545/rpc}"
SLEEP_SECS="${SLEEP_SECS:-2}"
COUNT_PER_LOOP="${COUNT_PER_LOOP:-1}"

[ -n "$ADDR" ] || die "Usage: $0 <mining_address>   (or set MINING_ADDRESS env var)"

# Best-effort: activate local venv if present (doesn't fail if missing)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.venv/bin/activate"
  log "Activated venv: $SCRIPT_DIR/.venv"
fi

command -v animica >/dev/null 2>&1 || die "'animica' not found in PATH. Activate your venv or install animica."

log "Starting miner loop"
log "Payout address: $ADDR"
log "RPC URL:        $RPC_URL"
log "Blocks/loop:    $COUNT_PER_LOOP"
log "Sleep seconds:  $SLEEP_SECS"

trap 'warn "Caught signal; exiting."; exit 0' INT TERM

i=0
while true; do
  i=$((i+1))
  log "Loop #$i: mining $COUNT_PER_LOOP block(s)…"
  set +e
  animica miner mine-blocks --address "$ADDR" --count "$COUNT_PER_LOOP" --rpc-url "$RPC_URL"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    warn "animica miner exited with code $rc (will retry after ${SLEEP_SECS}s)"
  fi
  sleep "$SLEEP_SECS"
done