#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# build_all.sh — Build contracts + dapp for the fullstack monorepo template
#
# What it does (best-effort, safe to run repeatedly):
#   1) Contracts:
#      - If a Makefile exists with a "build" target → uses `make build`
#      - Else, if requirements.txt exists → creates a venv, installs deps
#      - Then tries (in order):
#         a) `python -m contracts.tools.build_package` (repo tooling)
#         b) `python scripts/build.py` (if present in the project)
#         c) skips with a helpful message if neither tool is present
#      - Outputs typically land in ./contracts/build/
#
#   2) Dapp:
#      - Picks a package manager automatically (pnpm > yarn > npm)
#      - Runs install (unless --no-install) then build
#      - Outputs typically land in ./dapp/dist/
#
# Usage:
#   ./scripts/build_all.sh                # build contracts + dapp
#   ./scripts/build_all.sh --contracts    # build only contracts
#   ./scripts/build_all.sh --dapp         # build only dapp
#   ./scripts/build_all.sh --no-install   # skip package installs (dapp)
#   ./scripts/build_all.sh --clean        # clean typical artifacts before build
#
# Notes:
#   - This script is intentionally defensive and portable; customize freely.
#   - If your contracts project uses a different builder, wire it in the
#     build_contracts() function below.
# ------------------------------------------------------------------------------

set -Eeuo pipefail

# ----------- tiny helpers -----------------------------------------------------
say()   { printf "\033[1;36m[build]\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
err()   { printf "\033[1;31m[err]\033[0m  %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

# Resolve repo root as the directory that contains this script (2 levels up)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONTRACTS_DIR="$REPO_ROOT/contracts"
DAPP_DIR="$REPO_ROOT/dapp"

ONLY_CONTRACTS=0
ONLY_DAPP=0
NO_INSTALL=0
DO_CLEAN=0

for arg in "$@"; do
  case "$arg" in
    --contracts) ONLY_CONTRACTS=1 ;;
    --dapp)      ONLY_DAPP=1 ;;
    --no-install) NO_INSTALL=1 ;;
    --clean)     DO_CLEAN=1 ;;
    -h|--help)
      sed -n '1,80p' "$0" | sed -n '1,/^# ------------------------------------------------------------------------------$/p' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      die "Unknown flag: $arg (use -h for help)"
      ;;
  esac
done

if (( ONLY_CONTRACTS == 1 && ONLY_DAPP == 1 )); then
  die "Choose either --contracts or --dapp, not both."
fi

# ----------- cleaners ---------------------------------------------------------
clean_artifacts() {
  say "Cleaning common build artifacts…"
  # Contracts
  if [ -d "$CONTRACTS_DIR" ]; then
    find "$CONTRACTS_DIR" -maxdepth 2 -type d \( -name "build" -o -name ".venv" \) -prune -print0 \
      | xargs -0 -I{} bash -c 'say " removing: {}"; rm -rf "{}"' || true
    find "$CONTRACTS_DIR" -type d -name "__pycache__" -print0 | xargs -0 rm -rf || true
  fi
  # Dapp
  if [ -d "$DAPP_DIR" ]; then
    find "$DAPP_DIR" -maxdepth 2 -type d \( -name "node_modules" -o -name "dist" \) -prune -print0 \
      | xargs -0 -I{} bash -c 'say " removing: {}"; rm -rf "{}"' || true
  fi
}

# ----------- dapp package manager detection ----------------------------------
pick_pm() {
  # prefer pnpm > yarn > npm
  if command -v pnpm >/dev/null 2>&1; then
    echo "pnpm"
  elif command -v yarn >/dev/null 2>&1; then
    echo "yarn"
  elif command -v npm >/dev/null 2>&1; then
    echo "npm"
  else
    echo ""
  fi
}

# ----------- contract build ---------------------------------------------------
build_contracts() {
  [ -d "$CONTRACTS_DIR" ] || { warn "Contracts dir not found: $CONTRACTS_DIR"; return 0; }
  say "Building contracts…"
  cd "$CONTRACTS_DIR"

  # 1) Makefile build if present
  if [ -f Makefile ] && make -n build >/dev/null 2>&1; then
    say "Using Makefile target: make build"
    make build
    say "Contracts built via make."
    return 0
  fi

  # 2) Python venv + deps if requirements.txt exists
  if [ -f requirements.txt ]; then
    say "Setting up Python venv (./.venv) and installing dependencies…"
    python3 -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
  fi

  # 3) Try repo tooling: contracts.tools.build_package
  if python - <<'PY' >/dev/null 2>&1; then
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("contracts.tools.build_package") else 1)
PY
  then
    say "Using contracts.tools.build_package (repo tooling)…"
    mkdir -p build
    # Best-effort defaults; customize as needed for multiple contracts.
    python -m contracts.tools.build_package \
      --src contracts/contract.py \
      --manifest contracts/manifest.json \
      --out build/
    say "Contracts built with contracts.tools.build_package."
    return 0
  fi

  # 4) Fallback: project-local script
  if [ -f scripts/build.py ]; then
    say "Using local builder: scripts/build.py"
    python scripts/build.py
    say "Contracts built with scripts/build.py."
    return 0
  fi

  warn "No contract build tool found (Makefile, repo tooling, or scripts/build.py). Skipping contracts."
}

# ----------- dapp build -------------------------------------------------------
build_dapp() {
  [ -d "$DAPP_DIR" ] || { warn "Dapp dir not found: $DAPP_DIR"; return 0; }
  say "Building dapp…"
  cd "$DAPP_DIR"

  PM="$(pick_pm)"
  [ -n "$PM" ] || die "No package manager found (need pnpm, yarn, or npm)."

  if (( NO_INSTALL == 0 )); then
    say "Installing dependencies with $PM…"
    case "$PM" in
      pnpm) pnpm install ;;
      yarn) yarn install --frozen-lockfile || yarn install ;;
      npm)  npm ci || npm install ;;
    esac
  else
    say "Skipping dependency install (--no-install)."
  fi

  say "Running build with $PM…"
  case "$PM" in
    pnpm) pnpm build ;;
    yarn) yarn build ;;
    npm)  npm run build ;;
  endcase 2>/dev/null || true

  # If the shell doesn't support "endcase", fall back (POSIX sh compatibility)
  if [ $? -ne 0 ]; then
    case "$PM" in
      pnpm) pnpm build ;;
      yarn) yarn build ;;
      npm)  npm run build ;;
    esac
  fi

  if [ -d "dist" ]; then
    say "Dapp built → $(pwd)/dist"
  else
    warn "Build completed but no ./dist directory found. Check your build script."
  fi
}

# ----------- main -------------------------------------------------------------
if (( DO_CLEAN == 1 )); then
  clean_artifacts
fi

if (( ONLY_CONTRACTS == 1 )); then
  build_contracts
elif (( ONLY_DAPP == 1 )); then
  build_dapp
else
  build_contracts
  build_dapp
fi

say "All done."
