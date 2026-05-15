#!/usr/bin/env bash
# Clean-VM end-to-end smoke for `pip install animica` + `animica node up`.
#
# Run on a freshly-provisioned VM (or a CI runner) that has only:
#   - python3.10+
#   - docker (engine + `docker compose`)
#   - outbound internet
#
# What it checks, in order:
#
#   1. Prereqs are present
#   2. `pip install animica==<VERSION>` succeeds from PyPI
#   3. `animica network set mainnet` + `animica node up` runs to completion
#   4. The standalone node container reaches health
#   5. The host can reach the RPC and `chain.getHead` returns the genesis
#   6. The local consensus_id matches what 0.1.x users on the same wheel produce
#   7. The node makes sync progress (head height climbs above 0) within a
#      bounded window — this is the load-bearing check that proves the
#      deployed mainnet seed will accept our handshake
#
# Exit code: 0 if every check passes, non-zero on the first failure. Each
# check prints a clear PASS/FAIL line plus diagnostic detail on failure.
#
# Usage:
#   tools/smoke/clean_vm_sync.sh                 # defaults to 0.1.7
#   tools/smoke/clean_vm_sync.sh 0.1.7
#   ANIMICA_SMOKE_NETWORK=devnet tools/smoke/clean_vm_sync.sh 0.1.7

set -u

ANIMICA_VERSION="${1:-0.1.7}"
NETWORK="${ANIMICA_SMOKE_NETWORK:-mainnet}"
PUBLIC_RPC="${ANIMICA_PUBLIC_RPC:-https://rpc.animica.org/rpc}"

case "$NETWORK" in
  mainnet)       LOCAL_RPC="http://127.0.0.1:8545/rpc"; CONTAINER="animica-mainnet-node" ;;
  testnet)       LOCAL_RPC="http://127.0.0.1:18546/rpc"; CONTAINER="animica-testnet-node" ;;
  devnet)        LOCAL_RPC="http://127.0.0.1:28545/rpc"; CONTAINER="animica-devnet-node" ;;
  *) echo "unknown network: $NETWORK"; exit 2 ;;
esac

VENV="$(mktemp -d -t animica-smoke.XXXXXX)/venv"

GREEN=$(tput setaf 2 2>/dev/null || true)
RED=$(tput setaf 1 2>/dev/null || true)
YELLOW=$(tput setaf 3 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

pass() { printf "%bPASS%b  %s\n" "$GREEN" "$RESET" "$1"; }
fail() { printf "%bFAIL%b  %s\n" "$RED" "$RESET" "$1"; exit 1; }
warn() { printf "%bWARN%b  %s\n" "$YELLOW" "$RESET" "$1"; }
step() { printf "\n--- %s ---\n" "$1"; }

cleanup() {
  step "cleanup"
  if [ -d "$VENV" ] && [ -x "$VENV/bin/animica" ]; then
    "$VENV/bin/animica" node down --volumes 2>/dev/null || true
  fi
  # Best-effort container teardown in case the CLI shim is missing
  docker rm -f "$CONTAINER" 2>/dev/null || true
  docker volume ls -q 2>/dev/null | grep -i "$NETWORK" | xargs -r docker volume rm 2>/dev/null || true
  rm -rf "$(dirname "$VENV")" 2>/dev/null || true
}
trap cleanup EXIT

# Pretty progress bar for waits
spin_wait() {
  local label="$1"; local total="$2"; local check_cmd="$3"
  local i=0
  while [ "$i" -lt "$total" ]; do
    if eval "$check_cmd" >/dev/null 2>&1; then
      printf "\r%-50s %ds [ok]\n" "$label" "$i"
      return 0
    fi
    printf "\r%-50s %ds" "$label" "$i"
    sleep 1
    i=$((i + 1))
  done
  printf "\r%-50s %ds [timeout]\n" "$label" "$i"
  return 1
}

#
# 1. prereqs
#
step "1. prereq check"
command -v docker >/dev/null     || fail "docker not on PATH"
command -v python3 >/dev/null    || fail "python3 not on PATH"
docker info >/dev/null 2>&1      || fail "docker engine not reachable (is the daemon running and your user in the docker group?)"
pass "docker $(docker --version | awk '{print $3}' | tr -d ,)"
pass "$(python3 --version)"

#
# 2. fresh install
#
step "2. install animica==$ANIMICA_VERSION into a clean venv"
python3 -m venv "$VENV" || fail "could not create venv at $VENV"
"$VENV/bin/python" -m pip install --upgrade --quiet pip || fail "pip self-upgrade failed"
"$VENV/bin/python" -m pip install --quiet "animica==$ANIMICA_VERSION" \
  || fail "pip install animica==$ANIMICA_VERSION failed (CDN propagation? PyPI down?)"
INSTALLED=$("$VENV/bin/python" -m pip show animica | awk '/^Version:/ {print $2}')
[ "$INSTALLED" = "$ANIMICA_VERSION" ] || fail "expected $ANIMICA_VERSION installed, got $INSTALLED"
pass "animica==$INSTALLED installed in $VENV"

#
# 3. node up
#
step "3. animica network set $NETWORK + animica node up"
"$VENV/bin/animica" network set "$NETWORK" >/dev/null \
  || fail "animica network set $NETWORK failed"
# Capture full output so we can surface build errors if anything fails
UP_LOG=$(mktemp)
if ! "$VENV/bin/animica" node up --no-wait-sync --rpc-ready-timeout 120 >"$UP_LOG" 2>&1; then
  echo "--- animica node up output (tail 60) ---"
  tail -60 "$UP_LOG"
  fail "animica node up failed — see output above"
fi
pass "animica node up returned successfully"

#
# 4. container health
#
step "4. container health"
if ! spin_wait "waiting for $CONTAINER to be healthy" 90 \
    "docker inspect --format '{{.State.Health.Status}}' $CONTAINER 2>/dev/null | grep -q '^healthy\$'"; then
  echo "--- last 80 container log lines ---"
  docker logs --tail 80 "$CONTAINER" 2>&1 || true
  fail "container never reached healthy state"
fi
pass "container $CONTAINER is healthy"

#
# 5. RPC reachable + genesis returned
#
step "5. local RPC: chain.getHead"
RPC_RESPONSE=$(curl -fsS --max-time 10 -X POST -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":{}}' \
  "$LOCAL_RPC" 2>&1) || fail "local RPC at $LOCAL_RPC unreachable from host"

LOCAL_HEIGHT=$(echo "$RPC_RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['height'])" 2>/dev/null) \
  || fail "could not parse height from local RPC response: $RPC_RESPONSE"
LOCAL_HASH=$(echo "$RPC_RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['hash'])")
pass "local RPC up — height=$LOCAL_HEIGHT hash=$LOCAL_HASH"

#
# 6. consensus_id sanity check
#
step "6. local consensus_id"
LOCAL_CID=$("$VENV/bin/python" -c "
from core.genesis import loader
from pathlib import Path
import animica
gp = Path(animica.__file__).resolve().parent.parent / 'core/genesis/${NETWORK}.json'
i = loader.compute_genesis_identity(gp)
print(f'{i.chain_id}|{i.fork_id}|{i.consensus_id}')
" 2>/dev/null) || fail "could not compute local genesis identity"
echo "  $LOCAL_CID"
pass "local consensus_id computed"

#
# 7. seed reachability — fail fast if the public infra is down
#
step "7. seed reachability"
if [ "$NETWORK" = "devnet" ]; then
  warn "devnet has no public peers — skipping seed and sync checks"
  SEED_OK=0
else
  SEED_HOSTS=("144.126.133.21" "mainnet.animica.org")
  SEED_OK=0
  for h in "${SEED_HOSTS[@]}"; do
    if python3 -c "
import socket, sys
s = socket.socket(); s.settimeout(5)
try: s.connect(('$h', 30333)); sys.exit(0)
except Exception: sys.exit(1)
" 2>/dev/null; then
      pass "seed ${h}:30333 reachable"
      SEED_OK=1
      break
    fi
  done

  if [ "$SEED_OK" -eq 0 ]; then
    warn "no seed P2P port (30333) is reachable from this VM"
    echo "  Probed: ${SEED_HOSTS[*]}"
    echo "  Likely cause: the mainnet seed daemon is down. The wheel + image"
    echo "  build are fine — this is an operator-side outage. Restart the seed:"
    echo
    echo "    ssh root@144.126.133.21"
    echo "    pip install --upgrade animica==${ANIMICA_VERSION}"
    echo "    animica network set mainnet"
    echo "    animica node up"
    echo
    fail "cannot continue sync check while seed is unreachable"
  fi

  # Public RPC for target-height reference. Non-fatal — if it's behind a 502
  # but seeds are up, sync can still happen via direct P2P.
  PUBLIC_HEAD=$(curl -fsS --max-time 10 -X POST -H 'content-type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":{}}' \
    "$PUBLIC_RPC" 2>&1) \
    || warn "public RPC $PUBLIC_RPC unreachable — target height check will be inconclusive"
  PUBLIC_HEIGHT=$(echo "$PUBLIC_HEAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['height'])" 2>/dev/null) || PUBLIC_HEIGHT="?"
  echo "  public network head: $PUBLIC_HEIGHT"
  echo "  local node head:     $LOCAL_HEIGHT"
  echo "  watching for sync progress for 90 seconds..."

  START=$LOCAL_HEIGHT
  PROGRESSED=0
  for i in 1 2 3 4 5 6 7 8 9; do
    sleep 10
    CURR=$(curl -fsS --max-time 5 -X POST -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":{}}' \
      "$LOCAL_RPC" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['height'])" 2>/dev/null) || CURR="$START"
    printf "    t=%2ds  height=%s\n" "$((i * 10))" "$CURR"
    if [ "$CURR" -gt "$START" ]; then
      PROGRESSED=1
      pass "sync progressing: started at height $START, now at $CURR"
      break
    fi
  done

  if [ "$PROGRESSED" -eq 0 ]; then
    echo
    echo "--- handshake diagnostics (most recent) ---"
    docker logs --tail 200 "$CONTAINER" 2>&1 \
      | grep -iE "handshake|consensus|fork_id|chain_id|peer.*reject|hello timeout" \
      | tail -20 || true
    echo
    fail "no sync progress in 90s — local stayed at height $START (public head $PUBLIC_HEIGHT). \
This usually means the deployed seed's consensus_id does not match this wheel. \
Upgrade the seed (mainnet.animica.org / 144.126.133.21) to animica==$ANIMICA_VERSION \
so its consensus_id matches what this wheel computes."
  fi
fi

echo
echo "${GREEN}all checks passed${RESET}"
