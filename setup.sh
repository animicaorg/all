#!/usr/bin/env bash
set -euo pipefail
set -o errtrace
IFS=$'\n\t'
trap 'echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") ERROR line ${LINENO}: ${BASH_COMMAND:-unknown}" >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

WITH_PLAYWRIGHT=false
WITH_PQ=false
SKIP_NODE=false
SKIP_PYTHON=false
SKIP_PNPM=false
CLEAN=false
WITH_DOCKER=true
DOCKER_AVAILABLE=false

log()  { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"; }
warn() { echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") WARN: $*" >&2; }
section() { echo; echo "[setup] ================================================================="; log "$*"; echo "[setup] ================================================================="; }

usage() {
  cat <<'USAGE'
Usage: setup.sh [options]

  --clean             Remove .venv before installing
  --with-playwright   Also install Playwright browsers and dependencies
  --with-pq           Install/build PQ deps (liboqs/liboqs-python) if your environment supports it
  --without-docker    Skip Docker Engine / Docker Compose installation
  --skip-node         Skip Node.js installation check
  --skip-python       Skip Python environment setup
  --skip-pnpm         Skip pnpm workspace install
  --help              Show this help message
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-playwright) WITH_PLAYWRIGHT=true ;;
    --with-pq)         WITH_PQ=true ;;
    --without-docker)  WITH_DOCKER=false ;;
    --skip-node)       SKIP_NODE=true ;;
    --skip-python)     SKIP_PYTHON=true ;;
    --skip-pnpm)       SKIP_PNPM=true ;;
    --clean)           CLEAN=true ;;
    --help|-h)         usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

require_root() {
  if [[ ${EUID:-0} -ne 0 ]]; then
    echo "[setup] This script must be run as root (try: sudo ./setup.sh)." >&2
    exit 1
  fi
}

require_ubuntu() {
  if [[ ! -f /etc/os-release ]]; then
    echo "[setup] Unable to detect OS (missing /etc/os-release)." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "[setup] Detected OS: ${PRETTY_NAME:-unknown}. This script requires Ubuntu." >&2
    exit 1
  fi
  log "Detected Ubuntu ${VERSION_ID:-unknown} (${PRETTY_NAME:-Ubuntu})"
}

retry_cmd() {
  local attempts="${1:-3}"
  local delay="${2:-3}"
  shift 2
  local cmd=("$@")
  for ((i=1; i<=attempts; i++)); do
    if "${cmd[@]}"; then return 0; fi
    warn "Command failed (attempt $i/$attempts): ${cmd[*]}"
    if (( i < attempts )); then sleep "$delay"; fi
  done
  return 1
}

apt_wait_for_locks() {
  local attempts=30 delay=2
  if ! command -v lsof >/dev/null 2>&1; then
    warn "lsof not found; skipping apt lock inspection"
    return 0
  fi
  for ((i=1; i<=attempts; i++)); do
    if ! lsof /var/lib/dpkg/lock >/dev/null 2>&1 \
      && ! lsof /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
      && ! lsof /var/lib/apt/lists/lock >/dev/null 2>&1; then
      return 0
    fi
    log "apt/dpkg lock detected (attempt $i/${attempts}); waiting ${delay}s..."
    sleep "$delay"
  done
  warn "apt locks did not clear; continuing but operations may fail."
}

apt_update() {
  apt_wait_for_locks
  env DEBIAN_FRONTEND=noninteractive dpkg --configure -a || warn "dpkg configure reported issues"
  retry_cmd 3 5 env DEBIAN_FRONTEND=noninteractive apt-get update -y || warn "apt-get update failed after retries"
}

apt_install() {
  local packages=("$@")
  [[ ${#packages[@]} -eq 0 ]] && return 0
  apt_wait_for_locks
  env DEBIAN_FRONTEND=noninteractive dpkg --configure -a || warn "dpkg configure reported issues"
  retry_cmd 3 5 env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}" \
    || warn "apt-get install failed for: ${packages[*]}"
}

clean_state() {
  section "Cleaning previous state"
  if [[ -d "$VENV_DIR" ]]; then
    rm -rf "$VENV_DIR"
    log "Removed $VENV_DIR"
  fi
}

ensure_base_packages() {
  section "Ensuring base system packages"
  apt_update
  apt_install ca-certificates curl git gnupg lsb-release software-properties-common build-essential pkg-config lsof
}

add_docker_repository() {
  apt_install ca-certificates curl gnupg apt-transport-https
  mkdir -p /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
      || { warn "Unable to download Docker GPG key"; return 1; }
  fi
  chmod a+r /etc/apt/keyrings/docker.gpg || true
  # shellcheck disable=SC1091
  . /etc/os-release
  local codename="${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null || true)}"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt_update
}

ensure_docker() {
  if [[ "$WITH_DOCKER" != true ]]; then
    section "Skipping Docker setup (--without-docker)"
    return 0
  fi

  section "Ensuring Docker Engine and Compose"
  if command -v docker >/dev/null 2>&1; then
    log "Docker CLI detected ($(docker --version | head -n1))"
  else
    add_docker_repository || warn "Failed to prepare Docker repository"
    apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DOCKER_AVAILABLE=true
  else
    warn "Docker and/or Compose are unavailable after installation attempts"
    exit 1
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^docker.service'; then
    systemctl enable --now docker || warn "Failed to enable/start docker service"
  fi

  if [[ ${SUDO_USER:-root} != "root" ]]; then
    local user="${SUDO_USER:-$USER}"
    usermod -aG docker "$user" || warn "Failed to add $user to docker group"
    log "Added $user to docker group (you may need to log out/in)."
  fi
}

ensure_python() {
  if [[ "$SKIP_PYTHON" == true ]]; then
    section "Skipping Python setup (--skip-python)"
    return 0
  fi

  section "Setting up Python virtual environment"

  # Prefer 3.12 on Ubuntu 24.04+, but keep this resilient.
  apt_install python3 python3-venv python3-dev python3-pip
  if command -v python3.12 >/dev/null 2>&1; then
    apt_install python3.12 python3.12-venv python3.12-dev || true
    python3.12 -m venv "$VENV_DIR"
  else
    python3 -m venv "$VENV_DIR"
  fi

  local python="$VENV_DIR/bin/python"
  "$python" -m pip install --upgrade pip setuptools wheel

  # Base repo requirements (if present)
  if [[ -f "$ROOT_DIR/requirements.txt" ]]; then
    "$python" -m pip install -r "$ROOT_DIR/requirements.txt"
  fi

  # PQ library (repo-local)
  if [[ -d "$ROOT_DIR/pq" ]]; then
    "$python" -m pip install -e "$ROOT_DIR/pq"
  fi

  # IMPORTANT FIX:
  # Install the local SDK FIRST so any dependency on "omni-sdk"/"omni_sdk" is satisfied
  # before installing animica.
  if [[ -d "$ROOT_DIR/sdk/python" ]]; then
    "$python" -m pip install -e "$ROOT_DIR/sdk/python"
  else
    warn "Missing sdk/python; omni_sdk will not be available."
  fi

  # Ensure these are present for CLI runtime (tx signing/CBOR + faucet RPC):
  "$python" -m pip install --upgrade "cbor2>=5.6.0" "requests>=2.31.0"

  # Now install Animica python package (with extras if they exist)
  if [[ -d "$ROOT_DIR/python" ]]; then
    # Try extras first; fall back to plain editable if extras are not defined.
    if ! "$python" -m pip install -e "$ROOT_DIR/python[dev,stratum]"; then
      warn "Editable install with extras failed; retrying without extras."
      "$python" -m pip install -e "$ROOT_DIR/python"
    fi
  else
    warn "Missing python/ directory; skipping animica python install."
  fi

  "$python" -m pip check || warn "pip check reported issues (some optional deps may be missing)"
  log "Python environment ready: $VENV_DIR"
}

ensure_node() {
  if [[ "$SKIP_NODE" == true ]]; then
    section "Skipping Node.js setup (--skip-node)"
    return 0
  fi

  section "Ensuring Node.js (v20+)"

  if command -v node >/dev/null 2>&1; then
    local version major
    version="$(node -v | sed 's/^v//')"
    major="${version%%.*}"
    if (( major >= 20 )); then
      log "Found Node.js v$version"
      return 0
    fi
    warn "Node.js v$version detected; attempting to upgrade to v20.x"
  else
    log "Node.js not found; installing v20.x"
  fi

  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - || warn "Failed to add NodeSource repository; using default apt if possible"
  apt_update
  apt_install nodejs || warn "Node.js installation failed; install Node.js v20+ manually"
  command -v node >/dev/null 2>&1 && log "Installed Node.js $(node -v)" || warn "Node.js still missing after install"
}

ensure_pnpm() {
  if [[ "$SKIP_PNPM" == true ]]; then
    section "Skipping pnpm workspace install (--skip-pnpm)"
    return 0
  fi

  section "Installing pnpm workspace dependencies"
  if ! command -v corepack >/dev/null 2>&1; then
    warn "corepack not found; ensure Node.js is installed first"
    return 0
  fi

  corepack enable
  corepack prepare pnpm@latest --activate
  pnpm config set store-dir "$ROOT_DIR/.pnpm-store" || true

  local pnpm_cmd=(pnpm -r install)
  if [[ "$WITH_PLAYWRIGHT" != true ]]; then
    pnpm_cmd+=(--ignore-scripts)
  fi
  "${pnpm_cmd[@]}"
}

install_playwright() {
  if [[ "$WITH_PLAYWRIGHT" != true ]]; then
    log "Playwright install skipped (enable with --with-playwright)"
    return 0
  fi

  section "Installing Playwright browsers"
  apt_wait_for_locks

  if [[ -d "$ROOT_DIR/website" ]]; then
    retry_cmd 3 5 pnpm -C "$ROOT_DIR/website" exec playwright install --with-deps || warn "Playwright install failed for website"
  fi
  if [[ -d "$ROOT_DIR/studio-web" ]]; then
    retry_cmd 3 5 pnpm -C "$ROOT_DIR/studio-web" exec playwright install --with-deps || warn "Playwright install failed for studio-web"
  fi
}

main() {
  require_root
  require_ubuntu
  [[ "$CLEAN" == true ]] && clean_state
  ensure_base_packages
  ensure_docker
  ensure_python
  ensure_node
  ensure_pnpm
  install_playwright
  section "Done"
  log "Activate Python venv with: source \"$VENV_DIR/bin/activate\""
}

main "$@"
