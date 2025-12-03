#!/usr/bin/env bash
set -euo pipefail

# Build unsigned desktop installers for the Animica wallet on macOS and Linux.
# - Installs platform build dependencies (apt/brew) when available
# - Fetches Flutter packages
# - Builds release binaries
# - Packages artifacts into installers/dist with descriptive names

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${REPO_ROOT}/wallet"
DIST_DIR="${REPO_ROOT}/installers/dist"
PUBSPEC="${APP_DIR}/pubspec.yaml"

log() { printf "[wallet-build] %s\n" "$*"; }
err() { printf "[wallet-build][error] %s\n" "$*" >&2; exit 1; }

read_version() {
  if [[ ! -f "${PUBSPEC}" ]]; then
    err "pubspec.yaml not found at ${PUBSPEC}"
  fi
  awk -F": " '/^version:/ {print $2; exit}' "${PUBSPEC}"
}

install_common_deps() {
  log "Ensuring Flutter dependencies are available"
  if ! command -v flutter >/dev/null 2>&1; then
    err "Flutter is required but not on PATH. Install Flutter 3.x before running."
  fi

  (cd "${APP_DIR}" && flutter pub get)
}

install_macos_deps() {
  log "Installing macOS toolchain dependencies"
  if command -v brew >/dev/null 2>&1; then
    brew update >/dev/null
    brew install --quiet cmake ninja || true
  fi
  flutter config --enable-macos-desktop
}

install_linux_deps() {
  log "Installing Linux toolchain dependencies"
  if command -v apt-get >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && [[ "$(id -u)" -ne 0 ]]; then
      sudo apt-get update -y
      sudo apt-get install -y clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev libglu1-mesa
    else
      apt-get update -y
      apt-get install -y clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev libglu1-mesa
    fi
  fi
  flutter config --enable-linux-desktop
}

package_macos() {
  local version="$1"
  local arch="$2"
  log "Building macOS release binary"
  (cd "${APP_DIR}" && flutter build macos --release)

  local build_dir="${APP_DIR}/build/macos/Build/Products/Release"
  local app_bundle
  app_bundle="$(find "${build_dir}" -maxdepth 1 -name "*.app" | head -n 1)"
  [[ -n "${app_bundle}" ]] || err "macOS .app bundle not found under ${build_dir}"

  mkdir -p "${DIST_DIR}"
  local dmg_name="animica-wallet-${version}-macos-${arch}.dmg"
  local dmg_path="${DIST_DIR}/${dmg_name}"

  log "Creating DMG ${dmg_name}"
  hdiutil create -volname "Animica Wallet" -srcfolder "${app_bundle}" -ov -format UDZO "${dmg_path}"
  log "macOS artifact written to ${dmg_path}"
}

package_linux() {
  local version="$1"
  local arch="$2"
  log "Building Linux release binary"
  (cd "${APP_DIR}" && flutter build linux --release)

  local bundle_dir="${APP_DIR}/build/linux/${arch}/release/bundle"
  [[ -d "${bundle_dir}" ]] || err "Linux bundle not found at ${bundle_dir}"

  mkdir -p "${DIST_DIR}"
  local tar_name="animica-wallet-${version}-linux-${arch}.tar.gz"
  local tar_path="${DIST_DIR}/${tar_name}"

  log "Packaging Linux bundle into ${tar_name}"
  tar -czf "${tar_path}" -C "${bundle_dir}" .
  log "Linux artifact written to ${tar_path}"
}

usage() {
  cat <<USAGE
Build desktop installers for the Animica wallet.
Usage: $(basename "$0") [macos|linux|all]
Default target is the host platform.
USAGE
}

target_from_host() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux) echo "linux" ;;
    *) err "Unsupported host platform $(uname -s)." ;;
  esac
}

main() {
  local target="${1:-""}"
  local version arch bundle_arch

  if [[ -z "${target}" ]]; then
    target="$(target_from_host)"
  fi

  case "${target}" in
    macos|linux|all) ;;
    -h|--help) usage; exit 0 ;;
    *) usage; err "Unknown target '${target}'" ;;
  esac

  version="$(read_version)"
  arch="$(uname -m)"
  case "${arch}" in
    x86_64|amd64) bundle_arch="x64" ;;
    arm64|aarch64) bundle_arch="arm64" ;;
    *) bundle_arch="${arch}" ;;
  esac

  install_common_deps

  if [[ "${target}" == "macos" || "${target}" == "all" ]]; then
    install_macos_deps
    package_macos "${version}" "${arch}"
  fi

  if [[ "${target}" == "linux" || "${target}" == "all" ]]; then
    install_linux_deps
    package_linux "${version}" "${bundle_arch}"
  fi

  log "Done. Artifacts are in ${DIST_DIR}"
}

main "$@"
