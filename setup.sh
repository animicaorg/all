#!/usr/bin/env bash
set -euo pipefail

# Animica monorepo bootstrapper
# Installs Node workspace dependencies and the local Animica Python packages.
# Ensures trio is available so trio-based pytest runs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBOQS_DIR="$ROOT_DIR/.liboqs"
LIBOQS_VERSION="0.15.0"
LIBOQS_PY_VERSION="$LIBOQS_VERSION"
LIBOQS_REPO="https://github.com/open-quantum-safe/liboqs.git"

# Parse command-line flags
SKIP_LIBOQS_REBUILD=false
for arg in "$@"; do
  case "$arg" in
    --skip-liboqs-rebuild)
      SKIP_LIBOQS_REBUILD=true
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--skip-liboqs-rebuild]"
      exit 1
      ;;
  esac
done

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
  log "════════════════════════════════════════════════════════════════════════"
  log "Building liboqs from source (this may take a few minutes)..."
  log "Using liboqs version: $LIBOQS_VERSION"
  log "Repository: $LIBOQS_REPO"
  log "════════════════════════════════════════════════════════════════════════"
  
  # Check prerequisites first
  check_build_prerequisites
  
  local src_dir="$LIBOQS_DIR/src"
  local build_dir="$LIBOQS_DIR/build"
  local install_prefix="$LIBOQS_DIR/install"
  
  # Always clean up previous builds to ensure fresh installation
  if [[ -d "$LIBOQS_DIR" ]]; then
    log "Removing previous liboqs build at $LIBOQS_DIR"
    rm -rf "$LIBOQS_DIR"
  fi
  
  mkdir -p "$src_dir"
  
  # Clone liboqs repository
  log "Cloning liboqs v${LIBOQS_VERSION} from ${LIBOQS_REPO}..."
  if ! git clone --branch "$LIBOQS_VERSION" --depth 1 "$LIBOQS_REPO" "$src_dir" 2>&1 | grep -v "^Cloning"; then
    fail "Failed to clone liboqs repository at tag/branch '$LIBOQS_VERSION'.

Possible causes:
  1. The tag '$LIBOQS_VERSION' does not exist in the repository
     (Note: liboqs uses version tags like '0.15.0', not 'v0.15.0')
  2. Network connectivity issues
  3. Repository URL has changed

Please verify the tag exists at: https://github.com/open-quantum-safe/liboqs/tags
Current tag being used: $LIBOQS_VERSION

If the tag doesn't exist, check the liboqs releases page for available versions:
https://github.com/open-quantum-safe/liboqs/releases"
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
  
  log ""
  log "════════════════════════════════════════════════════════════════════════"
  log "✓ liboqs v${LIBOQS_VERSION} successfully built and installed"
  log "  Install location: $install_prefix"
  log "  Library path: $install_prefix/lib"
  log "  Include path: $install_prefix/include"
  log ""
  log "Environment variables have been set for the current session."
  log ""
  log "To reuse this liboqs build in future shell sessions:"
  log "  1. Source the convenience script: source $ROOT_DIR/.liboqs/env.sh"
  log "  2. Or add to your shell profile (~/.bashrc, ~/.zshrc):"
  log ""
  log "     export LIBRARY_PATH=\"$install_prefix/lib:\$LIBRARY_PATH\""
  log "     export PKG_CONFIG_PATH=\"$install_prefix/lib/pkgconfig:\$PKG_CONFIG_PATH\""
  log "     export C_INCLUDE_PATH=\"$install_prefix/include:\$C_INCLUDE_PATH\""
  
  local os_type
  os_type=$(detect_os)
  if [[ "$os_type" == "Darwin" ]]; then
    log "     export DYLD_LIBRARY_PATH=\"$install_prefix/lib:\$DYLD_LIBRARY_PATH\""
  else
    log "     export LD_LIBRARY_PATH=\"$install_prefix/lib:\$LD_LIBRARY_PATH\""
  fi
  
  log "════════════════════════════════════════════════════════════════════════"
  log ""
  
  # Create a convenience script to source the environment variables
  cat > "$LIBOQS_DIR/env.sh" << 'ENVSCRIPT'
#!/usr/bin/env bash
# Source this file to set up liboqs environment variables
# Usage: source .liboqs/env.sh

LIBOQS_PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")/install" && pwd)"

export LIBRARY_PATH="${LIBOQS_PREFIX}/lib:${LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${LIBOQS_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export C_INCLUDE_PATH="${LIBOQS_PREFIX}/include:${C_INCLUDE_PATH:-}"
export CPLUS_INCLUDE_PATH="${LIBOQS_PREFIX}/include:${CPLUS_INCLUDE_PATH:-}"

case "$(uname -s)" in
  Darwin)
    export DYLD_LIBRARY_PATH="${LIBOQS_PREFIX}/lib:${DYLD_LIBRARY_PATH:-}"
    ;;
  *)
    export LD_LIBRARY_PATH="${LIBOQS_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
    ;;
esac

echo "[liboqs env] Environment variables set for liboqs at $LIBOQS_PREFIX"
ENVSCRIPT
  chmod +x "$LIBOQS_DIR/env.sh"
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

  # Install liboqs-python for production-ready PQ signing (SPHINCS+, Dilithium3)
  log "Installing liboqs-python for post-quantum cryptographic signing"
  
  # Check if git is available for cloning (required for liboqs build)
  if ! command -v git >/dev/null 2>&1; then
    fail "git is required to build liboqs from source but is not installed.
To install git:
  • Ubuntu/Debian: sudo apt-get install git
  • macOS: brew install git or install Xcode Command Line Tools
  • Fedora/RHEL: sudo dnf install git"
  fi
  
  # Strategy: Always build liboqs from source to ensure fresh, known-good installation
  # unless --skip-liboqs-rebuild flag is passed
  if [[ "$SKIP_LIBOQS_REBUILD" == "false" ]]; then
    log "Building liboqs from source (use --skip-liboqs-rebuild to skip on repeated runs)..."
    # Build liboqs from source (this will check for cmake, make, gcc/clang and fail if missing)
    build_liboqs_from_source
  else
    log "Skipping liboqs rebuild (--skip-liboqs-rebuild flag set)"
    local install_prefix="$LIBOQS_DIR/install"
    if [[ -d "$install_prefix" ]]; then
      log "Using existing liboqs installation at $install_prefix"
      setup_liboqs_env_vars "$install_prefix"
    else
      warn "No existing liboqs installation found at $install_prefix"
      log "Building liboqs from source (required for first-time setup)..."
      build_liboqs_from_source
    fi
  fi
  
  # Install liboqs-python with locally-built liboqs
  log "Installing liboqs-python with locally-built liboqs..."
  
  # Capture the output in a temporary file for diagnostics
  local install_log
  install_log=$(mktemp /tmp/oqs_install.XXXXXX.log)
  # Build from source to ensure compatibility with our liboqs 0.15.0
  # Using --no-binary forces pip to build from source instead of using wheels
  # that may have older bundled liboqs versions (0.14.x)
  if python -m pip install "liboqs-python==${LIBOQS_PY_VERSION}" --no-binary liboqs-python --no-cache-dir >"$install_log" 2>&1; then
    log "✓ liboqs-python installed successfully (built from source)"
    log "  Built liboqs is at: $LIBOQS_DIR/install"
    rm -f "$install_log"
  else
    fail "Failed to install liboqs-python with locally-built liboqs.

Build location: $LIBOQS_DIR/install
Installation log: $install_log

Possible issues:
  1. The liboqs build may have succeeded but pip cannot find the shared library
  2. Missing Python development headers (python3-dev or python3-devel package)
  3. Environment variables not properly set in the current shell

To debug:
  • Check the installation log: cat $install_log
  • Verify liboqs library exists: ls -la $LIBOQS_DIR/install/lib/liboqs.*
  • Try manual installation: python -m pip install liboqs-python --no-binary liboqs-python --no-cache-dir

Note: Post-quantum signing is currently unavailable. As a workaround for development
only, you can set ANIMICA_UNSAFE_PQ_FAKE=1, but this is NOT secure and should
NEVER be used in production."
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