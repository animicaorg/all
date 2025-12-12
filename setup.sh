#!/usr/bin/env bash
set -euo pipefail
set -o errtrace
IFS=$'\n\t'

trap 'echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") ERROR line ${LINENO}: ${BASH_COMMAND:-unknown}" >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
LIBOQS_DIR="$ROOT_DIR/.liboqs"
LIBOQS_VERSION="0.10.0"
LIBOQS_PY_VERSION="0.10.0"
WITH_PLAYWRIGHT=false
WITH_PQ=false
SKIP_NODE=false
SKIP_PYTHON=false
SKIP_PNPM=false
CLEAN=false
WITH_DOCKER=true

log() {
  echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*"
}

warn() {
  echo "[setup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") WARN: $*" >&2
}

section() {
  echo
  echo "[setup] ================================================================="
  log "$*"
  echo "[setup] ================================================================="
}

usage() {
  cat <<'USAGE'
Usage: setup.sh [options]
  --clean            Remove .venv and .liboqs before installing
  --with-playwright  Also install Playwright browsers and dependencies
  --with-pq          Build and install liboqs/liboqs-python (post-quantum)
  --without-docker   Skip Docker Engine / Docker Compose installation
  --skip-node        Skip Node.js installation check
  --skip-python      Skip Python environment setup
  --skip-pnpm        Skip pnpm workspace install
  --help             Show this help message
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-playwright) WITH_PLAYWRIGHT=true ;;
    --with-pq) WITH_PQ=true ;;
    --without-docker) WITH_DOCKER=false ;;
    --skip-node) SKIP_NODE=true ;;
    --skip-python) SKIP_PYTHON=true ;;
    --skip-pnpm) SKIP_PNPM=true ;;
    --clean) CLEAN=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "[setup] This script must be run as root (try: sudo ./setup.sh)." >&2
    exit 1
  fi
}

require_ubuntu() {
  if [[ ! -f /etc/os-release ]]; then
    echo "[setup] Unable to detect OS (missing /etc/os-release)." >&2
    exit 1
  fi
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "[setup] Detected OS: ${PRETTY_NAME:-unknown}. This script requires Ubuntu." >&2
    exit 1
  fi
  log "Detected Ubuntu ${VERSION_ID:-unknown} (${PRETTY_NAME:-Ubuntu})"
  if [[ -n "${VERSION_ID:-}" ]]; then
    local major minor
    IFS='.' read -r major minor <<<"${VERSION_ID}"
    if (( major < 24 )); then
      warn "Ubuntu ${VERSION_ID} detected. Ubuntu 24.04 or newer is recommended."
    fi
  fi
}

wait_for_apt() {
  local attempts=30
  local delay=2
  if ! command -v lsof >/dev/null 2>&1; then
    warn "lsof not found; skipping apt lock inspection"
    return 0
  fi
  for ((i=1; i<=attempts; i++)); do
    if ! lsof /var/lib/dpkg/lock >/dev/null 2>&1 && \
       ! lsof /var/lib/dpkg/lock-frontend >/dev/null 2>&1 && \
       ! lsof /var/lib/apt/lists/lock >/dev/null 2>&1; then
      return 0
    fi
    log "apt/dpkg lock detected (attempt $i/${attempts}); waiting ${delay}s..."
    sleep "$delay"
  done
  warn "apt locks did not clear after $((attempts*delay))s; continuing but operations may fail."
}

apt_update() {
  wait_for_apt
  DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true
  DEBIAN_FRONTEND=noninteractive apt-get update -y
}

apt_install() {
  local packages=("$@")
  if [[ ${#packages[@]} -eq 0 ]]; then
    return
  fi
  wait_for_apt
  DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
}

retry_cmd() {
  local attempts=${1:-3}
  local delay=${2:-3}
  shift 2
  local cmd=("$@")

  for ((i=1; i<=attempts; i++)); do
    if "${cmd[@]}"; then
      return 0
    fi
    warn "Command failed (attempt $i/$attempts): ${cmd[*]}"
    if (( i < attempts )); then
      sleep "$delay"
    fi
  done
  return 1
}

clean_state() {
  section "Cleaning previous state"
  if [[ -d "$VENV_DIR" ]]; then
    rm -rf "$VENV_DIR"
    log "Removed $VENV_DIR"
  fi
  if [[ -d "$LIBOQS_DIR" ]]; then
    rm -rf "$LIBOQS_DIR"
    log "Removed $LIBOQS_DIR"
  fi
}

ensure_base_packages() {
  section "Ensuring base system packages"
  apt_update
  apt_install ca-certificates curl git gnupg lsb-release software-properties-common build-essential pkg-config lsof
}

ensure_docker() {
  if [[ "$WITH_DOCKER" != true ]]; then
    section "Skipping Docker setup (--without-docker)"
    return
  fi

  section "Ensuring Docker Engine and Compose"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker already installed ($(docker --version | head -n1))"
  else
    apt_update
    apt_install apt-transport-https ca-certificates curl gnupg
    apt_install docker.io docker-compose-plugin
  fi

  if systemctl list-unit-files | grep -q '^docker.service'; then
    systemctl enable --now docker || warn "Failed to enable/start docker service"
  fi

  if ! docker compose version >/dev/null 2>&1; then
    warn "Docker Compose plugin not available; please install manually."
  fi

  if [[ ${SUDO_USER:-root} != "root" ]]; then
    local user=${SUDO_USER:-$USER}
    usermod -aG docker "$user" || warn "Failed to add $user to docker group"
    log "Added $user to docker group (you may need to log out/in)."
  fi
}

ensure_python() {
  if [[ "$SKIP_PYTHON" == true ]]; then
    section "Skipping Python setup (--skip-python)"
    return
  fi

  section "Setting up Python 3.12 virtual environment"
  apt_install python3.12 python3.12-venv python3.12-dev

  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3.12 -m venv "$VENV_DIR"
    log "Created virtualenv at $VENV_DIR"
  else
    log "Virtualenv already exists at $VENV_DIR"
  fi

  local python="$VENV_DIR/bin/python"
  "$python" -m pip install --upgrade pip setuptools wheel
  "$python" -m pip install -r "$ROOT_DIR/requirements.txt"
  "$python" -m pip install -e "$ROOT_DIR/python[dev,stratum]"
  "$python" -m pip install -e "$ROOT_DIR/sdk/python"
}

ensure_node() {
  if [[ "$SKIP_NODE" == true ]]; then
    section "Skipping Node.js setup (--skip-node)"
    return
  fi

  section "Checking Node.js"
  if command -v node >/dev/null 2>&1; then
    local version
    version=$(node -v | sed 's/^v//')
    local major=${version%%.*}
    if (( major >= 20 )); then
      log "Found Node.js v$version"
      return
    else
      warn "Node.js v$version detected; attempting to upgrade to v20.x"
    fi
  else
    log "Node.js not found; installing v20.x from NodeSource"
  fi

  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt_update
  apt_install nodejs
  log "Installed Node.js $(node -v)"
}

ensure_pnpm() {
  if [[ "$SKIP_PNPM" == true ]]; then
    section "Skipping pnpm workspace install (--skip-pnpm)"
    return
  fi

  section "Installing pnpm workspace dependencies"
  corepack enable
  corepack prepare pnpm@latest --activate
  pnpm config set store-dir "$ROOT_DIR/.pnpm-store"
  pnpm -r install
}

install_playwright() {
  if [[ "$WITH_PLAYWRIGHT" == true ]]; then
    section "Installing Playwright browsers"
    wait_for_apt
    if ! retry_cmd 3 5 pnpm exec playwright install --with-deps; then
      warn "Playwright installation failed after retries; browser tests may not work."
    fi
  else
    log "Playwright install skipped (enable with --with-playwright)"
  fi
}

write_liboqs_env() {
  cat > "$LIBOQS_DIR/env.sh" <<'ENVVARS'
#!/usr/bin/env bash
LIBOQS_PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")/install" && pwd)"
export LD_LIBRARY_PATH="${LIBOQS_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export LIBRARY_PATH="${LIBOQS_PREFIX}/lib:${LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${LIBOQS_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export C_INCLUDE_PATH="${LIBOQS_PREFIX}/include:${C_INCLUDE_PATH:-}"
export CPLUS_INCLUDE_PATH="${LIBOQS_PREFIX}/include:${CPLUS_INCLUDE_PATH:-}"
ENVVARS
  chmod +x "$LIBOQS_DIR/env.sh"
}

build_liboqs() {
  if [[ "$WITH_PQ" != true ]]; then
    log "PQ setup skipped (enable with --with-pq)"
    return
  fi

  section "Building liboqs ${LIBOQS_VERSION}"
  apt_install cmake ninja-build gcc g++ make pkg-config git

  local build_root="$LIBOQS_DIR/build"
  local src_dir="$LIBOQS_DIR/src"
  local install_dir="$LIBOQS_DIR/install"

  rm -rf "$LIBOQS_DIR"
  mkdir -p "$build_root" "$src_dir" "$install_dir"

  git clone --branch "$LIBOQS_VERSION" --depth 1 https://github.com/open-quantum-safe/liboqs.git "$src_dir"
  pushd "$build_root" >/dev/null
  cmake -GNinja -DCMAKE_INSTALL_PREFIX="$install_dir" -DBUILD_SHARED_LIBS=ON -DOQS_USE_OPENSSL=OFF -DCMAKE_BUILD_TYPE=Release "$src_dir"
  ninja
  ninja install
  popd >/dev/null

  write_liboqs_env
  log "liboqs installed to $install_dir"
}

install_liboqs_python() {
  if [[ "$WITH_PQ" != true ]]; then
    return
  fi
  if [[ "$SKIP_PYTHON" == true ]]; then
    warn "Skipping liboqs-python because Python setup was skipped"
    return
  fi

  section "Installing liboqs-python ${LIBOQS_PY_VERSION}"
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    warn "Virtualenv missing; cannot install liboqs-python"
    return
  fi

  # shellcheck source=/dev/null
  source "$LIBOQS_DIR/env.sh"
  local python="$VENV_DIR/bin/python"
  "$python" -m pip install --no-binary=:all: --force-reinstall "liboqs-python==${LIBOQS_PY_VERSION}"

  local verify_script='import oqs, sys; print("liboqs version", oqs.oqs_version());
from oqs import Signature; sig=Signature("SPHINCS+-SHAKE-128s-simple"); print("sig alg", sig.details); print("Loaded from", oqs.LIB)'
  if "$python" - <<PY
${verify_script}
PY
  then
    log "liboqs-python verification succeeded"
  else
    warn "liboqs-python verification failed. Cleaning up $LIBOQS_DIR to avoid partial state."
    rm -rf "$LIBOQS_DIR"
    exit 1
  fi
}

husky_notice() {
  section "Husky/git hooks"
  if [[ ! -d "$ROOT_DIR/.git" ]]; then
    log "Skipped husky setup: not a git checkout"
    return
  fi
  if [[ "$SKIP_PNPM" == false ]]; then
    if ! pnpm exec husky install; then
      warn "Husky install via pnpm exec failed; retrying with pnpm dlx"
      pnpm dlx husky install || warn "Husky install skipped (command failed)"
    fi
  else
    log "Skipped husky setup because pnpm install was skipped"
  fi
}

smoke_tests() {
  section "Running CLI smoke checks"
  if [[ -x "$VENV_DIR/bin/animica" ]]; then
    "$VENV_DIR/bin/animica" --help >/dev/null || warn "animica --help failed"
    "$VENV_DIR/bin/animica" wallet create --label setup_smoke >/dev/null 2>&1 || warn "wallet already exists or command failed"
  else
    warn "animica CLI not found in virtualenv; skipping smoke tests"
  fi
  log "Activate environment with: source $VENV_DIR/bin/activate"
  log "Start a node with: animica node up"
}

main() {
  require_root
  require_ubuntu

  if [[ "$CLEAN" == true ]]; then
    clean_state
  fi

  ensure_base_packages
  ensure_python
  ensure_node
  ensure_docker
  ensure_pnpm
  install_playwright
  build_liboqs
  install_liboqs_python
  husky_notice
  smoke_tests
  log "Setup completed successfully."
}

main
