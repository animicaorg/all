#!/bin/bash
# verify-macos-bundle.sh - Validate macOS bundle signing/integrity for AnimicaWallet.app.
#
# Runs strict checks intended to catch "Code Signature Invalid" launch failures:
# - codesign verification (deep + strict)
# - optional Gatekeeper assessment (spctl)
# - file/lipo/otool inspection
# - per-binary nested Mach-O signature verification
# - architecture compatibility checks (arm64 by default)
#
# Usage examples:
#   ./scripts/verify-macos-bundle.sh --app /path/AnimicaWallet.app
#   ./scripts/verify-macos-bundle.sh --app /path/AnimicaWallet.app --require-spctl

set -euo pipefail

APP_BUNDLE=""
MAIN_EXECUTABLE_NAME="AnimicaWallet"
REQUIRE_ARCH="arm64"
REQUIRE_SPCTL=false

usage() {
    cat <<'EOF'
Usage:
  ./scripts/verify-macos-bundle.sh --app <AnimicaWallet.app> [options]

Required:
  --app <path>               Path to AnimicaWallet.app

Optional:
  --main-executable <name>   Main executable name (default: AnimicaWallet)
  --require-arch <arch>      Require this architecture in every Mach-O (default: arm64)
  --require-spctl            Fail if `spctl --assess` fails
  --help                     Show this help
EOF
}

log() {
    printf '[verify-macos-bundle] %s\n' "$1"
}

fail() {
    printf '[verify-macos-bundle][FAIL] %s\n' "$1" >&2
    exit 1
}

classify_codesign_failure() {
    local output="$1"
    if grep -qi "code object is not signed" <<<"$output"; then
        printf 'diagnostic: unsigned code object\n' >&2
    elif grep -qi "a sealed resource is missing or invalid" <<<"$output"; then
        printf 'diagnostic: bundle was modified after signing (sealed resources mismatch)\n' >&2
    elif grep -qi "not valid for use in process" <<<"$output"; then
        printf 'diagnostic: invalid signature for code-loading process\n' >&2
    elif grep -qi "code directory hash in" <<<"$output"; then
        printf 'diagnostic: signature hash mismatch / corrupted Mach-O pages\n' >&2
    else
        printf 'diagnostic: signature verification failed (see output above)\n' >&2
    fi
}

run_checked() {
    local description="$1"
    shift
    log "$description"
    local output
    if ! output=$("$@" 2>&1); then
        printf '[verify-macos-bundle][FAIL] command failed: %s\n' "$description" >&2
        printf '[verify-macos-bundle][FAIL] command:' >&2
        printf ' %q' "$@" >&2
        printf '\n' >&2
        printf '%s\n' "$output" >&2
        if [ "$1" = "codesign" ]; then
            classify_codesign_failure "$output"
        fi
        exit 1
    fi
    if [ -n "$output" ]; then
        printf '%s\n' "$output"
    fi
}

run_spctl_check() {
    if ! command -v spctl >/dev/null 2>&1; then
        if [ "$REQUIRE_SPCTL" = true ]; then
            fail "spctl is required but not found"
        fi
        log "spctl not available; skipping Gatekeeper assessment"
        return
    fi

    local output
    if output=$(spctl --assess --type execute --verbose=4 "$APP_BUNDLE" 2>&1); then
        printf '%s\n' "$output"
        return
    fi

    if [ "$REQUIRE_SPCTL" = true ]; then
        printf '[verify-macos-bundle][FAIL] Gatekeeper assessment failed\n' >&2
        printf '[verify-macos-bundle][FAIL] command: spctl --assess --type execute --verbose=4 %q\n' "$APP_BUNDLE" >&2
        printf '%s\n' "$output" >&2
        exit 1
    fi

    log "spctl assessment failed (ignored in this mode):"
    printf '%s\n' "$output"
}

is_macho_file() {
    local path="$1"
    local descriptor
    descriptor="$(file -b "$path" 2>/dev/null || true)"
    [[ "$descriptor" == *"Mach-O"* ]]
}

validate_required_arch() {
    local target="$1"
    local lipo_output
    if ! lipo_output="$(lipo -info "$target" 2>&1)"; then
        printf '[verify-macos-bundle][FAIL] could not inspect architecture for %s\n' "$target" >&2
        printf '%s\n' "$lipo_output" >&2
        printf 'diagnostic: malformed Mach-O or unreadable binary\n' >&2
        exit 1
    fi

    if ! grep -qw "$REQUIRE_ARCH" <<<"$lipo_output"; then
        printf '[verify-macos-bundle][FAIL] architecture mismatch for %s\n' "$target" >&2
        printf '[verify-macos-bundle][FAIL] required architecture: %s\n' "$REQUIRE_ARCH" >&2
        printf '[verify-macos-bundle][FAIL] lipo output: %s\n' "$lipo_output" >&2
        printf 'diagnostic: binary missing required architecture slice\n' >&2
        exit 1
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --app)
                [ "$#" -ge 2 ] || fail "--app requires a value"
                APP_BUNDLE="$2"
                shift 2
                ;;
            --main-executable)
                [ "$#" -ge 2 ] || fail "--main-executable requires a value"
                MAIN_EXECUTABLE_NAME="$2"
                shift 2
                ;;
            --require-arch)
                [ "$#" -ge 2 ] || fail "--require-arch requires a value"
                REQUIRE_ARCH="$2"
                shift 2
                ;;
            --require-spctl)
                REQUIRE_SPCTL=true
                shift
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

main() {
    parse_args "$@"

    [ -n "$APP_BUNDLE" ] || fail "--app is required"
    [ -d "$APP_BUNDLE" ] || fail "App bundle not found: $APP_BUNDLE"
    command -v codesign >/dev/null 2>&1 || fail "codesign not found"
    command -v file >/dev/null 2>&1 || fail "file not found"
    command -v lipo >/dev/null 2>&1 || fail "lipo not found"
    command -v otool >/dev/null 2>&1 || fail "otool not found"

    APP_BUNDLE="$(cd "$(dirname "$APP_BUNDLE")" && pwd)/$(basename "$APP_BUNDLE")"
    local main_executable="$APP_BUNDLE/Contents/MacOS/$MAIN_EXECUTABLE_NAME"
    [ -f "$main_executable" ] || fail "Main executable not found: $main_executable"

    run_checked "Top-level bundle signature verification" \
        codesign --verify --deep --strict --verbose=4 "$APP_BUNDLE"

    run_spctl_check

    run_checked "Main executable format (file)" file "$main_executable"
    run_checked "Main executable architectures (lipo)" lipo -info "$main_executable"
    run_checked "Main executable linked libraries (otool -L)" otool -L "$main_executable"

    validate_required_arch "$main_executable"

    local -a macho_files=()
    while IFS= read -r candidate; do
        if is_macho_file "$candidate"; then
            macho_files+=("$candidate")
        fi
    done < <(find "$APP_BUNDLE" -type f \( -perm -111 -o -name "*.dylib" -o -name "*.so" \) -print | LC_ALL=C sort)

    [ "${#macho_files[@]}" -gt 0 ] || fail "No nested Mach-O files found in $APP_BUNDLE"

    for macho in "${macho_files[@]}"; do
        run_checked "Verify nested signature: $macho" \
            codesign --verify --strict --verbose=2 "$macho"
        validate_required_arch "$macho"
    done

    log "Verified ${#macho_files[@]} nested Mach-O file(s)"
    log "Bundle verification succeeded for $APP_BUNDLE"
}

main "$@"
