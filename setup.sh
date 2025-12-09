#!/usr/bin/env bash
set -euo pipefail

# Animica monorepo bootstrapper
# Installs Node workspace dependencies and the local Animica Python packages.
# Ensures trio is available so trio-based pytest runs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBOQS_DIR="$ROOT_DIR/.liboqs"
LIBOQS_VERSION="0.10.1"
LIBOQS_REPO="https://github.com/open-quantum-safe/liboqs.git"

BLUE='\033[34m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
log() { echo -e "${BLUE}[setup]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET} $*"; }
fail() { echo -e "${RED}[fail]${RESET} $*"; exit 1; }

detect_os() {
  # Returns "Linux" or "Darwin" (macOS)
  uname -s
}

check_build_prerequisites() {
  local missing=()
  
  if ! command -v cmake >/dev/null 2>&1; then
    missing+=("cmake")
  fi
  
  if ! command -v make >/dev/null 2>&1; then
    missing+=("make")
  fi
  
  # Check for C compiler (gcc or clang)
  if ! command -v gcc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1; then
    missing+=("gcc or clang")
  fi
  
  if [[ ${#missing[@]} -gt 0 ]]; then
    fail "Missing required build tools: ${missing[*]}

To install build prerequisites:
  • Ubuntu/Debian: sudo apt-get install cmake build-essential
  • macOS: brew install cmake
  • Fedora/RHEL: sudo dnf install cmake gcc make"
  fi
  
  log "Build prerequisites check passed (cmake, make, C compiler)"
}

setup_liboqs_env_vars() {
  local install_prefix="$1"
  local os_type
  os_type=$(detect_os)
  
  # Set library paths so python-oqs can find liboqs
  export LIBRARY_PATH="${install_prefix}/lib:${LIBRARY_PATH:-}"
  export PKG_CONFIG_PATH="${install_prefix}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
  export C_INCLUDE_PATH="${install_prefix}/include:${C_INCLUDE_PATH:-}"
  export CPLUS_INCLUDE_PATH="${install_prefix}/include:${CPLUS_INCLUDE_PATH:-}"
  
  if [[ "$os_type" == "Darwin" ]]; then
    export DYLD_LIBRARY_PATH="${install_prefix}/lib:${DYLD_LIBRARY_PATH:-}"
  else
    export LD_LIBRARY_PATH="${install_prefix}/lib:${LD_LIBRARY_PATH:-}"
  fi
  
  log "Set library environment variables for liboqs at $install_prefix"
}

build_liboqs_from_source() {
  log "Building liboqs from source (this may take a few minutes)..."
  
  # Check prerequisites first
  check_build_prerequisites
  
  local src_dir="$LIBOQS_DIR/src"
  local build_dir="$LIBOQS_DIR/build"
  local install_prefix="$LIBOQS_DIR/install"
  
  # Clean up any partial builds
  if [[ -d "$LIBOQS_DIR" ]]; then
    log "Removing previous liboqs build at $LIBOQS_DIR"
    rm -rf "$LIBOQS_DIR"
  fi
  
  mkdir -p "$src_dir"
  
  # Clone liboqs repository
  log "Cloning liboqs v${LIBOQS_VERSION} from ${LIBOQS_REPO}..."
  if ! git clone --branch "$LIBOQS_VERSION" --depth 1 "$LIBOQS_REPO" "$src_dir" 2>&1 | grep -v "^Cloning"; then
    fail "Failed to clone liboqs repository"
  fi
  
  mkdir -p "$build_dir"
  cd "$build_dir"
  
  # Configure with CMake
  log "Configuring liboqs build (shared libs, no OpenSSL dependency)..."
  if ! cmake \
    -DCMAKE_INSTALL_PREFIX="$install_prefix" \
    -DBUILD_SHARED_LIBS=ON \
    -DOQS_USE_OPENSSL=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    "$src_dir" >/dev/null 2>&1; then
    fail "CMake configuration failed. Check that cmake and build tools are properly installed."
  fi
  
  # Build
  local nproc_count
  nproc_count=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)
  log "Building liboqs with $nproc_count parallel jobs..."
  if ! make -j"$nproc_count" >/dev/null 2>&1; then
    fail "liboqs build failed"
  fi
  
  # Install to local prefix
  log "Installing liboqs to $install_prefix..."
  if ! make install >/dev/null 2>&1; then
    fail "liboqs installation failed"
  fi
  
  cd "$ROOT_DIR"
  
  # Set up environment variables
  setup_liboqs_env_vars "$install_prefix"
  
  log "liboqs successfully built and installed to $install_prefix"
  log "Note: To reuse this liboqs build in future sessions, set these environment variables:"
  log "  export LIBRARY_PATH=\"$install_prefix/lib:\$LIBRARY_PATH\""
  log "  export PKG_CONFIG_PATH=\"$install_prefix/lib/pkgconfig:\$PKG_CONFIG_PATH\""
  log "  export C_INCLUDE_PATH=\"$install_prefix/include:\$C_INCLUDE_PATH\""
  
  local os_type
  os_type=$(detect_os)
  if [[ "$os_type" == "Darwin" ]]; then
    log "  export DYLD_LIBRARY_PATH=\"$install_prefix/lib:\$DYLD_LIBRARY_PATH\""
  else
    log "  export LD_LIBRARY_PATH=\"$install_prefix/lib:\$LD_LIBRARY_PATH\""
  fi
}

ensure_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    echo pnpm
    return
  fi
  if command -v npm >/dev/null 2>&1; then
    warn "pnpm not found; installing pnpm@9 globally via npm" >&2
    npm install -g pnpm@9 >/dev/null 2>&1 || fail "npm could not install pnpm"
    echo pnpm
    return
  fi
  fail "Neither pnpm nor npm is installed; please install one to continue"
}

install_node_deps() {
  local mgr
  mgr=$(ensure_pnpm)
  log "Installing Node workspace dependencies with $mgr"
  (cd "$ROOT_DIR" && $mgr install)
}

setup_python() {
  log "Creating Python virtual environment (.venv)"
  python3 -m venv "$ROOT_DIR/.venv"

  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"

  log "Upgrading pip and build tooling"
  python -m pip install --upgrade pip setuptools wheel

  if [[ -f "$ROOT_DIR/requirements.txt" ]]; then
    log "Installing shared Python dependencies (requirements.txt)"
    python -m pip install -r "$ROOT_DIR/requirements.txt"
  else
    warn "requirements.txt not found; skipping shared Python dependencies"
  fi

  log "Installing Animica Python package in editable mode with dev and stratum extras"
  python -m pip install -e "$ROOT_DIR/python[dev,stratum]"

  log "Installing SDK Python package in editable mode"
  python -m pip install -e "$ROOT_DIR/sdk/python"

  # Install pq module for bech32 support (required by omni_sdk)
  log "Installing pq module for post-quantum crypto support"
  SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
  PTH_FILE="$SITE_PACKAGES/animica-pq.pth"
  if [[ ! -f "$PTH_FILE" ]]; then
    # Add repository root to Python path so pq module can be imported
    echo "$ROOT_DIR" > "$PTH_FILE"
  fi

  # Install additional required dependencies
  log "Installing additional dependencies (requests for CLI, trio for RPC tests)"
  python -m pip install requests trio

  # Install python-oqs for production-ready PQ signing (SPHINCS+, Dilithium3)
  log "Installing python-oqs for post-quantum cryptographic signing"
  
  # Fast path: try to install python-oqs directly
  if python -m pip install python-oqs >/dev/null 2>&1; then
    log "python-oqs installed successfully"
  else
    warn "python-oqs installation failed (liboqs may not be available on this system)"
    log "Attempting to build liboqs from source..."
    
    # Check if git is available for cloning
    if ! command -v git >/dev/null 2>&1; then
      fail "git is required to build liboqs from source but is not installed"
    fi
    
    # Build liboqs from source
    build_liboqs_from_source
    
    # Retry python-oqs installation with liboqs now available
    log "Retrying python-oqs installation with liboqs built from source..."
    if python -m pip install python-oqs 2>&1 | tee /tmp/oqs_install_retry.log; then
      log "python-oqs installed successfully after building liboqs"
    else
      warn "python-oqs installation still failed after building liboqs"
      warn "Transaction signing will require ANIMICA_UNSAFE_PQ_FAKE=1 (development only)"
      warn "Check /tmp/oqs_install_retry.log for details"
      warn ""
      warn "You may need to:"
      warn "  1. Ensure liboqs was built correctly at $LIBOQS_DIR/install"
      warn "  2. Set environment variables manually (see above log messages)"
      warn "  3. Try: python -m pip install python-oqs --no-cache-dir"
    fi
  fi
}

log "Bootstrapping dependencies in $ROOT_DIR"
install_node_deps
setup_python

# Prepare a writable devnet environment file for docker-compose overrides
DEVNET_ENV_EXAMPLE="$ROOT_DIR/tests/devnet/env.devnet.example"
DEVNET_ENV_LOCAL="$ROOT_DIR/tests/devnet/.env"
if [[ -f "$DEVNET_ENV_EXAMPLE" && ! -f "$DEVNET_ENV_LOCAL" ]]; then
  log "Creating default devnet env at tests/devnet/.env (customize as needed)"
  cp "$DEVNET_ENV_EXAMPLE" "$DEVNET_ENV_LOCAL"
fi

log "Setup complete. Activate the environment with 'source .venv/bin/activate'."