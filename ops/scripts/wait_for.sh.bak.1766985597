#!/usr/bin/env bash
# Animica Ops — wait_for.sh
# Wait for endpoints (HTTP/HTTPS, WS/WSS, or TCP) to become reachable within a timeout.
#
# Usage:
#   ./ops/scripts/wait_for.sh [--timeout 120] [--interval 2] [--silent] <endpoint> [<endpoint> ...]
#
# Endpoint forms:
#   - http://host:port/path        (HTTP HEAD/GET considered OK on 2xx/3xx)
#   - https://host:port/path
#   - ws://host:port/path          (expects HTTP 101 Switching Protocols; falls back to TCP check)
#   - wss://host:port/path
#   - tcp://host:port              (raw TCP connect check)
#   - host:port                    (shorthand for tcp://host:port)
#
# Env (defaults):
#   WAIT_TIMEOUT=120
#   WAIT_INTERVAL=2
#   WAIT_SILENT=0   (1 to suppress progress lines; final summary still printed)
#
set -euo pipefail

# --------------------------- config / flags -----------------------------------
TIMEOUT="${WAIT_TIMEOUT:-120}"
INTERVAL="${WAIT_INTERVAL:-2}"
SILENT="${WAIT_SILENT:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout) TIMEOUT="${2:?}"; shift 2 ;;
    --interval) INTERVAL="${2:?}"; shift 2 ;;
    --silent) SILENT=1; shift ;;
    --help|-h)
      grep -E '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *)
      break ;;
  esac
done

if [[ $# -lt 1 ]]; then
  echo "ERROR: no endpoints provided. See --help." >&2
  exit 2
fi

ENDPOINTS=("$@")

# --------------------------- pretty output ------------------------------------
RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; CYN=$'\033[36m'; BOLD=$'\033[1m'; RST=$'\033[0m'
log()   { [[ "$SILENT" -eq 1 ]] || printf "%s\n" "$*"; }
ok()    { printf "%b✓%b %s\n" "$GRN" "$RST" "$*"; }
warn()  { printf "%b!%b %s\n" "$YEL" "$RST" "$*"; }
fail()  { printf "%b✗%b %s\n" "$RED" "$RST" "$*"; }

need() { command -v "$1" >/dev/null 2>&1; }

# --------------------------- helpers ------------------------------------------
is_http() { [[ "$1" =~ ^https?:// ]]; }
is_ws()   { [[ "$1" =~ ^wss?:// ]]; }
is_tcp()  { [[ "$1" =~ ^tcp:// ]] || [[ "$1" =~ ^[^:/]+\:[0-9]+$ ]] || [[ "$1" =~ ^\[[0-9a-fA-F:]+\]\:[0-9]+$ ]]; }

# Extract host and port from URL or host:port
# Sets HOST and PORT globals.
HOST=""; PORT=""
url_host_port() {
  local url="$1" scheme rest hostport
  HOST=""; PORT=""
  if [[ "$url" =~ ^(https?|wss?|tcp):// ]]; then
    scheme="${url%%://*}"
    rest="${url#*://}"
    hostport="${rest%%/*}"
  else
    hostport="$url"
    scheme="tcp"
  fi

  if [[ "$hostport" =~ ^\[[0-9a-fA-F:]+\]\:[0-9]+$ ]]; then
    HOST="${hostport%%]*}"; HOST="${HOST#\[}"
    PORT="${hostport##*:}"
  elif [[ "$hostport" =~ ^[^:]+:[0-9]+$ ]]; then
    HOST="${hostport%%:*}"
    PORT="${hostport##*:}"
  else
    # No port specified; infer from scheme
    HOST="$hostport"
    case "$scheme" in
      http|ws)  PORT="80" ;;
      https|wss) PORT="443" ;;
      *) PORT="" ;;
    esac
  fi
}

# HTTP check: 2xx/3xx considered OK
check_http() {
  local url="$1" code=""
  if need curl; then
    # Try HEAD first, then GET (some servers don't permit HEAD)
    code="$(curl -sS -o /dev/null -w '%{http_code}' -I "$url" || true)"
    if [[ ! "$code" =~ ^2|3[0-9]{2}$ ]]; then
      code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" || true)"
    fi
    [[ "$code" =~ ^2|3[0-9]{2}$ ]]
  else
    warn "curl not found; falling back to TCP check for $url"
    url_host_port "$url"
    check_tcp_hostport "$HOST" "$PORT"
  fi
}

# WS check: expect HTTP 101 Switching Protocols; fallback to TCP port open
check_ws() {
  local url="$1" code=""
  if need curl; then
    code="$(curl -sS --http1.1 -o /dev/null -w '%{http_code}' \
      -H 'Connection: Upgrade' \
      -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' \
      -H 'Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==' \
      "$url" || true)"
    if [[ "$code" == "101" ]]; then
      return 0
    fi
    # Some gateways respond 2xx before upgrading; accept TCP as fallback
    url_host_port "$url"
    check_tcp_hostport "$HOST" "$PORT"
  else
    warn "curl not found; falling back to TCP check for $url"
    url_host_port "$url"
    check_tcp_hostport "$HOST" "$PORT"
  fi
}

# TCP check using nc if available, else bash /dev/tcp with timeout
check_tcp_hostport() {
  local host="$1" port="$2"
  if [[ -z "$host" || -z "$port" ]]; then
    return 1
  fi
  if need nc; then
    nc -z -w 1 "$host" "$port" >/dev/null 2>&1
    return $?
  fi
  if need timeout; then
    timeout 2 bash -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1
  else
    # Last resort (may block briefly)
    bash -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1
  fi
}

check_tcp() {
  local target="$1"
  url_host_port "$target"
  check_tcp_hostport "$HOST" "$PORT"
}

wait_one() {
  local target="$1" start_ts deadline ok_flag=1
  start_ts="$(date +%s)"
  deadline=$(( start_ts + TIMEOUT ))
  while :; do
    if is_http "$target"; then
      if check_http "$target"; then ok_flag=0; break; fi
    elif is_ws "$target"; then
      if check_ws "$target"; then ok_flag=0; break; fi
    elif is_tcp "$target"; then
      if check_tcp "$target"; then ok_flag=0; break; fi
    else
      warn "Unknown endpoint scheme for '$target' — treating as TCP"
      if check_tcp "$target"; then ok_flag=0; break; fi
    fi

    if (( $(date +%s) >= deadline )); then
      ok_flag=1; break
    fi
    log "… waiting for ${CYN}${target}${RST} (retry in ${INTERVAL}s)"
    sleep "$INTERVAL"
  done
  return "$ok_flag"
}

# --------------------------- main ---------------------------------------------
TOTAL=${#ENDPOINTS[@]}
echo -e "${BOLD}Waiting for ${TOTAL} endpoint(s) (timeout=${TIMEOUT}s, interval=${INTERVAL}s)…${RST}"

FAILED=0
for ep in "${ENDPOINTS[@]}"; do
  printf "Checking %s%s%s … " "$CYN" "$ep" "$RST"
  if wait_one "$ep"; then
    printf "%bOK%b\n" "$GRN" "$RST"
  else
    printf "%bTIMEOUT%b\n" "$RED" "$RST"
    FAILED=$((FAILED+1))
  fi
done

if (( FAILED == 0 )); then
  ok "All endpoints are reachable."
  exit 0
else
  fail "${FAILED}/${TOTAL} endpoint(s) did not become ready within ${TIMEOUT}s."
  exit 1
fi
