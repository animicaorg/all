#!/usr/bin/env bash
# Block until Animica devnet services are ready (RPC/WS/services/explorer)
# Usage:
#   bash tests/devnet/wait_for_services.sh
#
# Environment overrides:
#   NODE1_RPC       (default: http://localhost:8545)
#   NODE2_RPC       (default: http://localhost:9545)  # optional
#   SERVICES_URL    (default: http://localhost:8787)  # studio-services
#   EXPLORER_URL    (default: http://localhost:5173)
#   WAIT_NODE2      (default: 1)  # set 0 to skip waiting on node2
#   WAIT_EXPLORER   (default: 1)  # set 0 to skip waiting on explorer
#   WAIT_WS         (default: 1)  # also check TCP/WS port reachability
#   WAIT_TIMEOUT    (default: 180) # total seconds per target
#   SLEEP_INTERVAL  (default: 2)   # poll interval seconds

set -euo pipefail

NODE1_RPC="${NODE1_RPC:-http://localhost:8545}"
NODE2_RPC="${NODE2_RPC:-http://localhost:9545}"
SERVICES_URL="${SERVICES_URL:-http://localhost:8787}"
EXPLORER_URL="${EXPLORER_URL:-http://localhost:5173}"

WAIT_NODE2="${WAIT_NODE2:-1}"
WAIT_EXPLORER="${WAIT_EXPLORER:-1}"
WAIT_WS="${WAIT_WS:-1}"

WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"
SLEEP_INTERVAL="${SLEEP_INTERVAL:-2}"

bold() { printf "\033[1m%s\033[0m" "$*"; }
green() { printf "\033[32m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }
red() { printf "\033[31m%s\033[0m" "$*"; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# Extract host and port from a URL like http://host:port/...
# Sets HOST_OUT and PORT_OUT global vars.
parse_host_port() {
  local url="$1"
  HOST_OUT="$(printf '%s' "$url" | sed -E 's#^[a-zA-Z]+://([^/:]+).*#\1#')"
  PORT_OUT="$(printf '%s' "$url" | sed -En 's#^[a-zA-Z]+://[^/:]+:([0-9]+).*#\1#p')"
  if [[ -z "${PORT_OUT}" ]]; then
    # Default ports if missing
    if [[ "$url" =~ ^https:// ]]; then PORT_OUT=443; else PORT_OUT=80; fi
  fi
}

# Wait until GET $1 returns a 200..399 HTTP code
wait_http_ok() {
  local url="$1"
  local label="${2:-$url}"
  local deadline=$(( $(date +%s) + WAIT_TIMEOUT ))

  if ! have_cmd curl; then
    echo "$(red "ERROR") curl is required for HTTP checks" >&2
    return 1
  fi

  printf "Waiting for %s (HTTP)... " "$(bold "$label")"
  while true; do
    if code=$(curl -fsS -o /dev/null -w '%{http_code}' "$url") 2>/dev/null; then
      if [[ "$code" =~ ^(2|3)[0-9]{2}$ ]]; then
        echo "$(green "ready") ($code)"
        return 0
      fi
    fi
    if (( $(date +%s) >= deadline )); then
      echo "$(red "timeout after ${WAIT_TIMEOUT}s")"
      return 1
    fi
    sleep "$SLEEP_INTERVAL"
  done
}

# Wait until TCP $host:$port accepts connections (WS or HTTP)
wait_tcp_up() {
  local host="$1" port="$2" label="${3:-$host:$port}"
  local deadline=$(( $(date +%s) + WAIT_TIMEOUT ))

  printf "Waiting for %s (TCP)... " "$(bold "$label")"

  # Prefer bash's /dev/tcp if available
  while true; do
    if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
      exec 3>&-  # close
      echo "$(green "ready")"
      return 0
    fi

    # Fallback: nc -z
    if have_cmd nc; then
      if nc -z "$host" "$port" >/dev/null 2>&1; then
        echo "$(green "ready")"
        return 0
      fi
    fi

    if (( $(date +%s) >= deadline )); then
      echo "$(red "timeout after ${WAIT_TIMEOUT}s")"
      return 1
    fi
    sleep "$SLEEP_INTERVAL"
  done
}

echo ""
echo "⛓  $(bold "Animica devnet readiness check")"
echo "    Node1 RPC:      $NODE1_RPC"
echo "    Node2 RPC:      $NODE2_RPC (WAIT_NODE2=$WAIT_NODE2)"
echo "    Services URL:   $SERVICES_URL"
echo "    Explorer URL:   $EXPLORER_URL (WAIT_EXPLORER=$WAIT_EXPLORER)"
echo "    WS/TCP checks:  $WAIT_WS"
echo "    Timeout/target: ${WAIT_TIMEOUT}s, interval: ${SLEEP_INTERVAL}s"
echo ""

# --- Node1: /healthz then /readyz; optional WS/TCP port
wait_http_ok "${NODE1_RPC%/}/healthz" "node1 /healthz"
wait_http_ok "${NODE1_RPC%/}/readyz"  "node1 /readyz"
if [[ "$WAIT_WS" == "1" ]]; then
  parse_host_port "$NODE1_RPC"
  wait_tcp_up "$HOST_OUT" "$PORT_OUT" "node1 WS/TCP ${HOST_OUT}:${PORT_OUT}"
fi

# --- Node2 (optional)
if [[ "$WAIT_NODE2" == "1" ]]; then
  wait_http_ok "${NODE2_RPC%/}/healthz" "node2 /healthz"
  wait_http_ok "${NODE2_RPC%/}/readyz"  "node2 /readyz"
  if [[ "$WAIT_WS" == "1" ]]; then
    parse_host_port "$NODE2_RPC"
    wait_tcp_up "$HOST_OUT" "$PORT_OUT" "node2 WS/TCP ${HOST_OUT}:${PORT_OUT}"
  fi
else
  echo "$(yellow "Skipping node2 checks (WAIT_NODE2=0)")"
fi

# --- studio-services
wait_http_ok "${SERVICES_URL%/}/healthz" "studio-services /healthz"

# --- explorer (optional)
if [[ "$WAIT_EXPLORER" == "1" ]]; then
  wait_http_ok "${EXPLORER_URL%/}/" "explorer-web / (preview)"
else
  echo "$(yellow "Skipping explorer checks (WAIT_EXPLORER=0)")"
fi

echo ""
echo "$(green "✔ All requested services are ready.")"
echo "   - RPC:      ${NODE1_RPC}"
echo "   - Services: ${SERVICES_URL}"
if [[ "$WAIT_NODE2" == "1" ]]; then
  echo "   - RPC-2:    ${NODE2_RPC}"
fi
if [[ "$WAIT_EXPLORER" == "1" ]]; then
  echo "   - Explorer: ${EXPLORER_URL}"
fi
echo ""

# Optional: emit a tiny JSON blob for automation
if have_cmd jq; then
  jq -n --arg node1 "$NODE1_RPC" \
        --arg node2 "$NODE2_RPC" \
        --arg services "$SERVICES_URL" \
        --arg explorer "$EXPLORER_URL" \
        '{node1_rpc:$node1, node2_rpc:$node2, services:$services, explorer:$explorer, ready:true}' \
    | sed 's/^/READY: /'
fi
