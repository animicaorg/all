#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# deploy_and_link.sh — Deploy contracts and wire addresses/ABI into the dapp
#
# What it does:
#   1) (Optional) Builds contracts via ./scripts/build_all.sh or local fallbacks
#   2) Deploys the sample contract(s) in ./contracts
#      - Prefers:   `python -m contracts.tools.deploy`
#      - Fallbacks: `python scripts/deploy_all.py` (if present)
#                   `make deploy` (if Makefile with deploy target)
#   3) Captures the deployed address and writes:
#      - contracts/deployments/${CHAIN_ID}.json
#      - dapp/.env.local      (VITE_RPC_URL, VITE_CHAIN_ID, VITE_CONTRACT_ADDRESS)
#      - dapp/src/contracts/abi.json (copied from contracts/manifest.json)
#
# Usage:
#   ./scripts/deploy_and_link.sh
#   ./scripts/deploy_and_link.sh --no-build
#   ./scripts/deploy_and_link.sh --rpc http://127.0.0.1:8545 --chain 31337
#   ./scripts/deploy_and_link.sh --mnemonic "test test test ..." --account-index 0
#   ./scripts/deploy_and_link.sh --private-key 0xabc123... (overrides mnemonic)
#   ./scripts/deploy_and_link.sh --contract-path contracts/manifest.json
#   ./scripts/deploy_and_link.sh --dry-run (show what would happen)
#
# Env vars (override flags):
#   RPC_URL, CHAIN_ID, DEPLOYER_MNEMONIC, DEPLOYER_PRIVATE_KEY, ACCOUNT_INDEX
#
# Notes:
#   - This script aims to be portable and idempotent.
#   - Customize the "DEPLOY STEP" to use your preferred deployer or CLI.
#   - The dapp wiring assumes Vite/React using VITE_* env vars.
# ------------------------------------------------------------------------------

set -Eeuo pipefail

# ---------------------------- helpers -----------------------------------------
say()   { printf "\033[1;36m[deploy]\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m[warn]\033[0m   %s\n" "$*"; }
err()   { printf "\033[1;31m[error]\033[0m  %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

# ---------------------------- locate repo -------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTRACTS_DIR="$REPO_ROOT/contracts"
DAPP_DIR="$REPO_ROOT/dapp"

# ---------------------------- defaults ----------------------------------------
BUILD=1
DRY_RUN=0
RPC_URL_DEFAULT="${RPC_URL:-http://127.0.0.1:8645}"
CHAIN_ID_DEFAULT="${CHAIN_ID:-31337}"
ACCOUNT_INDEX_DEFAULT="${ACCOUNT_INDEX:-0}"
CONTRACT_MANIFEST_DEFAULT="$CONTRACTS_DIR/manifest.json"

# flags
RPC_URL="$RPC_URL_DEFAULT"
CHAIN_ID="$CHAIN_ID_DEFAULT"
ACCOUNT_INDEX="$ACCOUNT_INDEX_DEFAULT"
MNEMONIC="${DEPLOYER_MNEMONIC:-}"
PRIVKEY="${DEPLOYER_PRIVATE_KEY:-}"
MANIFEST_PATH="$CONTRACT_MANIFEST_DEFAULT"

while (( "$#" )); do
  case "$1" in
    --no-build) BUILD=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --rpc) RPC_URL="${2:?}"; shift 2 ;;
    --chain) CHAIN_ID="${2:?}"; shift 2 ;;
    --mnemonic) MNEMONIC="${2:?}"; shift 2 ;;
    --private-key) PRIVKEY="${2:?}"; shift 2 ;;
    --account-index) ACCOUNT_INDEX="${2:?}"; shift 2 ;;
    --contract-path) MANIFEST_PATH="${2:?}"; shift 2 ;;
    -h|--help)
      sed -n '1,120p' "$0" | sed -n '1,/^# ------------------------------------------------------------------------------$/p' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      die "Unknown argument: $1 (use -h for help)"
      ;;
  esac
done

# ---------------------------- validations -------------------------------------
[ -d "$CONTRACTS_DIR" ] || die "Contracts dir not found: $CONTRACTS_DIR"
[ -f "$MANIFEST_PATH" ] || die "Contract manifest not found: $MANIFEST_PATH"
[ -d "$DAPP_DIR" ] || warn "Dapp dir not found: $DAPP_DIR (dapp wiring will be skipped)"

# best-effort tools
PY=python3
if ! command -v "$PY" >/dev/null 2>&1 && command -v python >/dev/null 2>&1; then
  PY=python
fi

# ---------------------------- dry-run echo ------------------------------------
if (( DRY_RUN )); then
  say "DRY RUN — will not execute changes"
  say "Settings:"
  printf "  RPC_URL         = %s\n" "$RPC_URL"
  printf "  CHAIN_ID        = %s\n" "$CHAIN_ID"
  printf "  ACCOUNT_INDEX   = %s\n" "$ACCOUNT_INDEX"
  printf "  MANIFEST_PATH   = %s\n" "$MANIFEST_PATH"
  printf "  BUILD           = %s\n" "$BUILD"
  printf "  MNEMONIC set    = %s\n" "$([ -n "$MNEMONIC" ] && echo yes || echo no)"
  printf "  PRIVATE KEY set = %s\n" "$([ -n "$PRIVKEY" ] && echo yes || echo no)"
fi

# ---------------------------- step: build -------------------------------------
if (( BUILD == 1 )); then
  say "Building contracts (./scripts/build_all.sh)…"
  if (( DRY_RUN )); then
    say "DRY RUN: would run: $REPO_ROOT/scripts/build_all.sh --contracts"
  else
    if [ -x "$REPO_ROOT/scripts/build_all.sh" ]; then
      "$REPO_ROOT/scripts/build_all.sh" --contracts
    else
      warn "scripts/build_all.sh not found; trying contract-local fallbacks…"
      # fallback to Makefile or nothing
      if [ -f "$CONTRACTS_DIR/Makefile" ] && make -C "$CONTRACTS_DIR" -n build >/dev/null 2>&1; then
        make -C "$CONTRACTS_DIR" build
      else
        warn "No Makefile build target; assuming contracts already built or not required."
      fi
    fi
  fi
else
  say "Skipping build (--no-build)."
fi

# ---------------------------- step: deploy ------------------------------------
DEPLOYMENTS_DIR="$CONTRACTS_DIR/deployments"
DEPLOY_OUT="$DEPLOYMENTS_DIR/${CHAIN_ID}.json"
mkdir -p "$DEPLOYMENTS_DIR"

say "Deploying from manifest: $MANIFEST_PATH"
say "Target RPC: $RPC_URL  Chain: $CHAIN_ID"

ADDRESS=""
DEPLOY_METHOD=""

if (( DRY_RUN )); then
  say "DRY RUN: would attempt python -m contracts.tools.deploy first."
else
  # Prefer python -m contracts.tools.deploy if available
  if "$PY" - <<'PY' >/dev/null 2>&1; then
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("contracts.tools.deploy") else 1)
PY
  then
    DEPLOY_METHOD="contracts.tools.deploy"
    say "Using python module deployer: contracts.tools.deploy"
    # Build deploy command
    DEPLOY_CMD=( "$PY" -m contracts.tools.deploy
      --manifest "$MANIFEST_PATH"
      --rpc "$RPC_URL"
      --chain-id "$CHAIN_ID"
      --json
    )
    if [ -n "$PRIVKEY" ]; then
      DEPLOY_CMD+=( --private-key "$PRIVKEY" )
    elif [ -n "$MNEMONIC" ]; then
      DEPLOY_CMD+=( --mnemonic "$MNEMONIC" --account-index "$ACCOUNT_INDEX" )
    fi

    # Run deployer and capture JSON
    DEPLOY_JSON="$("${DEPLOY_CMD[@]}")" || die "Deployer execution failed."
    # Try to parse address from JSON (jq if available, else greps)
    if command -v jq >/dev/null 2>&1; then
      ADDRESS="$(printf '%s' "$DEPLOY_JSON" | jq -r '.address // .contract.address // .contracts[0].address // empty')"
    fi
    if [ -z "$ADDRESS" ]; then
      # crude regex fallback
      ADDRESS="$(printf '%s' "$DEPLOY_JSON" | sed -nE 's/.*"address"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1)"
    fi
    [ -n "$ADDRESS" ] || die "Could not parse deployed address from deployer output."
    printf '%s\n' "$DEPLOY_JSON" > "$DEPLOY_OUT"
    say "Deployed address: $ADDRESS"
  elif [ -f "$CONTRACTS_DIR/scripts/deploy_all.py" ]; then
    DEPLOY_METHOD="scripts/deploy_all.py"
    say "Using local deployer: contracts/scripts/deploy_all.py"
    if command -v jq >/dev/null 2>&1; then
      ADDR_JSON="$("$PY" "$CONTRACTS_DIR/scripts/deploy_all.py" \
        --rpc "$RPC_URL" --chain "$CHAIN_ID" ${MNEMONIC:+--mnemonic "$MNEMONIC"} ${PRIVKEY:+--private-key "$PRIVKEY"} \
        | tee /dev/stderr)"
      ADDRESS="$(printf '%s' "$ADDR_JSON" | jq -r '.contracts[0].address // .address // empty')"
    else
      ADDR_JSON="$("$PY" "$CONTRACTS_DIR/scripts/deploy_all.py" \
        --rpc "$RPC_URL" --chain "$CHAIN_ID" ${MNEMONIC:+--mnemonic "$MNEMONIC"} ${PRIVKEY:+--private-key "$PRIVKEY"} )"
      ADDRESS="$(printf '%s' "$ADDR_JSON" | sed -nE 's/.*"address"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1)"
    fi
    [ -n "$ADDRESS" ] || die "Could not parse deployed address from scripts/deploy_all.py output."
    printf '%s\n' "$ADDR_JSON" > "$DEPLOY_OUT"
    say "Deployed address: $ADDRESS"
  elif [ -f "$CONTRACTS_DIR/Makefile" ] && make -C "$CONTRACTS_DIR" -n deploy >/dev/null 2>&1; then
    DEPLOY_METHOD="make deploy"
    say "Using Makefile: make deploy"
    MAKE_OUT="$(make -C "$CONTRACTS_DIR" deploy RPC_URL="$RPC_URL" CHAIN_ID="$CHAIN_ID" MNEMONIC="$MNEMONIC" PRIVKEY="$PRIVKEY" | tee /dev/stderr)"
    # try to find address in output
    if command -v jq >/dev/null 2>&1; then
      ADDRESS="$(printf '%s' "$MAKE_OUT" | jq -r '.address // .contract.address // .contracts[0].address // empty')"
    fi
    if [ -z "$ADDRESS" ]; then
      ADDRESS="$(printf '%s' "$MAKE_OUT" | sed -nE 's/.*(0x[0-9a-fA-F]{40}).*/\1/p' | head -n1)"
    fi
    [ -n "$ADDRESS" ] || die "Could not locate contract address in make deploy output."
    printf '%s\n' "$MAKE_OUT" > "$DEPLOY_OUT"
    say "Deployed address: $ADDRESS"
  else
    die "No deployer found. Expected one of:
      - python -m contracts.tools.deploy
      - contracts/scripts/deploy_all.py
      - make -C contracts deploy"
  fi
fi

# For DRY_RUN, fabricate a placeholder address to continue the wiring preview.
if (( DRY_RUN )); then
  ADDRESS="${ADDRESS:-0xDeaDbeefDeaDbeefDeaDbeefDeaDbeefDeaDbeef}"
  say "DRY RUN: using placeholder address $ADDRESS for wiring steps."
  printf '{ "chain_id":"%s","address":"%s","manifest":"%s" }\n' "$CHAIN_ID" "$ADDRESS" "$MANIFEST_PATH" > "$DEPLOY_OUT"
fi

# ---------------------------- step: write deployments json ---------------------
# Ensure a minimal normalized JSON exists (helpful for other tooling).
if command -v jq >/dev/null 2>&1; then
  say "Normalizing deployments JSON → $DEPLOY_OUT"
  jq -n --arg chain "$CHAIN_ID" --arg addr "$ADDRESS" --arg manifest "$(realpath "$MANIFEST_PATH" 2>/dev/null || echo "$MANIFEST_PATH")" \
    '{ chain_id: $chain, contracts: [ { name: "Main", address: $addr, manifest: $manifest } ] }' > "$DEPLOY_OUT.tmp"
  mv "$DEPLOY_OUT.tmp" "$DEPLOY_OUT"
else
  warn "jq not found; writing a minimal deployments JSON."
  printf '{ "chain_id":"%s", "contracts":[{"name":"Main","address":"%s","manifest":"%s"}] }\n' \
    "$CHAIN_ID" "$ADDRESS" "$MANIFEST_PATH" > "$DEPLOY_OUT"
fi
say "Wrote deployments → $DEPLOY_OUT"

# ---------------------------- step: wire dapp env ------------------------------
if [ -d "$DAPP_DIR" ]; then
  ENV_FILE="$DAPP_DIR/.env.local"
  say "Wiring dapp env → $ENV_FILE"
  mkdir -p "$DAPP_DIR"
  touch "$ENV_FILE"

  # write/update keys in-place (preserve comments/unknown keys)
  upsert_env() {
    local key="$1"; shift
    local val="$1"; shift
    if grep -qE "^[#[:space:]]*${key}=" "$ENV_FILE"; then
      # replace existing
      sed -i.bak -E "s|^([#[:space:]]*${key}=).*|\1${val}|g" "$ENV_FILE"
    else
      printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
  }

  if (( DRY_RUN )); then
    say "DRY RUN: would set VITE_RPC_URL=$RPC_URL"
    say "DRY RUN: would set VITE_CHAIN_ID=$CHAIN_ID"
    say "DRY RUN: would set VITE_CONTRACT_ADDRESS=$ADDRESS"
  else
    upsert_env "VITE_RPC_URL" "$RPC_URL"
    upsert_env "VITE_CHAIN_ID" "$CHAIN_ID"
    upsert_env "VITE_CONTRACT_ADDRESS" "$ADDRESS"
    rm -f "$ENV_FILE.bak" || true
  fi
else
  warn "Dapp dir missing; skipping env wiring."
fi

# ---------------------------- step: copy ABI for dapp --------------------------
if [ -d "$DAPP_DIR" ]; then
  ABI_OUT_DIR="$DAPP_DIR/src/contracts"
  ABI_OUT_FILE="$ABI_OUT_DIR/abi.json"
  say "Syncing ABI for dapp → $ABI_OUT_FILE"
  mkdir -p "$ABI_OUT_DIR"

  # Extract "abi" from manifest if possible; else copy entire manifest.
  if command -v jq >/dev/null 2>&1; then
    if (( DRY_RUN )); then
      say "DRY RUN: would extract .abi from $MANIFEST_PATH to $ABI_OUT_FILE"
    else
      if jq -e '.abi' "$MANIFEST_PATH" >/dev/null 2>&1; then
        jq '.abi' "$MANIFEST_PATH" > "$ABI_OUT_FILE"
      else
        warn ".abi not found in manifest; copying entire manifest as ABI surrogate."
        cp "$MANIFEST_PATH" "$ABI_OUT_FILE"
      fi
    fi
  else
    warn "jq not found; copying manifest to ABI path."
    (( DRY_RUN )) || cp "$MANIFEST_PATH" "$ABI_OUT_FILE"
  fi
else
  warn "Dapp dir missing; skipping ABI wiring."
fi

# ---------------------------- summary -----------------------------------------
say "Summary:"
printf "  Chain ID:             %s\n" "$CHAIN_ID"
printf "  RPC URL:              %s\n" "$RPC_URL"
printf "  Contract address:     %s\n" "$ADDRESS"
printf "  Deployments file:     %s\n" "$DEPLOY_OUT"
if [ -d "$DAPP_DIR" ]; then
  printf "  Dapp env:             %s\n" "$DAPP_DIR/.env.local"
  printf "  Dapp ABI:             %s\n" "$DAPP_DIR/src/contracts/abi.json"
fi

say "Done."
