#!/usr/bin/env bash
set -euo pipefail

log()  { echo "[setup] $(date -u +%FT%TZ) $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
die()  { echo "[setup][ERROR] $*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/.venv"

# ----------------------------
# Flags / env
# ----------------------------
FRESH_INSTALL=false
ENABLE_P2P=true
P2P_PORT="${P2P_PORT:-30333}"
P2P_BIND_IP="${P2P_BIND_IP:-0.0.0.0}"
P2P_ADVERTISE_IP="${P2P_ADVERTISE_IP:-}"   # if empty we auto-detect
P2P_LOG_DIR="${P2P_LOG_DIR:-$ROOT/logs}"
P2P_PID_FILE="${P2P_PID_FILE:-$P2P_LOG_DIR/animica-p2p.pid}"

if [ "${FRESH:-}" = "1" ]; then
  FRESH_INSTALL=true
fi
if [ "${DISABLE_P2P:-}" = "1" ]; then
  ENABLE_P2P=false
fi

for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH_INSTALL=true ;;
    --no-p2p) ENABLE_P2P=false ;;
    --p2p-port=*) P2P_PORT="${arg#*=}" ;;
    --p2p-bind=*) P2P_BIND_IP="${arg#*=}" ;;
    --p2p-advertise=*) P2P_ADVERTISE_IP="${arg#*=}" ;;
    --help|-h)
      cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --fresh              Remove existing .venv and perform a clean installation
  --no-p2p             Do not start P2P listener/broadcaster after install
  --p2p-port=PORT      P2P port (default: 30333)
  --p2p-bind=IP        Bind IP for listener (default: 0.0.0.0)
  --p2p-advertise=IP   Advertise IP for peers (default: auto-detect)

Environment Variables:
  FRESH=1              Same as --fresh flag
  DISABLE_P2P=1        Same as --no-p2p
  P2P_PORT             Same as --p2p-port
  P2P_BIND_IP          Same as --p2p-bind
  P2P_ADVERTISE_IP     Same as --p2p-advertise
  PIP_INDEX_URL        If set, use this as the primary pip index
  PIP_EXTRA_INDEX_URL  If set, use this as an additional pip index

Examples:
  # Regular idempotent setup
  ./setup.sh

  # Fresh installation (removes existing .venv)
  ./setup.sh --fresh
  # OR
  FRESH=1 ./setup.sh

  # Disable P2P autostart
  ./setup.sh --no-p2p

  # Explicit P2P advertise IP/port
  ./setup.sh --p2p-advertise=144.126.133.21 --p2p-port=30333
EOF
      exit 0
      ;;
  esac
done

install_system_deps() {
  if ! have apt-get; then
    warn "apt-get not found; skipping system deps install."
    return
  fi

  log "Installing minimal system dependencies via apt-get"

  local NEEDED_PKGS=()
  for pkg in ca-certificates curl git python3 python3-venv python3-pip; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      NEEDED_PKGS+=("$pkg")
    fi
  done

  if [ "${#NEEDED_PKGS[@]}" -gt 0 ]; then
    log "Installing packages: ${NEEDED_PKGS[*]}"

    if [ "$(id -u)" -ne 0 ]; then
      if ! have sudo; then
        warn "sudo not available and not running as root. Cannot install system packages."
        warn "Please install these packages manually: ${NEEDED_PKGS[*]}"
        return
      fi
      sudo apt-get update -y
      sudo apt-get install -y --no-install-recommends "${NEEDED_PKGS[@]}"
    else
      apt-get update -y
      apt-get install -y --no-install-recommends "${NEEDED_PKGS[@]}"
    fi
  else
    log "All required system packages already installed"
  fi
}

ensure_venv() {
  if [ "$FRESH_INSTALL" = true ] && [ -d "$VENV_DIR" ]; then
    log "FRESH mode: removing existing virtual environment at $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi

  if [ -d "$VENV_DIR" ]; then
    log "Virtual environment already exists at $VENV_DIR (reusing)"
  else
    log "Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  log "Upgrading pip, setuptools, and wheel"
  python -m pip install -U pip setuptools wheel --quiet
}

install_local_dependencies() {
  log "Installing local SDK dependencies (omni-sdk)"

  if [ -d "$ROOT/sdk/python" ] && [ -f "$ROOT/sdk/python/pyproject.toml" ]; then
    log "Installing omni-sdk from $ROOT/sdk/python"
    if ! python -m pip install -e "$ROOT/sdk/python" --quiet; then
      die "Failed to install omni-sdk from local path. Check $ROOT/sdk/python/pyproject.toml"
    fi
  else
    warn "omni-sdk package not found at $ROOT/sdk/python - installation may fail"
    warn "To use a custom pip index, set: PIP_EXTRA_INDEX_URL=https://your-index/simple"
  fi
}

install_animica() {
  log "Installing Animica package in editable mode"

  if [ -d "$ROOT/python" ] && [ -f "$ROOT/python/pyproject.toml" ]; then
    if ! python -m pip install -e "$ROOT/python[dev]"; then
      die "Failed to install animica[dev]. Ensure omni-sdk is available via local path or PIP_EXTRA_INDEX_URL"
    fi
  elif [ -f "$ROOT/pyproject.toml" ]; then
    if ! python -m pip install -e "$ROOT[dev]"; then
      die "Failed to install animica[dev] from root pyproject.toml"
    fi
  else
    die "Could not find pyproject.toml (checked ./python and repo root)"
  fi

  if [ -d "$ROOT/pq" ] && [ -f "$ROOT/pq/pyproject.toml" ]; then
    log "Installing pq package (pure-Python PQ backend)"
    python -m pip install -e "$ROOT/pq"
  fi
}

verify_installation() {
  log "Verifying installation"

  if ! python -m animica --help >/dev/null 2>&1; then
    die "Installation verification failed: 'python -m animica --help' failed"
  fi

  if [ ! -x "$VENV_DIR/bin/animica" ]; then
    warn "Console script not found at $VENV_DIR/bin/animica"
  fi

  log "✓ Installation verified successfully"
}

# ----------------------------
# P2P autostart helpers
# ----------------------------
detect_advertise_ip() {
  # Prefer explicit env/flag
  if [ -n "${P2P_ADVERTISE_IP:-}" ]; then
    echo "$P2P_ADVERTISE_IP"
    return 0
  fi

  # Try public IP services (non-fatal)
  local ip=""
  if have curl; then
    ip="$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)"
    if [ -z "$ip" ]; then
      ip="$(curl -fsS --max-time 3 https://ifconfig.me 2>/dev/null || true)"
    fi
  fi
  if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "$ip"
    return 0
  fi

  # Fallback: first global IPv4 from the machine
  if have ip; then
    ip="$(ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -n 1 || true)"
  fi
  if [ -z "$ip" ] && have hostname; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi

  if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "$ip"
    return 0
  fi

  # Last resort: advertise bind ip if it's not 0.0.0.0
  if [ "$P2P_BIND_IP" != "0.0.0.0" ]; then
    echo "$P2P_BIND_IP"
    return 0
  fi

  echo ""
}

help_text_for() {
  # returns help text (best-effort) for a command; never fails the script
  set +e
  local out=""
  out="$("$@" --help 2>/dev/null)"
  local rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo ""
    return 0
  fi
  echo "$out"
}

supports_flag() {
  local help_text="$1"
  local flag="$2"
  echo "$help_text" | grep -q -- "$flag"
}

p2p_start_command() {
  # Decide how to start the node/p2p (best-effort; no hard dependency on exact subcommand names)
  # Prefer: animica node run
  if "$VENV_DIR/bin/animica" node --help >/dev/null 2>&1; then
    if "$VENV_DIR/bin/animica" node run --help >/dev/null 2>&1; then
      echo "$VENV_DIR/bin/animica node run"
      return 0
    fi
    if "$VENV_DIR/bin/animica" node start --help >/dev/null 2>&1; then
      echo "$VENV_DIR/bin/animica node start"
      return 0
    fi
  fi

  # Fallback: module invocation
  if python -m animica --help >/dev/null 2>&1; then
    # If CLI exists but node subcommands are different, we still try "node run" via module.
    echo "python -m animica node run"
    return 0
  fi

  echo ""
}

start_p2p() {
  if [ "$ENABLE_P2P" != true ]; then
    log "P2P autostart disabled (--no-p2p / DISABLE_P2P=1)."
    return 0
  fi

  mkdir -p "$P2P_LOG_DIR"

  # If there is a stale pid file, try to clean it up
  if [ -f "$P2P_PID_FILE" ]; then
    local oldpid
    oldpid="$(cat "$P2P_PID_FILE" 2>/dev/null || true)"
    if [ -n "$oldpid" ] && kill -0 "$oldpid" >/dev/null 2>&1; then
      log "P2P appears already running (pid $oldpid). Skipping autostart."
      return 0
    fi
    rm -f "$P2P_PID_FILE" || true
  fi

  local advertise_ip
  advertise_ip="$(detect_advertise_ip)"
  if [ -z "$advertise_ip" ]; then
    warn "Could not auto-detect an advertise IP. P2P will still bind on ${P2P_BIND_IP}:${P2P_PORT}."
    warn "Set P2P_ADVERTISE_IP=<your_public_ip> (or pass --p2p-advertise=IP) to broadcast correctly."
  fi

  local cmd
  cmd="$(p2p_start_command)"
  if [ -z "$cmd" ]; then
    warn "Could not determine how to start the Animica node/P2P from CLI. Skipping P2P autostart."
    return 0
  fi

  # Build args by inspecting help text (so we don't hardcode flag names that might differ)
  local help
  help="$(help_text_for $cmd)"
  local args=()

  # Bind/listen flags (try a few common names)
  if [ -n "$help" ]; then
    if supports_flag "$help" "--p2p-bind"; then
      args+=(--p2p-bind "${P2P_BIND_IP}:${P2P_PORT}")
    elif supports_flag "$help" "--p2p-listen"; then
      args+=(--p2p-listen "${P2P_BIND_IP}:${P2P_PORT}")
    elif supports_flag "$help" "--p2p-host" && supports_flag "$help" "--p2p-port"; then
      args+=(--p2p-host "$P2P_BIND_IP" --p2p-port "$P2P_PORT")
    elif supports_flag "$help" "--host" && supports_flag "$help" "--port"; then
      # last-resort generic host/port (may be RPC; only applied if node run uses them for P2P too)
      args+=(--host "$P2P_BIND_IP" --port "$P2P_PORT")
    fi

    # Advertise/broadcast flags
    if [ -n "$advertise_ip" ]; then
      if supports_flag "$help" "--p2p-advertise"; then
        args+=(--p2p-advertise "${advertise_ip}:${P2P_PORT}")
      elif supports_flag "$help" "--advertise"; then
        args+=(--advertise "${advertise_ip}:${P2P_PORT}")
      elif supports_flag "$help" "--external-address"; then
        args+=(--external-address "${advertise_ip}:${P2P_PORT}")
      fi
    fi
  fi

  log "Starting P2P listener/broadcaster (bind ${P2P_BIND_IP}:${P2P_PORT}${advertise_ip:+, advertise ${advertise_ip}:${P2P_PORT}})"
  log "Command: $cmd ${args[*]}"

  # Start in background so setup.sh can finish
  # shellcheck disable=SC2091
  nohup bash -lc "
    set -euo pipefail
    cd \"$ROOT\"
    source \"$VENV_DIR/bin/activate\"
    exec $cmd ${args[*]}
  " >"$P2P_LOG_DIR/animica-p2p.log" 2>&1 &

  echo $! >"$P2P_PID_FILE"
  log "P2P started (pid $(cat "$P2P_PID_FILE")). Logs: $P2P_LOG_DIR/animica-p2p.log"
}

print_usage() {
  cat <<EOF

========================================================================
  Animica Setup Complete
========================================================================

To use Animica:

  1. Activate the virtual environment:
     $ source .venv/bin/activate

  2. Run the animica CLI:
     $ animica --help

  3. Test PQ functionality:
     $ python -c "from animica.pq import kem_keygen, kem_encaps, kem_decaps; ek,dk=kem_keygen(); k,ct=kem_encaps(ek); assert kem_decaps(dk,ct)==k; print('KEM ok')"
     $ python -c "from animica.pq import sig_keygen, sig_sign, sig_verify; pk,sk=sig_keygen(); m=b'hi'; s=sig_sign(sk,m); assert sig_verify(pk,m,s); print('SIG ok')"

  4. Run tests:
     $ pytest -q python/animica/pq/tests

P2P autostart:
  - If enabled, setup.sh attempted to start a P2P listener and broadcast/advertise address.
  - P2P PID file: $P2P_PID_FILE
  - P2P log file:  $P2P_LOG_DIR/animica-p2p.log
  - To disable:    ./setup.sh --no-p2p  (or DISABLE_P2P=1)

Post-quantum cryptography is enabled by default using pure-Python
implementations (no liboqs/oqs dependencies required).

To disable PQ (for testing fallback behavior):
  $ export ANIMICA_PQ_MODE=disabled

For more information, see:
  - docs/pq_pure_python.md
  - python/animica/pq/README.md (if exists)

========================================================================

EOF
}

main() {
  if [ "$FRESH_INSTALL" = true ]; then
    log "Animica setup starting (FRESH mode - clean install)"
  else
    log "Animica setup starting (Ubuntu 24.04 compatible, idempotent)"
  fi

  install_system_deps
  ensure_venv
  install_local_dependencies
  install_animica
  verify_installation

  # Start P2P (best-effort; never fails the install)
  set +e
  start_p2p
  set -e

  print_usage
  log "Setup complete!"
}

main "$@"
