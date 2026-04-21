#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

BUILD_ARGS=()
WEBSITE_DIR="$REPO_ROOT/website/public/wallet"
VERSION_OVERRIDE=""

usage() {
    cat <<'EOF'
Usage: ./scripts/release-windows-cross.sh [options]

Build Windows artifacts from Linux and publish them into website/public/wallet.

Options:
  --clean                   Remove the previous windows-cross build directory first
  --debug                   Build Debug artifacts instead of Release
  --check                   Validate prerequisites only, then exit
  --jobs <n>                Parallel build jobs
  --qt-root <path>          Windows-target Qt prefix
  --qt-host-path <path>     Host Qt prefix used for moc/rcc/uic
  --openssl-root <path>     Windows-target OpenSSL prefix
  --compiler-prefix <name>  MinGW compiler prefix (default: x86_64-w64-mingw32)
  --per-machine             Generate a per-machine installer (admin/UAC)
  --website-dir <path>      Override website/public/wallet destination
  --version <label>         Override the website build label (default: git describe)
  --help                    Show this help
EOF
}

fail() {
    printf 'release-windows-cross.sh: %s\n' "$1" >&2
    exit 1
}

resolve_version() {
    if [ -n "$VERSION_OVERRIDE" ]; then
        printf '%s\n' "$VERSION_OVERRIDE"
        return
    fi

    if git -C "$REPO_ROOT" describe --tags --exact-match >/dev/null 2>&1; then
        git -C "$REPO_ROOT" describe --tags --exact-match
        return
    fi

    local base_version
    base_version="$(git -C "$REPO_ROOT" describe --tags --abbrev=0 2>/dev/null || printf 'v0.1.1')"
    printf '%s-%s\n' "$base_version" "$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --website-dir)
                [ "$#" -ge 2 ] || fail "--website-dir requires a value"
                WEBSITE_DIR="$2"
                shift 2
                ;;
            --version)
                [ "$#" -ge 2 ] || fail "--version requires a value"
                VERSION_OVERRIDE="$2"
                shift 2
                ;;
            --help)
                usage
                exit 0
                ;;
            --clean|--debug|--check|--jobs|--qt-root|--qt-host-path|--openssl-root|--compiler-prefix|--per-machine)
                BUILD_ARGS+=("$1")
                if [ "$1" = "--jobs" ] || [ "$1" = "--qt-root" ] || [ "$1" = "--qt-host-path" ] || [ "$1" = "--openssl-root" ] || [ "$1" = "--compiler-prefix" ]; then
                    [ "$#" -ge 2 ] || fail "$1 requires a value"
                    BUILD_ARGS+=("$2")
                    shift 2
                else
                    shift
                fi
                ;;
            *)
                fail "Unknown option: $1"
                ;;
        esac
    done
}

main() {
    parse_args "$@"

    "$SCRIPT_DIR/build-windows-cross.sh" "${BUILD_ARGS[@]}"

    if printf '%s\n' "${BUILD_ARGS[@]}" | grep -qx -- '--check'; then
        exit 0
    fi

    "$SCRIPT_DIR/publish-wallet-downloads.sh" \
        --platform windows \
        --source-dir "$WALLET_ROOT/dist/windows" \
        --website-dir "$WEBSITE_DIR" \
        --version "$(resolve_version)"
}

main "$@"
