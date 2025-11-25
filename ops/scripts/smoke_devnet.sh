#!/usr/bin/env bash
# Animica Ops — Devnet Smoke Test
# 1) Curl JSON-RPC (chain.getHead / chain.getChainId)
# 2) Observe at least +1 block (miner running)
# 3) Check Explorer HTTP and Prometheus-style /metrics endpoints
#
# Usage:
#   ./ops/scripts/smoke_devnet.sh
#   ./ops/scripts/smoke_devnet.sh --strict     # require Explorer & metrics (fail if missing)
#
# Env overrides:
#   RPC_HTTP_URL (default: http://localhost:8545/rpc)
#   EXPLORER_URL (default: http://localhost:8081)
#   SERVICES_URL (default: http://localhost:8787)
#   DA_API_URL   (default: http://localhost:8688)
#   RPC_METRICS_URL (defaults to ${RPC_HTTP_URL%/rpc}/metrics)
#   SERVICES_METRICS_URL (default: ${SERVICES_URL}/metrics)
#   DA_METRICS_URL (default: ${DA_API_URL}/metrics)
#   CHAIN_ID (optional expected value; compared if set)
#   TIMEOUT_SEC (default: 120)
#
set -euo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
  shift || true
fi

# ---------- pretty output ----------
RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; CYN=$'\033[36m'; BOLD=$'\033[1m'; RST=$'\033[0m'
ok()   { printf "%b✓%b %s\n" "$GRN" "$RST" "$*"; }
warn() { printf "%b!%b %s\n" "$YEL" "$RST" "$*"; }
fail() { printf "%b✗%b %s\n" "$RED" "$RST" "$*"; }
headln(){ printf "\n%s%s%s\n" "$BOLD" "$*" "$RST"; }

need() { command -v "$1" >/dev/null 2>&1 || { fail "missing dependency: $1"; exit 1; }; }

# ---------- deps ----------
need curl
need jq

# ---------- config ----------
RPC_HTTP_URL="${RPC_HTTP_URL:-http://localhost:8545/rpc}"
EXPLORER_URL="${EXPLORER_URL:-http://localhost:8081}"
SERVICES_URL="${SERVICES_URL:-http://localhost:8787}"
DA_API_URL="${DA_API_URL:-http://localhost:8688}"

# derive default /metrics from RPC base
RPC_BASE="${RPC_HTTP_URL%/rpc}"
RPC_METRICS_URL="${RPC_METRICS_URL:-${RPC_BASE}/metrics}"
SERVICES_METRICS_URL="${SERVICES_METRICS_URL:-${SERVICES_URL}/metrics}"
DA_METRICS_URL="${DA_METRICS_URL:-${DA_API_URL}/metrics}"

TIMEOUT_SEC="${TIMEOUT_SEC:-120}"

# ---------- helpers ----------
rpc_call() {
  local method="$1"; shift
  local params_json="${1:-[]}"
  curl -sS -X POST \
    -H 'Content-Type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"${method}\",\"params\":${params_json}}" \
    "${RPC_HTTP_URL}"
}

get_height() {
  local resp="$1"
  jq -r '.result.height // .result.number // .result | select(type=="number")' <<<"$resp"
}

http_ok() {
  # returns 0 if HTTP 200..399
  local url="$1"
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" || true)"
  [[ "$code" =~ ^2|3[0-9]{2}$ ]]
}

metrics_ok() {
  local url="$1"
  local body
  body="$(curl -fsS "$url" 2>/dev/null || true)"
  [[ -n "$body" ]] && grep -qE '^# (HELP|TYPE) ' <<<"$body"
}

# ---------- step 1: JSON-RPC sanity ----------
headln "Step 1 — JSON-RPC sanity @ ${CYN}${RPC_HTTP_URL}${RST}"
RESP_HEAD="$(rpc_call "chain.getHead")" || { fail "chain.getHead request failed"; exit 1; }
if [[ "$(jq -r '.error? // empty' <<<"$RESP_HEAD")" != "" ]]; then
  fail "JSON-RPC error: $(jq -c '.error' <<<"$RESP_HEAD")"; exit 1;
fi
HEIGHT="$(get_height "$RESP_HEAD" || true)"
if [[ -z "${HEIGHT:-}" ]]; then
  fail "Could not parse head height from response: $(jq -c '.' <<<"$RESP_HEAD")"
  exit 1
fi
ok "Head height: ${BOLD}${HEIGHT}${RST}"

RESP_CHAINID="$(rpc_call "chain.getChainId")" || { fail "chain.getChainId request failed"; exit 1; }
CHAIN_ID_ACTUAL="$(jq -r '.result // .error // empty' <<<"$RESP_CHAINID")"
if [[ -z "${CHAIN_ID_ACTUAL}" || "${CHAIN_ID_ACTUAL}" == "null" || "${CHAIN_ID_ACTUAL}" == *"code"* ]]; then
  warn "Could not parse chainId cleanly (response: $(jq -c '.' <<<"$RESP_CHAINID"))."
else
  ok "ChainId: ${BOLD}${CHAIN_ID_ACTUAL}${RST}"
  if [[ -n "${CHAIN_ID:-}" && "${CHAIN_ID_ACTUAL}" != "${CHAIN_ID}" ]]; then
    fail "Expected CHAIN_ID=${CHAIN_ID}, got ${CHAIN_ID_ACTUAL}"
    exit 1
  fi
fi

# ---------- step 2: observe +1 block ----------
headln "Step 2 — Waiting for miner to advance head by ≥ 1 block"
TARGET=$(( HEIGHT + 1 ))
DEADLINE=$(( SECONDS + TIMEOUT_SEC ))
LAST_H="$HEIGHT"

while (( SECONDS < DEADLINE )); do
  sleep 2
  RESP="$(rpc_call "chain.getHead")" || true
  CUR="$(get_height "$RESP" || echo -1)"
  if [[ "$CUR" =~ ^[0-9]+$ ]]; then
    if (( CUR > LAST_H )); then
      LAST_H="$CUR"
      printf "%b•%b height now %s\n" "$CYN" "$RST" "$LAST_H"
    fi
    if (( CUR >= TARGET )); then
      ok "Head advanced to ${BOLD}${CUR}${RST} (Δ = $(( CUR - HEIGHT )))"
      break
    fi
  else
    warn "Could not read height; response: $(jq -c '.' <<<"$RESP")"
  fi
done

if (( LAST_H < TARGET )); then
  fail "Miner did not produce a block within ${TIMEOUT_SEC}s (still at height ${LAST_H})."
  exit 1
fi

# ---------- step 3: Explorer & Metrics ----------
REQ_FAILS=0

headln "Step 3 — Explorer HTTP check @ ${CYN}${EXPLORER_URL}${RST}"
if http_ok "${EXPLORER_URL}"; then
  ok "Explorer reachable (HTTP OK)"
else
  if (( STRICT )); then
    fail "Explorer not reachable at ${EXPLORER_URL}"
    REQ_FAILS=$((REQ_FAILS+1))
  else
    warn "Explorer not reachable at ${EXPLORER_URL} (continuing; --strict to require)"
  fi
fi

headln "Step 3a — RPC /metrics @ ${CYN}${RPC_METRICS_URL}${RST}"
if metrics_ok "${RPC_METRICS_URL}"; then
  ok "RPC metrics endpoint looks healthy"
else
  if (( STRICT )); then
    fail "RPC metrics endpoint missing or invalid at ${RPC_METRICS_URL}"
    REQ_FAILS=$((REQ_FAILS+1))
  else
    warn "RPC metrics endpoint missing or invalid at ${RPC_METRICS_URL}"
  fi
fi

headln "Step 3b — Services /metrics @ ${CYN}${SERVICES_METRICS_URL}${RST}"
if metrics_ok "${SERVICES_METRICS_URL}"; then
  ok "Studio-Services metrics endpoint looks healthy"
else
  if (( STRICT )); then
    fail "Services metrics invalid at ${SERVICES_METRICS_URL}"
    REQ_FAILS=$((REQ_FAILS+1))
  else
    warn "Services metrics invalid at ${SERVICES_METRICS_URL}"
  fi
fi

headln "Step 3c — DA /metrics @ ${CYN}${DA_METRICS_URL}${RST}"
if metrics_ok "${DA_METRICS_URL}"; then
  ok "DA metrics endpoint looks healthy"
else
  if (( STRICT )); then
    fail "DA metrics invalid at ${DA_METRICS_URL}"
    REQ_FAILS=$((REQ_FAILS+1))
  else
    warn "DA metrics invalid at ${DA_METRICS_URL}"
  fi
fi

# ---------- summary ----------
headln "Summary"
if (( REQ_FAILS == 0 )); then
  ok "Smoke test passed."
  echo
  echo "Endpoints:"
  echo "  RPC HTTP:      ${RPC_HTTP_URL}"
  echo "  Explorer:      ${EXPLORER_URL}"
  echo "  RPC /metrics:  ${RPC_METRICS_URL}"
  echo "  Services:      ${SERVICES_URL}   (/metrics checked)"
  echo "  DA API:        ${DA_API_URL}     (/metrics checked)"
  exit 0
else
  fail "Smoke test encountered ${REQ_FAILS} failing checks."
  exit 1
fi
