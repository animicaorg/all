#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

WEBSITE_DIR="$REPO_ROOT/website/public/wallet"
PLATFORM=""
SOURCE_DIR=""
VERSION=""

LINUX_APPIMAGE=""
LINUX_DEB=""
LINUX_TARBALL=""
LINUX_RAW=""

usage() {
    cat <<'EOF'
Usage: ./scripts/publish-wallet-downloads.sh [options]

Publish wallet artifacts into website/public/wallet and regenerate manifest.json.

Options:
  --platform <windows|linux>  Platform to publish
  --version <label>           Version/build label to expose on the website
  --source-dir <path>         Windows dist directory (defaults to wallet-qt/dist/windows)
  --website-dir <path>        Override website/public/wallet output directory
  --appimage <path>           Linux AppImage to publish
  --deb <path>                Linux .deb to publish
  --tarball <path>            Linux portable tar.gz to publish
  --raw <path>                Linux raw animica-wallet executable to publish
  --help                      Show this help
EOF
}

log() {
    printf '[publish-wallet] %s\n' "$1"
}

fail() {
    printf 'publish-wallet-downloads.sh: %s\n' "$1" >&2
    exit 1
}

copy_if_requested() {
    local source_path="$1"
    local destination_path="$2"

    if [ -z "$source_path" ]; then
        return
    fi
    [ -f "$source_path" ] || fail "Requested artifact does not exist: $source_path"
    install -m 0644 "$source_path" "$destination_path"
}

write_checksum_file() {
    local output_path="$1"
    shift
    local files=("$@")

    if [ "${#files[@]}" -eq 0 ]; then
        rm -f "$output_path"
        return
    fi

    (
        cd "$WEBSITE_DIR"
        sha256sum "${files[@]}" > "$(basename "$output_path")"
    )
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --platform)
                [ "$#" -ge 2 ] || fail "--platform requires a value"
                PLATFORM="$2"
                shift 2
                ;;
            --version)
                [ "$#" -ge 2 ] || fail "--version requires a value"
                VERSION="$2"
                shift 2
                ;;
            --source-dir)
                [ "$#" -ge 2 ] || fail "--source-dir requires a value"
                SOURCE_DIR="$2"
                shift 2
                ;;
            --website-dir)
                [ "$#" -ge 2 ] || fail "--website-dir requires a value"
                WEBSITE_DIR="$2"
                shift 2
                ;;
            --appimage)
                [ "$#" -ge 2 ] || fail "--appimage requires a value"
                LINUX_APPIMAGE="$2"
                shift 2
                ;;
            --deb)
                [ "$#" -ge 2 ] || fail "--deb requires a value"
                LINUX_DEB="$2"
                shift 2
                ;;
            --tarball)
                [ "$#" -ge 2 ] || fail "--tarball requires a value"
                LINUX_TARBALL="$2"
                shift 2
                ;;
            --raw)
                [ "$#" -ge 2 ] || fail "--raw requires a value"
                LINUX_RAW="$2"
                shift 2
                ;;
            --help)
                usage
                exit 0
                ;;
            *)
                fail "Unknown option: $1"
                ;;
        esac
    done

    [ -n "$PLATFORM" ] || fail "--platform is required"
    [ -n "$VERSION" ] || fail "--version is required"
}

publish_windows() {
    local dist_dir="${SOURCE_DIR:-$WALLET_ROOT/dist/windows}"
    local source_installer="$dist_dir/animica-wallet-setup-x64.exe"
    local source_zip="$dist_dir/animica-wallet-windows-x64.zip"

    [ -f "$source_installer" ] || fail "Windows installer not found at $source_installer"
    [ -f "$source_zip" ] || fail "Windows ZIP not found at $source_zip"

    rm -f \
        "$WEBSITE_DIR/animica-wallet-windows-x64.exe" \
        "$WEBSITE_DIR/animica-wallet-windows-x64.zip" \
        "$WEBSITE_DIR/animica-wallet-windows.sha256"

    install -m 0644 "$source_installer" "$WEBSITE_DIR/animica-wallet-windows-x64.exe"
    install -m 0644 "$source_zip" "$WEBSITE_DIR/animica-wallet-windows-x64.zip"

    write_checksum_file \
        "$WEBSITE_DIR/animica-wallet-windows.sha256" \
        "animica-wallet-windows-x64.exe" \
        "animica-wallet-windows-x64.zip"
}

publish_linux() {
    [ -n "$LINUX_APPIMAGE$LINUX_DEB$LINUX_TARBALL$LINUX_RAW" ] || fail "At least one Linux artifact is required"

    rm -f \
        "$WEBSITE_DIR/animica-wallet-linux.AppImage" \
        "$WEBSITE_DIR/animica-wallet-linux.deb" \
        "$WEBSITE_DIR/animica-wallet-linux.tar.gz" \
        "$WEBSITE_DIR/animica-wallet-linux.sha256" \
        "$WEBSITE_DIR/animica-wallet" \
        "$WEBSITE_DIR/animica-wallet.sha256"

    copy_if_requested "$LINUX_APPIMAGE" "$WEBSITE_DIR/animica-wallet-linux.AppImage"
    copy_if_requested "$LINUX_DEB" "$WEBSITE_DIR/animica-wallet-linux.deb"
    copy_if_requested "$LINUX_TARBALL" "$WEBSITE_DIR/animica-wallet-linux.tar.gz"
    copy_if_requested "$LINUX_RAW" "$WEBSITE_DIR/animica-wallet"

    local package_files=()
    [ -f "$WEBSITE_DIR/animica-wallet-linux.AppImage" ] && package_files+=("animica-wallet-linux.AppImage")
    [ -f "$WEBSITE_DIR/animica-wallet-linux.deb" ] && package_files+=("animica-wallet-linux.deb")
    [ -f "$WEBSITE_DIR/animica-wallet-linux.tar.gz" ] && package_files+=("animica-wallet-linux.tar.gz")

    write_checksum_file "$WEBSITE_DIR/animica-wallet-linux.sha256" "${package_files[@]}"

    if [ -f "$WEBSITE_DIR/animica-wallet" ]; then
        write_checksum_file "$WEBSITE_DIR/animica-wallet.sha256" "animica-wallet"
    fi
}

main() {
    parse_args "$@"
    mkdir -p "$WEBSITE_DIR"

    case "$PLATFORM" in
        windows)
            publish_windows
            ;;
        linux)
            publish_linux
            ;;
        *)
            fail "Unsupported platform: $PLATFORM"
            ;;
    esac

    "$SCRIPT_DIR/generate-wallet-manifest.py" \
        --website-dir "$WEBSITE_DIR" \
        --version "$VERSION"

    log "Published $PLATFORM wallet downloads into $WEBSITE_DIR"
    log "Manifest: $WEBSITE_DIR/manifest.json"
}

main "$@"
