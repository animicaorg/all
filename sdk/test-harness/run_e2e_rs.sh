#!/usr/bin/env bash
# Animica SDK — Rust E2E harness: deploy + call Counter via examples/quickstart.rs
# 
# Usage:
#   ./sdk/test-harness/run_e2e_rs.sh \
#     [--rpc http://127.0.0.1:8545] \
#     [--chain 1337] \
#     [--alg dilithium3] \
#     [--mnemonic "abandon abandon ..."] \
#     [--account-index 0]
#
# Env overrides:
#   RPC_URL, CHAIN_ID, ALG_ID, MNEMONIC, ACCOUNT_INDEX
#   SDK_RS_FEATURES  (e.g. "pq" to enable oqs-backed signers if available)

set -euo pipefail

# --- repo roots ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUST_ROOT="${REPO_ROOT}/rust"

# --- defaults from env ---
RPC="${RPC_URL:-http://127.0.0.1:8545}"
CHAIN="${CHAIN_ID:-0}"
ALG="${ALG_ID:-dilithium3}"
MNEMONIC="${MNEMONIC:-}"
ACCOUNT_INDEX="${ACCOUNT_INDEX:-0}"
FEATURES="${SDK_RS_FEATURES:-}"

# --- tiny argv parser (flag, value) ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rpc) RPC="$2"; shift 2 ;;
    --chain) CHAIN="$2"; shift 2 ;;
    --alg) ALG="$2"; shift 2 ;;
    --mnemonic) MNEMONIC="$2"; shift 2 ;;
    --account-index) ACCOUNT_INDEX="$2"; shift 2 ;;
    --features) FEATURES="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- sanity checks ---
command -v cargo >/dev/null 2>&1 || { echo "cargo not found in PATH" >&2; exit 2; }

# --- build cargo command ---
pushd "${RUST_ROOT}" >/dev/null

CARGO_ARGS=(run -p animica-sdk --example quickstart --release)
if [[ -n "${FEATURES}" ]]; then
  CARGO_ARGS+=(--features "${FEATURES}")
fi
CARGO_ARGS+=(--)

# forward example args
EX_ARGS=(--rpc "${RPC}" --chain "${CHAIN}" --alg "${ALG}" --account-index "${ACCOUNT_INDEX}")
if [[ -n "${MNEMONIC}" ]]; then
  EX_ARGS+=(--mnemonic "${MNEMONIC}")
fi

echo "[rs-e2e] crate=animica-sdk example=quickstart"
echo "[rs-e2e] RPC=${RPC} CHAIN=${CHAIN} ALG=${ALG} ACCOUNT_INDEX=${ACCOUNT_INDEX} FEATURES=${FEATURES:-<none>}"
if [[ -n "${MNEMONIC}" ]]; then echo "[rs-e2e] Using explicit mnemonic from env/args"; fi

# --- run ---
cargo "${CARGO_ARGS[@]}" "${EX_ARGS[@]}"

popd >/dev/null
