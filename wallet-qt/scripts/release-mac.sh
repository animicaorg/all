#!/bin/bash
# release-mac.sh - Build, sign, verify, and package a native macOS Animica Wallet release.
#
# This flow is intentionally strict about signing order:
#   1) Build/stage .app
#   2) Sign nested code first (no codesign --deep)
#   3) Sign top-level .app
#   4) Verify codesign/spctl + architecture checks
#   5) Build DMG from a copied staging directory
#   6) Re-verify that the source .app remained valid after packaging
#
# Usage:
#   ./scripts/release-mac.sh [--dmg] [--sign|--adhoc-sign] [--notarize] [--debug] [--arch arm64|x86_64|universal2]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

BUILD_TYPE="Release"
CREATE_DMG=false
SIGN_APP=false
ADHOC_SIGN=false
NOTARIZE=false
ARCH_LABEL="$(uname -m)"
OUTPUT_DIR="$REPO_ROOT/dist/wallet-qt"
WEBSITE_DIR="$REPO_ROOT/website/public/wallet"

usage() {
    cat <<'EOF'
Usage: ./scripts/release-mac.sh [options]

Options:
  --dmg                   Build a DMG from the verified app bundle
  --sign                  Sign with Developer ID (requires CODESIGN_IDENTITY)
  --adhoc-sign            Sign with ad-hoc identity (-)
  --notarize              Notarize release artifact (requires --sign and Apple credentials)
  --debug                 Use Debug build (default: Release)
  --arch <value>          Build arch: arm64 | x86_64 | universal2 (default: host arch)
  --help                  Show this help

Environment for --sign:
  CODESIGN_IDENTITY       Example: Developer ID Application: Example Corp (TEAMID)

Environment for --notarize:
  APPLE_ID                Apple ID email
  APPLE_TEAM_ID           Apple team ID
  NOTARY_KEYCHAIN_PROFILE Optional keychain profile (default: AC_PASSWORD)
EOF
}

log() {
    printf '[release-mac] %s\n' "$1"
}

fail() {
    printf '[release-mac][FAIL] %s\n' "$1" >&2
    exit 1
}

run_checked() {
    local description="$1"
    shift
    log "$description"
    local output
    if ! output=$("$@" 2>&1); then
        printf '[release-mac][FAIL] command failed: %s\n' "$description" >&2
        printf '[release-mac][FAIL] command:' >&2
        printf ' %q' "$@" >&2
        printf '\n' >&2
        printf '%s\n' "$output" >&2
        exit 1
    fi
    if [ -n "$output" ]; then
        printf '%s\n' "$output"
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dmg)
                CREATE_DMG=true
                shift
                ;;
            --sign)
                SIGN_APP=true
                shift
                ;;
            --adhoc-sign)
                ADHOC_SIGN=true
                shift
                ;;
            --notarize)
                NOTARIZE=true
                SIGN_APP=true
                shift
                ;;
            --debug)
                BUILD_TYPE="Debug"
                shift
                ;;
            --arch)
                [ "$#" -ge 2 ] || fail "--arch requires a value"
                ARCH_LABEL="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                fail "Unknown option: $1"
                ;;
        esac
    done
}

resolve_cmake_arches() {
    case "$ARCH_LABEL" in
        arm64)
            printf '%s' 'arm64'
            ;;
        x86_64)
            printf '%s' 'x86_64'
            ;;
        universal2)
            printf '%s' 'arm64;x86_64'
            ;;
        *)
            fail "Unsupported --arch value: $ARCH_LABEL (expected arm64, x86_64, or universal2)"
            ;;
    esac
}

required_arch_for_verification() {
    if [ "$ARCH_LABEL" = "x86_64" ]; then
        printf '%s' 'x86_64'
    else
        printf '%s' 'arm64'
    fi
}

website_arch_label() {
    if [ "$ARCH_LABEL" = "universal2" ]; then
        printf '%s' 'universal'
    else
        printf '%s' "$ARCH_LABEL"
    fi
}

verify_release_app() {
    local app_bundle="$1"
    local required_arch="$2"
    local -a verify_args=(--app "$app_bundle" --require-arch "$required_arch")
    if [ "$SIGN_APP" = true ]; then
        verify_args+=(--require-spctl)
    fi
    run_checked "Validating signed app bundle" \
        "$SCRIPT_DIR/verify-macos-bundle.sh" "${verify_args[@]}"
}

verify_dmg_payload() {
    local dmg_path="$1"
    local required_arch="$2"
    local mountpoint="$3"

    rm -rf "$mountpoint"
    mkdir -p "$mountpoint"

    run_checked "Mounting DMG for payload verification" \
        hdiutil attach "$dmg_path" -nobrowse -readonly -mountpoint "$mountpoint"

    local mounted_app="$mountpoint/AnimicaWallet.app"
    [ -d "$mounted_app" ] || fail "DMG payload missing AnimicaWallet.app at $mounted_app"

    run_checked "Validating app inside mounted DMG" \
        "$SCRIPT_DIR/verify-macos-bundle.sh" --app "$mounted_app" --require-arch "$required_arch"

    run_checked "Detaching DMG mount" hdiutil detach "$mountpoint"
    rmdir "$mountpoint" 2>/dev/null || true
}

main() {
    parse_args "$@"

    [ "$(uname -s)" = "Darwin" ] || fail "This script must run on macOS"
    command -v cmake >/dev/null 2>&1 || fail "cmake not found"
    command -v python3 >/dev/null 2>&1 || fail "python3 not found"
    command -v ditto >/dev/null 2>&1 || fail "ditto not found"
    command -v shasum >/dev/null 2>&1 || fail "shasum not found"
    if [ "$CREATE_DMG" = true ]; then
        command -v hdiutil >/dev/null 2>&1 || fail "hdiutil not found"
    fi

    if [ "$SIGN_APP" = true ] && [ "$ADHOC_SIGN" = true ]; then
        fail "Choose either --sign or --adhoc-sign, not both"
    fi

    if [ "$NOTARIZE" = true ] && [ "$SIGN_APP" = false ]; then
        fail "--notarize requires --sign (Developer ID)"
    fi

    # Safe default for local release iterations: never ship an unsigned app by accident.
    if [ "$SIGN_APP" = false ] && [ "$ADHOC_SIGN" = false ]; then
        log "No signing flag provided; defaulting to --adhoc-sign."
        ADHOC_SIGN=true
    fi

    local cmake_arches
    cmake_arches="$(resolve_cmake_arches)"
    local required_arch
    required_arch="$(required_arch_for_verification)"

    local build_dir="$WALLET_ROOT/build/mac-release"
    local install_dir="$build_dir/stage"
    local dmg_stage_dir="$build_dir/dmg-stage"
    local dmg_mount_dir="$build_dir/dmg-mount"

    cd "$REPO_ROOT"
    local version
    if git describe --tags --exact-match >/dev/null 2>&1; then
        version="$(git describe --tags --exact-match)"
    else
        local base_version
        base_version="$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.1.1")"
        version="${base_version}-$(git rev-parse --short HEAD)"
    fi

    log "======================================"
    log "Animica Wallet - macOS Release Build"
    log "======================================"
    log "Build type: $BUILD_TYPE"
    log "Requested arch: $ARCH_LABEL"
    log "CMake arches: $cmake_arches"
    log "Required verify arch: $required_arch"
    log "Version: $version"
    log "Create DMG: $CREATE_DMG"
    log "Developer ID signing: $SIGN_APP"
    log "Ad-hoc signing: $ADHOC_SIGN"
    log "Notarization: $NOTARIZE"

    python3 "$WALLET_ROOT/scripts/gen-icons.py" --check >/dev/null 2>&1 || python3 "$WALLET_ROOT/scripts/gen-icons.py"

    rm -rf "$build_dir"
    mkdir -p "$build_dir"

    if [ -z "${CMAKE_PREFIX_PATH:-}" ]; then
        for candidate in /opt/homebrew/opt/qt@6 /usr/local/opt/qt@6 /opt/homebrew/opt/qt /usr/local/opt/qt; do
            if [ -d "$candidate" ]; then
                export CMAKE_PREFIX_PATH="$candidate"
                break
            fi
        done
    fi

    run_checked "Configuring CMake project" \
        cmake -S "$WALLET_ROOT" -B "$build_dir" \
            -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
            -DCMAKE_OSX_ARCHITECTURES="$cmake_arches" \
            -DWALLET_BUNDLE_PYTHON_RUNTIME=ON \
            -DBUILD_TESTING=OFF

    run_checked "Building wallet app bundle" \
        cmake --build "$build_dir" --config "$BUILD_TYPE" -j"$(sysctl -n hw.ncpu)"

    rm -rf "$install_dir"
    mkdir -p "$install_dir"
    run_checked "Installing staged app bundle" \
        cmake --install "$build_dir" --config "$BUILD_TYPE" --prefix "$install_dir"

    local staged_app="$install_dir/AnimicaWallet.app"
    run_checked "Verifying staged bundle layout" \
        python3 "$SCRIPT_DIR/verify-bundle-layout.py" --platform macos --path "$staged_app"

    local dist_dir="$OUTPUT_DIR/$version/macos"
    mkdir -p "$dist_dir"

    local release_app="$dist_dir/AnimicaWallet-${version}-macos-${ARCH_LABEL}.app"
    rm -rf "$release_app"
    run_checked "Copying staged app to release location (ditto)" \
        ditto "$staged_app" "$release_app"

    if [ "$ADHOC_SIGN" = true ]; then
        run_checked "Signing app with ad-hoc identity (nested first)" \
            "$SCRIPT_DIR/sign-macos-bundle.sh" --app "$release_app" --adhoc-sign
    else
        [ -n "${CODESIGN_IDENTITY:-}" ] || fail "CODESIGN_IDENTITY is required for --sign"
        run_checked "Signing app with Developer ID identity (nested first)" \
            "$SCRIPT_DIR/sign-macos-bundle.sh" --app "$release_app" --identity "$CODESIGN_IDENTITY"
    fi

    verify_release_app "$release_app" "$required_arch"

    local app_zip="$dist_dir/AnimicaWallet-${version}-macos-${ARCH_LABEL}.zip"
    rm -f "$app_zip"
    run_checked "Creating ZIP archive of signed app" \
        ditto -c -k --sequesterRsrc --keepParent "$release_app" "$app_zip"

    local dmg_path=""
    if [ "$CREATE_DMG" = true ]; then
        dmg_path="$dist_dir/AnimicaWallet-${version}-macos-${ARCH_LABEL}.dmg"
        rm -f "$dmg_path"
        rm -rf "$dmg_stage_dir"
        mkdir -p "$dmg_stage_dir"

        # Build DMG from an isolated copy so tooling cannot mutate the signed source bundle.
        run_checked "Preparing isolated DMG staging directory" \
            ditto "$release_app" "$dmg_stage_dir/AnimicaWallet.app"
        ln -sfn /Applications "$dmg_stage_dir/Applications"

        if command -v create-dmg >/dev/null 2>&1; then
            run_checked "Creating DMG via create-dmg" \
                create-dmg \
                    --volname "Animica Wallet" \
                    --volicon "$WALLET_ROOT/resources/icons/animica.icns" \
                    --window-pos 200 120 \
                    --window-size 640 420 \
                    --icon-size 100 \
                    --icon "AnimicaWallet.app" 180 150 \
                    --hide-extension "AnimicaWallet.app" \
                    --app-drop-link 460 150 \
                    "$dmg_path" \
                    "$dmg_stage_dir"
        else
            run_checked "Creating DMG via hdiutil" \
                hdiutil create -volname "Animica Wallet" \
                    -srcfolder "$dmg_stage_dir" \
                    -ov -format UDZO \
                    "$dmg_path"
        fi

        # Ensure the original signed release bundle is still valid after DMG tooling runs.
        verify_release_app "$release_app" "$required_arch"
        verify_dmg_payload "$dmg_path" "$required_arch" "$dmg_mount_dir"
    fi

    if [ "$NOTARIZE" = true ]; then
        [ -n "${APPLE_ID:-}" ] || fail "APPLE_ID is required for notarization"
        [ -n "${APPLE_TEAM_ID:-}" ] || fail "APPLE_TEAM_ID is required for notarization"
        local notary_profile="${NOTARY_KEYCHAIN_PROFILE:-AC_PASSWORD}"

        if [ "$CREATE_DMG" = true ]; then
            [ -n "$dmg_path" ] || fail "Internal error: DMG path missing"
            run_checked "Submitting DMG for notarization" \
                xcrun notarytool submit "$dmg_path" \
                    --apple-id "$APPLE_ID" \
                    --team-id "$APPLE_TEAM_ID" \
                    --password "@keychain:${notary_profile}" \
                    --wait
            run_checked "Stapling notarization ticket to DMG" xcrun stapler staple "$dmg_path"
            run_checked "Assessing notarized DMG" spctl --assess --type open --verbose=4 "$dmg_path"
        else
            run_checked "Submitting app ZIP for notarization" \
                xcrun notarytool submit "$app_zip" \
                    --apple-id "$APPLE_ID" \
                    --team-id "$APPLE_TEAM_ID" \
                    --password "@keychain:${notary_profile}" \
                    --wait
            run_checked "Stapling notarization ticket to app bundle" xcrun stapler staple "$release_app"
            verify_release_app "$release_app" "$required_arch"
        fi
    fi

    (
        cd "$dist_dir"
        : > SHA256SUMS
        shasum -a 256 "$(basename "$app_zip")" >> SHA256SUMS
        if [ -n "$dmg_path" ]; then
            shasum -a 256 "$(basename "$dmg_path")" >> SHA256SUMS
        fi
    )

    if [ -d "$REPO_ROOT/website/public" ]; then
        local -a publish_args=(
            --platform macos
            --version "$version"
            --website-dir "$WEBSITE_DIR"
            --architecture "$(website_arch_label)"
            --zip "$app_zip"
        )
        if [ -n "$dmg_path" ]; then
            publish_args+=(--dmg "$dmg_path")
        fi
        run_checked "Refreshing website downloads" \
            "$SCRIPT_DIR/publish-wallet-downloads.sh" "${publish_args[@]}"
    fi

    log ""
    log "======================================"
    log "Release Build Complete"
    log "======================================"
    log "Signed app: $release_app"
    log "ZIP: $app_zip"
    if [ -n "$dmg_path" ]; then
        log "DMG: $dmg_path"
    fi
    log "Checksums: $dist_dir/SHA256SUMS"
    log "Smoke test: $SCRIPT_DIR/smoke-test-mac.sh \"$release_app\""
}

main "$@"
