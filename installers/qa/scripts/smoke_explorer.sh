#!/usr/bin/env bash
#
# smoke_explorer.sh — Launch Explorer Desktop (best-effort) and sanity check:
#   1) App/web URL reachable
#   2) RPC head reachable & (ideally) moving
#   3) Charts presence (heuristic or API JSON if provided)
#
# Requirements:
#   - bash, curl, python3
#
# Environment:
#   EXPLORER_APP_PATH   Path to the desktop app (e.g., "/Applications/Animica Explorer.app")
#   EXPLORER_URL        Explorer site URL to probe (default: http://127.0.0.1:8080)
#   RPC_URL             Node JSON-RPC base (default: http://127.0.0.1:8545)
#   WS_URL              Optional WS endpoint. If unset, derived from RPC_URL + "/ws"
#   APP_LAUNCH          "1" to try launching the app; "0" to skip (default: 1 on macOS, else 0)
#   CHARTS_URL          Optional explicit charts JSON endpoint (e.g., https://…/api/charts/txs?window=1d)
#   TIMEOUT_SECS        Global timeout for waits (default: 60)
#   STRICT_LIVE_HEAD    If "1", require head to increase during the window (default: 0)
#
# Exit codes:
#   0 success; non-zero on failure with a readable error.
set -euo pipefail

EXPLORER_URL="${EXPLORER_URL:-http://127.0.0.1:8080}"
RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
TIMEOUT_SECS="${TIMEOUT_SECS:-60}"
STRICT_LIVE_HEAD="${STRICT_LIVE_HEAD:-0}"
CHARTS_URL="${CHARTS_URL:-}"

OS="$(uname -s || echo unknown)"
if [[ "${APP_LAUNCH:-}" == "" ]]; then
  if [[ "$OS" == "Darwin" ]]; then APP_LAUNCH="1"; else APP_LAUNCH="0"; fi
fi

# Derive WS_URL if not provided: http(s) -> ws(s), append '/ws' if no path.
if [[ -z "${WS_URL:-}" ]]; then
  if [[ "$RPC_URL" =~ ^https:// ]]; then
    WS_URL="${RPC_URL/https:/wss:}"
  else
    WS_URL="${RPC_URL/http:/ws:}"
  fi
  # Append /ws if no path component beyond host:port
  if [[ ! "$WS_URL" =~ /ws($|[/?#]) ]]; then
    WS_URL="${WS_URL%/}/ws"
  fi
fi

need() { command -v "$1" >/dev/null 2>&1 || { echo "[smoke][ERROR] Missing command: $1" >&2; exit 1; }; }
need curl
need python3

log() { printf "[smoke] %s\n" "$*" >&2; }
die() { printf "[smoke][ERROR] %s\n" "$*" >&2; exit 1; }

launch_app() {
  [[ "$APP_LAUNCH" != "1" ]] && { log "APP_LAUNCH=0 → skipping desktop app launch."; return 0; }

  case "$OS" in
    Darwin)
      if [[ -n "${EXPLORER_APP_PATH:-}" ]]; then
        log "Launching Explorer app: $EXPLORER_APP_PATH"
        open "$EXPLORER_APP_PATH" || log "open failed (continuing; UI launch is best-effort)"
      else
        log "Launching Explorer by name (best-effort)…"
        open -a "Animica Explorer" || log "open -a Animica Explorer failed (continuing)"
      fi
      ;;
    Linux)
      if [[ -n "${EXPLORER_APP_PATH:-}" ]]; then
        log "Launching Explorer binary/AppImage: $EXPLORER_APP_PATH"
        "$EXPLORER_APP_PATH" >/dev/null 2>&1 & disown || log "could not launch (continuing)"
      else
        log "No EXPLORER_APP_PATH set; skipping launch on Linux."
      fi
      ;;
    *)
      log "OS=$OS not explicitly supported for UI launch; continuing without launching."
      ;;
  esac
}

probe_url() {
  local url="$1"
  log "Probing Explorer URL: $url"
  # Capture headers + first 64KB body
  local out stat
  out="$(curl -fsSIL --max-time 10 "$url" || true)"
  stat="$?"
  if [[ "$stat" -ne 0 ]]; then
    # Try GET body for better error info
    out="$(curl -fsS --max-time 15 "$url" || true)"
    [[ -z "$out" ]] && die "Explorer URL not reachable: $url"
  fi
  # Heuristic: require HTML-ish or JSON (if charts endpoint)
  if [[ -n "$CHARTS_URL" && "$url" == "$CHARTS_URL" ]]; then
    echo "$out" | head -c 1 >/dev/null || die "Charts endpoint returned empty."
  else
    # HTML marker
    echo "$out" | grep -qiE '<!DOCTYPE html|<html|<head>' || log "Explorer response not clearly HTML (continuing)."
  fi
  log "Explorer URL reachable."
}

rpc_post() {
  local payload="$1"
  curl -sS --max-time 10 \
    -H 'content-type: application/json' \
    -X POST --data "$payload" "$RPC_URL"
}

check_head_once() {
  rpc_post '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' \
  | python3 - <<'PY' || true
import sys, json
try:
  o=json.load(sys.stdin)
  print(int(o.get("result",{}).get("height", -1)))
except Exception:
  print(-1)
PY
}

check_rpc_head() {
  log "Checking RPC head at $RPC_URL …"
  local h1 h2
  h1="$(check_head_once)"
  [[ "$h1" -ge 0 ]] || die "RPC chain.getHead failed (no height)."
  log "Head height (t0) = $h1"
  sleep 5
  h2="$(check_head_once)"
  [[ "$h2" -ge 0 ]] || die "RPC chain.getHead failed on second read."
  log "Head height (t+5s) = $h2"
  if [[ "$h2" -gt "$h1" ]]; then
    log "Head is moving ✅"
    echo "moving"  # signal to caller
    return 0
  fi
  if [[ "$STRICT_LIVE_HEAD" == "1" ]]; then
    die "Head did not advance within 5s and STRICT_LIVE_HEAD=1."
  else
    log "Head reachable but did not advance (non-blocking)."
    echo "static"
  fi
}

# Optional: naive WS check (best-effort) using Python without external deps.
# We'll just open a TCP to the port to ensure it's listening if websockets lib isn't available.
ws_probe_best_effort() {
python3 - <<PY
import os, sys, time, re, socket, urllib.parse
url = os.environ.get("WS_URL","")
print("[py-ws] WS_URL:", url, file=sys.stderr)
try:
  u = urllib.parse.urlparse(url)
  host, port = u.hostname, u.port or (443 if u.scheme=="wss" else 80)
  # TCP connect test
  s = socket.create_connection((host, port), timeout=5)
  s.close()
  print("ok")
except Exception as e:
  print("[py-ws] best-effort TCP probe failed:", e, file=sys.stderr)
  print("skip")
PY
}

charts_check() {
  if [[ -n "$CHARTS_URL" ]]; then
    log "Checking charts data endpoint: $CHARTS_URL"
    local body
    body="$(curl -fsS --max-time 15 "$CHARTS_URL" || true)"
    [[ -n "$body" ]] || die "Charts endpoint empty."
    echo "$body" | grep -qE '^\s*\{' || echo "$body" | grep -qE '^\s*\[' || log "Charts response not JSON (continuing)."
    log "Charts endpoint reachable."
    echo "charts:ok"
    return 0
  fi

  # Heuristic: homepage HTML includes a chart lib mention (echarts|chartjs)
  log "Heuristic charts check on Explorer homepage…"
  local html
  html="$(curl -fsS --max-time 15 "$EXPLORER_URL" || true)"
  [[ -n "$html" ]] || { log "Homepage empty; skipping charts heuristic."; echo "charts:unknown"; return 0; }
  if echo "$html" | grep -qiE 'echarts|chartjs|<canvas|aria-role="img"'; then
    log "Charts markers found."
    echo "charts:ok"
  else
    log "No obvious chart markers (heuristic)."
    echo "charts:unknown"
  fi
}

main() {
  log "OS=$OS"
  log "EXPLORER_URL=$EXPLORER_URL"
  log "RPC_URL=$RPC_URL  WS_URL=$WS_URL"
  launch_app

  # 1) URL reachable
  probe_url "$EXPLORER_URL"

  # 2) Head reachable (+/- moving)
  local head_state
  head_state="$(check_rpc_head)"

  # 3) WS best-effort probe
  local ws_state
  ws_state="$(WS_URL="$WS_URL" ws_probe_best_effort || true)"

  # 4) Charts presence check
  local chart_state
  chart_state="$(charts_check)"

  # Summary JSON
  python3 - <<PY
import os, json, sys
summary = {
  "explorerUrl": os.environ.get("EXPLORER_URL"),
  "rpcUrl": os.environ.get("RPC_URL"),
  "wsUrl": os.environ.get("WS_URL"),
  "head": "${head_state}",
  "wsProbe": "${ws_state}",
  "charts": "${chart_state}",
}
print(json.dumps(summary, indent=2))
PY
  log "Smoke Explorer completed."
}

main
