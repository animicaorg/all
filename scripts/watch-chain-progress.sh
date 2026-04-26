#!/usr/bin/env bash
set -Eeuo pipefail

# Animica chain progress watchdog
# Restarts the node if head height stops increasing for too long.

ANIMICA_BIN="${ANIMICA_BIN:-animica}"
CHECK_EVERY="${CHECK_EVERY:-60}"          # seconds between checks
STALL_AFTER="${STALL_AFTER:-900}"         # restart if no height increase for this many seconds
START_WAIT="${START_WAIT:-30}"            # wait after restart before checking again
LOG_FILE="${LOG_FILE:-$HOME/animica/logs/chain-watchdog.log}"
STATE_FILE="${STATE_FILE:-$HOME/.animica-chain-watchdog.state}"
LOCK_FILE="${LOCK_FILE:-/tmp/animica-chain-watchdog.lock}"

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$STATE_FILE")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -u '+%F %T') [WARN] watchdog already running" >> "$LOG_FILE"
  exit 0
fi

log() {
  echo "$(date -u '+%F %T') $*" | tee -a "$LOG_FILE"
}

get_head_height() {
  local out height
  if ! out="$("$ANIMICA_BIN" node status 2>/dev/null)"; then
    return 1
  fi

  height="$(printf '%s\n' "$out" | awk -F': ' '/Head height:/ {print $2; exit}')"
  [[ -n "${height:-}" ]] || return 1
  [[ "$height" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$height"
}

restart_node() {
  log "[WARN] Chain appears stalled. Restarting node..."
  "$ANIMICA_BIN" node down >>"$LOG_FILE" 2>&1 || log "[WARN] animica node down returned non-zero"
  sleep 5
  "$ANIMICA_BIN" node up >>"$LOG_FILE" 2>&1
  log "[OK] Restart command issued"
  sleep "$START_WAIT"
}

last_height=""
last_progress_ts=0

if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE" || true
fi

if [[ -z "${last_height:-}" || -z "${last_progress_ts:-}" ]]; then
  if height="$(get_head_height)"; then
    last_height="$height"
    last_progress_ts="$(date +%s)"
    log "[INFO] Initialized watchdog at height $last_height"
  else
    log "[WARN] Could not read initial head height"
    last_height="0"
    last_progress_ts="$(date +%s)"
  fi
fi

while true; do
  now="$(date +%s)"

  if height="$(get_head_height)"; then
    if [[ "$height" =~ ^[0-9]+$ ]]; then
      if (( height > last_height )); then
        last_height="$height"
        last_progress_ts="$now"
        log "[OK] Chain progressed to height $last_height"
      else
        stalled_for=$(( now - last_progress_ts ))
        log "[INFO] Height unchanged at $height for ${stalled_for}s"

        if (( stalled_for >= STALL_AFTER )); then
          restart_node

          if new_height="$(get_head_height)"; then
            last_height="$new_height"
            last_progress_ts="$(date +%s)"
            log "[OK] Post-restart height $last_height"
          else
            last_progress_ts="$(date +%s)"
            log "[WARN] Post-restart status not readable yet"
          fi
        fi
      fi
    else
      log "[WARN] Non-numeric height returned: $height"
    fi
  else
    log "[WARN] Failed to get node status"
  fi

  cat > "$STATE_FILE" <<STATE
last_height="$last_height"
last_progress_ts="$last_progress_ts"
STATE

  sleep "$CHECK_EVERY"
done
