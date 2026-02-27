#!/usr/bin/env bash
# ops/setup_studio.sh - Fully automated Animica Studio setup for current user.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NETWORK="mainnet"
RPC_URL="http://127.0.0.1:8545/rpc"
MAX_BYTES="$((10 * 1024 * 1024 * 1024))"
INSTALL_TORCH=0
DEV_REMOTE_PUT=0
NO_LAUNCH=0
ALLOW_HOST_DATA_MKDIR=0

log()  { printf '\033[1;34m[setup_studio]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

usage() {
  cat <<USAGE
Usage: ./ops/setup_studio.sh [options]

Options:
  --network mainnet|testnet
  --rpc-url URL
  --max-bytes N
  --install-torch
  --dev-remote-put      (allowed only for localhost RPC)
  --no-launch
  --allow-host-data-mkdir  (explicitly allow creating /data on host)
  -h, --help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --network)
      NETWORK="${2:-}"; shift 2 ;;
    --rpc-url)
      RPC_URL="${2:-}"; shift 2 ;;
    --max-bytes)
      MAX_BYTES="${2:-}"; shift 2 ;;
    --install-torch)
      INSTALL_TORCH=1; shift ;;
    --dev-remote-put)
      DEV_REMOTE_PUT=1; shift ;;
    --no-launch)
      NO_LAUNCH=1; shift ;;
    --allow-host-data-mkdir)
      ALLOW_HOST_DATA_MKDIR=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      err "Unknown argument: $1"
      usage
      exit 1 ;;
  esac
done

if [[ "$NETWORK" != "mainnet" && "$NETWORK" != "testnet" ]]; then
  err "--network must be mainnet or testnet"
  exit 1
fi
if ! [[ "$MAX_BYTES" =~ ^[0-9]+$ ]]; then
  err "--max-bytes must be an integer"
  exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
  REAL_USER="${SUDO_USER:-root}"
else
  REAL_USER="$(id -un)"
fi
REAL_HOME="$(eval echo "~${REAL_USER}")"

if [[ "${EUID}" -eq 0 ]]; then
  printf '\n\033[1;33m[warn]\033[0m Running as root. Will configure Studio for %s at %s. Continue? [y/N] ' "$REAL_USER" "$REAL_HOME"
  read -r ans
  if [[ ! "$ans" =~ ^[Yy]$ ]]; then
    err "Aborted by user."
    exit 1
  fi
fi

if [[ "$DEV_REMOTE_PUT" -eq 1 ]]; then
  if [[ ! "$RPC_URL" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?/rpc/?$ ]]; then
    err "--dev-remote-put is only allowed with localhost RPC URLs"
    exit 1
  fi
fi

HOST_STUDIO_BASE="$REAL_HOME/.local/share/animica-studio"
HOST_ANIMICA_BASE="$REAL_HOME/.animica"
HOST_CHAIN="$HOST_ANIMICA_BASE/chain-1"
HOST_DA_DIR="$HOST_CHAIN/da"
HOST_DA_INGEST="$HOST_CHAIN/da_ingest"
HOST_DA_INGEST_PENDING="$HOST_DA_INGEST/pending"
HOST_DA_CONTRIB="$HOST_ANIMICA_BASE/da_contrib"
CONFIG_PATH="$HOST_STUDIO_BASE/config.json"

ensure_dir_owned() {
  local target="$1"
  if [[ "${EUID}" -eq 0 ]]; then
    mkdir -p "$target"
    chown "$REAL_USER":"$REAL_USER" "$target"
    chmod u+rwx "$target"
  else
    mkdir -p "$target"
    chmod u+rwx "$target" || true
  fi
}

run_as_real_user() {
  if [[ "${EUID}" -eq 0 && "$REAL_USER" != "root" ]]; then
    sudo -u "$REAL_USER" -E "$@"
  else
    "$@"
  fi
}

log "Preparing host directories under $REAL_HOME"
for d in \
  "$HOST_STUDIO_BASE/logs" \
  "$HOST_STUDIO_BASE/checkpoints" \
  "$HOST_STUDIO_BASE/datasets" \
  "$HOST_STUDIO_BASE/ena_models" \
  "$HOST_STUDIO_BASE/node" \
  "$HOST_STUDIO_BASE/templates" \
  "$HOST_CHAIN" \
  "$HOST_DA_DIR" \
  "$HOST_DA_INGEST_PENDING" \
  "$HOST_DA_CONTRIB"
do
  ensure_dir_owned "$d"
done
ok "Directories created and writable for $REAL_USER"

if [[ "$ALLOW_HOST_DATA_MKDIR" -eq 1 ]]; then
  warn "--allow-host-data-mkdir requested; creating /data/chain-1/da and /data/chain-1/da_ingest/pending"
  mkdir -p /data/chain-1/da /data/chain-1/da_ingest/pending
fi

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  err "Python 3.11+ is required"
  exit 1
fi
ok "Using Python: $($PYTHON_BIN --version 2>&1)"

VENV_DIR="$REPO_ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating virtualenv in $VENV_DIR"
  run_as_real_user "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "Installing Studio dependencies"
run_as_real_user "$VENV_DIR/bin/python" -m pip install --upgrade pip
log "Cleaning up broken pip distributions"
run_as_real_user "$VENV_DIR/bin/python" - <<'PY'
import glob
import os
import shutil
import site

for site_path in site.getsitepackages():
    for broken_dist in glob.glob(os.path.join(site_path, "~nimica*")):
        print(f"[setup_studio] Removing broken dist: {broken_dist}")
        shutil.rmtree(broken_dist, ignore_errors=True)
PY
if [[ -f "$REPO_ROOT/apps/animica_studio/requirements.txt" ]]; then
  run_as_real_user "$VENV_DIR/bin/pip" install -r "$REPO_ROOT/apps/animica_studio/requirements.txt"
else
  run_as_real_user "$VENV_DIR/bin/pip" install -e "$REPO_ROOT/apps/animica_studio"
fi
run_as_real_user "$VENV_DIR/bin/pip" install requests PySide6 cbor2 pillow psutil
if [[ "$INSTALL_TORCH" -eq 1 ]]; then
  log "Installing torch CPU wheel"
  run_as_real_user "$VENV_DIR/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu
else
  warn "Skipping torch install (use --install-torch to enable)"
fi

CLI_PATH="$REPO_ROOT/.venv/bin/animica"

log "Patching Studio config at $CONFIG_PATH"
CONFIG_PARENT="$(dirname "$CONFIG_PATH")"
ensure_dir_owned "$CONFIG_PARENT"
CONFIG_PATH="$CONFIG_PATH" REAL_HOME="$REAL_HOME" CLI_PATH="$CLI_PATH" run_as_real_user "$VENV_DIR/bin/python" - <<'PY'
import json
import os
import shutil
from pathlib import Path

cfg_path = Path(os.environ["CONFIG_PATH"])
real_home = os.environ["REAL_HOME"]
cli_path = os.environ["CLI_PATH"]

raw = None
if cfg_path.exists():
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    backup_path = cfg_path.with_suffix(".json.bak")
    shutil.copy2(cfg_path, backup_path)
else:
    raw = {}

cfg = {}
if isinstance(raw, dict):
    cfg = raw
elif isinstance(raw, list):
    for item in reversed(raw):
        if isinstance(item, dict):
            cfg = item
            cfg["_migrated_from_list"] = True
            break

da = cfg.setdefault("da", {})
profiles = cfg.setdefault("profiles", {})
if isinstance(profiles, list):
    converted_profiles = {}
    for index, profile_item in enumerate(profiles):
        if isinstance(profile_item, dict):
            profile_name = profile_item.get("name") or profile_item.get("id") or f"profile{index}"
            converted_profiles[profile_name] = profile_item
    profiles = converted_profiles
    cfg["profiles"] = profiles

default_profile = profiles.setdefault("default", {})
default_profile.setdefault("rpc_url", "http://127.0.0.1:8545/rpc")

da.setdefault("node_dir", "/data/chain-1/da")
da.setdefault("host_dir", f"{real_home}/.animica/chain-1/da")
da.setdefault("ingest_host_dir", f"{real_home}/.animica/chain-1/da_ingest")
da.setdefault("studio_dir", f"{real_home}/.animica/da_contrib")
da.setdefault("auto_start", True)
da["da_namespace"] = int(da.get("da_namespace", 0) or 0)

cfg["da_namespace"] = int(cfg.get("da_namespace", 0) or 0)

for k in ("host_dir", "ingest_host_dir", "studio_dir"):
    v = str(da.get(k, ""))
    if v.startswith("/data"):
        if k == "host_dir":
            da[k] = f"{real_home}/.animica/chain-1/da"
        elif k == "ingest_host_dir":
            da[k] = f"{real_home}/.animica/chain-1/da_ingest"
        elif k == "studio_dir":
            da[k] = f"{real_home}/.animica/da_contrib"

if Path(cli_path).exists():
    cfg["cli_path"] = cli_path

cfg_path.parent.mkdir(parents=True, exist_ok=True)
cfg_path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

if [[ "${EUID}" -eq 0 ]]; then
  chown "$REAL_USER":"$REAL_USER" "$CONFIG_PATH"
fi
ok "Studio config normalized"

COMPOSE_FILE="$REPO_ROOT/ops/docker/docker-compose.mainnet.yml"
if [[ -f "$COMPOSE_FILE" ]]; then
  log "Docker compose detected: $COMPOSE_FILE"
  UID_REAL="$(id -u "$REAL_USER")"
  GID_REAL="$(id -g "$REAL_USER")"
  DOCKER_ENV="$REPO_ROOT/ops/docker/.env"
  {
    echo "UID=$UID_REAL"
    echo "GID=$GID_REAL"
    echo "ANIMICA_UID=$UID_REAL"
    echo "ANIMICA_GID=$GID_REAL"
    echo "ANIMICA_DATA_MOUNT_SOURCE=$REAL_HOME/.animica"
    echo "HOME=$REAL_HOME"
  } > "$DOCKER_ENV"
  if [[ "${EUID}" -eq 0 ]]; then
    chown "$REAL_USER":"$REAL_USER" "$DOCKER_ENV"
  fi
  ok "Wrote $DOCKER_ENV with REAL_USER UID/GID and mount source"

  if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}}' | rg -x 'animica-mainnet-node|node' >/dev/null 2>&1; then
      NODE_CONTAINER="$(docker ps --format '{{.Names}}' | rg -x 'animica-mainnet-node|node' | head -n1)"
      if docker exec "$NODE_CONTAINER" sh -lc 'touch /data/.write_test && rm -f /data/.write_test'; then
        ok "Container write test succeeded (/data writable)"
      else
        warn "Container cannot write to /data. Fix compose with:"
        cat <<SNIP
services:
  node:
    user: "\${UID}:\${GID}"
    volumes:
      - "$REAL_HOME/.animica:/data"
SNIP
      fi
    else
      warn "No running node container named animica-mainnet-node or node; skipping container write test"
    fi
  else
    warn "Docker not found; skipping dockerized node checks"
  fi
fi

rpc_call() {
  local method="$1"
  local params_json="${2:-{}}"
  curl -fsS "$RPC_URL" \
    -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$method\",\"params\":$params_json}" 
}

log "Checking RPC readiness at $RPC_URL"
RPC_OK=0
RPC_DISCOVER_JSON=""
for _ in {1..30}; do
  if RPC_DISCOVER_JSON="$(rpc_call "rpc.discover" "{}" 2>/tmp/animica_rpc_discover.err)"; then
    printf '%s\n' "$RPC_DISCOVER_JSON" >/tmp/animica_rpc_discover.json
    RPC_OK=1
    break
  fi
  sleep 2
done
if [[ "$RPC_OK" -ne 1 ]]; then
  err "RPC is not reachable at $RPC_URL"
  cat <<EOF_BLOCK
Action required:
  1) Start node: animica node up
  2) Verify RPC: curl -s $RPC_URL
  3) Re-run: ./ops/setup_studio.sh --rpc-url $RPC_URL
EOF_BLOCK
  exit 1
fi
ok "RPC reachable"

DISCOVER_CHECK="$(printf '%s' "$RPC_DISCOVER_JSON" | "$VENV_DIR/bin/python" - <<'PY'
import json,sys
raw=sys.stdin.read().strip()
methods=set()
try:
    obj=json.loads(raw)
except Exception:
    print('warn|rpc.discover payload unreadable')
    raise SystemExit(0)
res=obj.get('result',{}) if isinstance(obj,dict) else {}
if isinstance(res,dict):
    caps=res.get('capabilities')
    if isinstance(caps,dict):
        methods.update(caps.keys())
        da_caps=caps.get('da')
        if isinstance(da_caps,dict):
            methods.update({f"da.{k}" for k in da_caps.keys() if isinstance(k,str)})
    for key in ('methods','available_methods'):
        values=res.get(key)
        if isinstance(values,list):
            methods.update(str(v) for v in values)
missing=[m for m in ('da.getIngestDir','da.ingestLocal','da.statPath') if m not in methods]
if missing:
    print('warn|' + ', '.join(missing))
else:
    print('ok|all ingest methods discovered')
PY
)"
if [[ "${DISCOVER_CHECK%%|*}" == "warn" ]]; then
  warn "Missing or unclear ingest RPC methods from rpc.discover: ${DISCOVER_CHECK#*|}"
else
  ok "rpc.discover reports ingest methods"
fi

ALLOW_REMOTE_PUT=false
if [[ "$DEV_REMOTE_PUT" -eq 1 ]]; then
  ALLOW_REMOTE_PUT=true
fi

DA_PARAMS="{\"enabled\":true,\"dir\":\"/data/chain-1/da\",\"max_bytes\":$MAX_BYTES,\"allow_remote_get\":true,\"allow_remote_put\":$ALLOW_REMOTE_PUT}"
DA_CONFIG_OK=0
for method in da.configure da_configure; do
  if rpc_call "$method" "$DA_PARAMS" >/tmp/animica_da_configure.json 2>/tmp/animica_da_configure.err; then
    DA_CONFIG_OK=1
    ok "Configured DA using $method"
    break
  fi
done
if [[ "$DA_CONFIG_OK" -ne 1 ]]; then
  err "Unable to configure DA via RPC"
  cat /tmp/animica_da_configure.err >&2 || true
  exit 1
fi

DA_STATUS_JSON=""
for method in da.getStatus da.status da_getStatus da_status; do
  if out="$(rpc_call "$method" "{}" 2>/dev/null)"; then
    DA_STATUS_JSON="$out"
    break
  fi
done
if [[ -z "$DA_STATUS_JSON" ]]; then
  warn "Could not fetch DA status (methods unavailable)"
else
  DA_VERIFY_MSG="$(printf '%s' "$DA_STATUS_JSON" | "$VENV_DIR/bin/python" - <<'PY'
import json,sys
obj=json.load(sys.stdin)
res=obj.get('result') if isinstance(obj,dict) else {}
enabled=bool((res or {}).get('enabled', False))
writable=bool((res or {}).get('writable', False))
print(f"enabled={enabled} writable={writable}")
if not (enabled and writable):
    raise SystemExit(2)
PY
)" || {
    err "DA status is not healthy (need enabled=true and writable=true)"
    printf '%s\n' "$DA_STATUS_JSON"
    exit 1
  }
  ok "DA status: $DA_VERIFY_MSG"
fi

INGEST_BLOCKED=0
if [[ "$ALLOW_REMOTE_PUT" == false ]]; then
  log "Verifying local ingest mapping"
  PROBE_PATH="$HOST_DA_INGEST_PENDING/.studio_probe"
  run_as_real_user touch "$PROBE_PATH"
  STAT_PARAMS="{\"path\":\"/data/chain-1/da_ingest/pending/.studio_probe\"}"
  if ! rpc_call "da.statPath" "$STAT_PARAMS" >/tmp/animica_da_statpath.json 2>/tmp/animica_da_statpath.err; then
    INGEST_BLOCKED=1
  fi
  if [[ "$INGEST_BLOCKED" -eq 1 ]]; then
    err "Node ingest mapping check failed."
    cat <<EOF_BLOCK
Exact fix:
  Ensure docker compose bind mount includes:
    - "$REAL_HOME/.animica:/data"
  Then restart node and re-run setup.
EOF_BLOCK
    exit 1
  fi
  ok "Ingest mapping verified via da.statPath"
fi

log "Running doctor"
DOCTOR_JSON="$(run_as_real_user "$VENV_DIR/bin/animica-studio" doctor --rpc-url "$RPC_URL" --json || true)"
printf '%s\n' "$DOCTOR_JSON" > /tmp/animica_studio_doctor.json
SUMMARY="$(printf '%s' "$DOCTOR_JSON" | "$VENV_DIR/bin/python" - <<'PY'
import json,sys
raw=sys.stdin.read().strip()
if not raw:
    print('blocked|doctor produced no output')
    raise SystemExit(0)
try:
    doc=json.loads(raw)
except Exception:
    print('degraded|doctor output not JSON')
    raise SystemExit(0)
issues=[]
for key in ('env','rpc','da','studio','ena','pipeline'):
    sec=doc.get(key)
    if isinstance(sec,dict) and sec.get('ok') is False:
        issues.append(f"{key}: {sec.get('detail') or sec.get('error') or 'not ok'}")
if issues:
    print('degraded|'+'; '.join(issues))
else:
    print('ready|doctor checks passed')
PY
)"
STATE="${SUMMARY%%|*}"
DETAIL="${SUMMARY#*|}"
case "$STATE" in
  ready) ok "✅ Ready - $DETAIL" ;;
  degraded) warn "⚠️ Degraded - $DETAIL" ;;
  *) err "❌ Blocked - $DETAIL" ;;
esac

if [[ "$NO_LAUNCH" -eq 1 ]]; then
  ok "Setup complete (launch skipped by --no-launch)."
  exit 0
fi

log "Launching Studio as $REAL_USER"
if [[ "${EUID}" -eq 0 && "$REAL_USER" != "root" ]]; then
  exec sudo -u "$REAL_USER" -E "$VENV_DIR/bin/animica-studio"
else
  exec "$VENV_DIR/bin/animica-studio"
fi
