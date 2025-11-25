#!/usr/bin/env bash
#
# smoke_wallet.sh — Launch Wallet (best-effort), check RPC connectivity, and send a tiny test tx.
#
# Requirements:
#   - bash, curl, python3
#   - (optional) local repo with sdk/python, or preinstalled omni_sdk (Python)
#
# This script is **non-destructive** by default: it sends a tiny self-transfer on a dev/test network.
# It does **not** modify mainnet unless you deliberately point RPC_URL/CHAIN_ID there.
#
# Environment variables (override as needed):
#   RPC_URL            JSON-RPC endpoint (default: http://127.0.0.1:8545)
#   CHAIN_ID           Numeric chain id (default: 1337)
#   WALLET_APP_PATH    Path to app bundle/binary (e.g., "/Applications/Animica Wallet.app")
#   APP_LAUNCH         "1" to try launching the Wallet UI; "0" to skip (default: 1 on macOS, else 0)
#   SMOKE_AMOUNT       Smallest test amount in chain units (default: 1)
#   WALLET_MNEMONIC    Test mnemonic (ONLY FOR DEV/TEST) — if unset, generates a throwaway mnemonic
#   DEST_ADDRESS       If set, send to this address; otherwise self-transfer
#   TIMEOUT_SECS       Global timeout for waits (default: 60)
#   RPC_CHECK_ONLY     If "1", skip tx and only check RPC connectivity
#   OMNI_SDK_PY_PATH   If set, pip-install the Python SDK from this path (editable)
#   FAUCET_URL         Optional studio-services faucet endpoint (POST) to fund the test account
#   FAUCET_API_KEY     Optional API key header value for faucet ("Authorization: Bearer <key>")
#
# Exit codes:
#   0 success; non-zero on failure with a readable error.
set -euo pipefail

# -------- Defaults --------
RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
CHAIN_ID="${CHAIN_ID:-1337}"
SMOKE_AMOUNT="${SMOKE_AMOUNT:-1}"
TIMEOUT_SECS="${TIMEOUT_SECS:-60}"
RPC_CHECK_ONLY="${RPC_CHECK_ONLY:-0}"
OMNI_SDK_PY_PATH="${OMNI_SDK_PY_PATH:-}"
FAUCET_URL="${FAUCET_URL:-}"
FAUCET_API_KEY="${FAUCET_API_KEY:-}"
DEST_ADDRESS="${DEST_ADDRESS:-}"

OS="$(uname -s || echo unknown)"
if [[ "${APP_LAUNCH:-}" == "" ]]; then
  if [[ "$OS" == "Darwin" ]]; then APP_LAUNCH="1"; else APP_LAUNCH="0"; fi
fi
WALLET_APP_PATH="${WALLET_APP_PATH:-}"

log() { printf "[smoke] %s\n" "$*" >&2; }
die() { printf "[smoke][ERROR] %s\n" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

need curl
need python3

# -------- Try to launch Wallet UI (best-effort) --------
launch_wallet() {
  if [[ "$APP_LAUNCH" != "1" ]]; then
    log "APP_LAUNCH=0 → skipping UI launch."
    return 0
  fi

  case "$OS" in
    Darwin)
      # Prefer explicit path; otherwise try default .app bundle name.
      if [[ -n "$WALLET_APP_PATH" ]]; then
        log "Launching Wallet app: $WALLET_APP_PATH"
        open "$WALLET_APP_PATH" || log "open failed (continuing; UI launch is best-effort)"
      else
        log "Launching Wallet app by name (best-effort)…"
        open -a "Animica Wallet" || log "open -a Animica Wallet failed (continuing)"
      fi
      ;;
    Linux)
      if [[ -n "$WALLET_APP_PATH" ]]; then
        log "Launching Wallet binary/AppImage: $WALLET_APP_PATH"
        "$WALLET_APP_PATH" >/dev/null 2>&1 & disown || log "could not launch (continuing)"
      else
        log "No WALLET_APP_PATH set; skipping launch on Linux."
      fi
      ;;
    *)
      log "OS=$OS not explicitly supported for UI launch; continuing without launching."
      ;;
  esac
}

# -------- RPC sanity: getParams & getHead --------
rpc_post() {
  local payload="$1"
  curl -sS --max-time 10 \
    -H 'content-type: application/json' \
    -X POST --data "$payload" "$RPC_URL"
}

check_rpc() {
  log "Checking RPC at $RPC_URL …"
  local params head
  params="$(rpc_post '{"jsonrpc":"2.0","id":1,"method":"chain.getParams","params":[]}' || true)"
  [[ -n "$params" ]] || die "RPC chain.getParams returned empty"
  echo "$params" | grep -q '"result"' || die "RPC chain.getParams missing result"
  log "chain.getParams OK"

  head="$(rpc_post '{"jsonrpc":"2.0","id":2,"method":"chain.getHead","params":[]}' || true)"
  [[ -n "$head" ]] || die "RPC chain.getHead returned empty"
  echo "$head" | grep -q '"result"' || die "RPC chain.getHead missing result"
  local height
  height="$(echo "$head" | python3 - <<'PY' || true
import sys, json
try:
  o=json.load(sys.stdin)
  print(o.get("result",{}).get("height","?"))
except Exception:
  print("?")
PY
)"
  log "chain.getHead OK (height=$height)"
}

# -------- Optional faucet drip --------
maybe_faucet() {
  local address="$1"
  [[ -z "$FAUCET_URL" ]] && return 0
  log "Requesting faucet drip for $address"
  local hdrs=()
  if [[ -n "$FAUCET_API_KEY" ]]; then
    hdrs+=( -H "Authorization: Bearer $FAUCET_API_KEY" )
  fi
  curl -sS -X POST "${hdrs[@]}" \
    -H 'content-type: application/json' \
    --data "{\"address\":\"$address\",\"chainId\":$CHAIN_ID}" \
    "$FAUCET_URL" | sed -e 's/^/[faucet] /' >&2 || log "faucet call failed (continuing)"
}

# -------- Python helper: sign & send tiny transfer via omni_sdk --------
do_basic_tx_py() {
python3 - <<'PY'
import os, sys, time, json, random, string
RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = int(os.environ.get("CHAIN_ID","1337"))
SMOKE_AMOUNT = int(os.environ.get("SMOKE_AMOUNT","1"))
MNEMONIC = os.environ.get("WALLET_MNEMONIC")
DEST_ADDRESS = os.environ.get("DEST_ADDRESS")
TIMEOUT_SECS = int(os.environ.get("TIMEOUT_SECS","60"))

def log(*a): print("[py-smoke]", *a, file=sys.stderr, flush=True)

# Try to import sdk; optionally install from local path if provided.
def ensure_sdk():
  try:
    import omni_sdk  # noqa: F401
    return
  except Exception:
    pass
  p = os.environ.get("OMNI_SDK_PY_PATH","")
  if p and os.path.exists(p):
    log("Installing omni_sdk from", p)
    os.system(f'python3 -m pip install -q -e "{p}"')  # best-effort
  else:
    # Try repo-relative path if present
    repo_rel = os.path.join("sdk","python")
    if os.path.exists(repo_rel):
      log("Installing omni_sdk from repo path", repo_rel)
      os.system(f'python3 -m pip install -q -e "{repo_rel}"')
  import importlib
  importlib.invalidate_caches()

ensure_sdk()

try:
  from omni_sdk.config import Config
  from omni_sdk.wallet.mnemonic import create_mnemonic
  from omni_sdk.wallet.signer import Dilithium3Signer
  from omni_sdk.address import from_public_key as addr_from_pub
  from omni_sdk.tx.build import build_transfer
  from omni_sdk.tx.send import send_and_await_receipt
  from omni_sdk.rpc.http import HttpClient
except Exception as e:
  log("Failed to import omni_sdk:", e)
  sys.exit(2)

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)
rpc = HttpClient(RPC_URL)

# Prepare signer
if not MNEMONIC:
  MNEMONIC = create_mnemonic()
  log("Generated throwaway mnemonic for smoke test.")

signer = Dilithium3Signer.from_mnemonic(MNEMONIC)
pub = signer.public_key_bytes()
sender = addr_from_pub(pub)
if not DEST_ADDRESS:
  DEST_ADDRESS = sender

log("Sender:", sender)
log("Destination:", DEST_ADDRESS)

# Optional: attempt faucet before sending (handled in bash; here we just pause a bit)
time.sleep(1.0)

# Build tiny transfer
tx = build_transfer(
  from_addr=sender,
  to_addr=DEST_ADDRESS,
  amount=SMOKE_AMOUNT,
  nonce=None,           # let RPC/service infer or we fetch below
  gas_price=None,       # optional: could call estimator
  tip=None,
)

# Submit + wait
try:
  tx_hash, receipt = send_and_await_receipt(cfg, signer, tx, timeout_secs=TIMEOUT_SECS)
except Exception as e:
  log("Send or await receipt failed:", e)
  sys.exit(3)

out = {
  "sender": sender,
  "to": DEST_ADDRESS,
  "amount": SMOKE_AMOUNT,
  "txHash": tx_hash,
  "receipt": receipt,
}
print(json.dumps(out, indent=2))
PY
}

# -------- Main --------
log "OS=$OS  RPC_URL=$RPC_URL  CHAIN_ID=$CHAIN_ID"
launch_wallet
check_rpc

if [[ "$RPC_CHECK_ONLY" == "1" ]]; then
  log "RPC_CHECK_ONLY=1 → skipping transaction. Smoke OK."
  exit 0
fi

# If we are going to send, and no mnemonic provided, we'll let the Python helper create one.
# Try a faucet drip if FAUCET_URL is set — but only after we know the derived address.
# We don't know it yet; ask the Python to print the address first?
log "Preparing to send tiny test tx (amount=$SMOKE_AMOUNT)…"

# Run a small pre-phase to derive address & maybe faucet (reuse python but dry-run build)
DERIVED_JSON="$(WALLET_MNEMONIC="${WALLET_MNEMONIC:-}" DEST_ADDRESS="$DEST_ADDRESS" \
  python3 - <<'PY'
import os, sys, json
try:
  from omni_sdk.wallet.mnemonic import create_mnemonic
  from omni_sdk.wallet.signer import Dilithium3Signer
  from omni_sdk.address import from_public_key as addr_from_pub
except Exception:
  # Silent if SDK isn't present yet; the next phase will try to install it.
  print(json.dumps({"mnemonic": os.environ.get("WALLET_MNEMONIC",""), "sender":"", "need_sdk": True}))
  sys.exit(0)

mn = os.environ.get("WALLET_MNEMONIC")
if not mn:
  mn = create_mnemonic()
signer = Dilithium3Signer.from_mnemonic(mn)
sender = addr_from_pub(signer.public_key_bytes())
print(json.dumps({"mnemonic": mn, "sender": sender, "need_sdk": False}))
PY
)"

MNEMONIC_FROM_PY="$(echo "$DERIVED_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["mnemonic"])' 2>/dev/null || true)"
SENDER_ADDR="$(echo "$DERIVED_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["sender"])' 2>/dev/null || true)"

if [[ -z "${WALLET_MNEMONIC:-}" && -n "$MNEMONIC_FROM_PY" ]]; then
  export WALLET_MNEMONIC="$MNEMONIC_FROM_PY"
fi

if [[ -n "$SENDER_ADDR" && -n "$FAUCET_URL" ]]; then
  maybe_faucet "$SENDER_ADDR"
  # Small wait to allow funding to land on chains with instant finality
  sleep 2
fi

# Now actually send
TX_OUT="$(do_basic_tx_py || true)"
if [[ -z "$TX_OUT" ]]; then
  die "Failed to send or await receipt (no output from helper)."
fi

echo "$TX_OUT" | sed -e 's/^/[tx] /' >&2

# Quick success check
echo "$TX_OUT" | grep -q '"status": *"SUCCESS"' || {
  log "Receipt does not show SUCCESS. Output above."
  exit 4
}

log "Smoke test succeeded."
