#!/bin/bash
# sign-macos-bundle.sh - Deterministically sign AnimicaWallet.app on macOS.
#
# This script intentionally does NOT use `codesign --deep`.
# It signs nested code first, then signs the top-level app bundle.
#
# Usage examples:
#   ./scripts/sign-macos-bundle.sh --app /path/AnimicaWallet.app --adhoc-sign
#   ./scripts/sign-macos-bundle.sh --app /path/AnimicaWallet.app \
#       --identity "Developer ID Application: Example, Inc. (TEAMID)"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_BUNDLE=""
SIGN_MODE=""
SIGN_IDENTITY=""
ENTITLEMENTS_PATH="$WALLET_ROOT/resources/macos/entitlements.plist"
ENABLE_RUNTIME=true
ENABLE_TIMESTAMP=true

usage() {
    cat <<'EOF'
Usage:
  ./scripts/sign-macos-bundle.sh --app <AnimicaWallet.app> [--adhoc-sign | --identity <id>] [options]

Required:
  --app <path>               Path to AnimicaWallet.app
  --adhoc-sign               Use ad-hoc signing identity (-), OR
  --identity <value>         Use explicit signing identity (e.g. Developer ID Application: ...)

Optional:
  --entitlements <path>      Entitlements plist for top-level app signing
  --no-runtime               Do not pass --options runtime when signing with identity
  --no-timestamp             Do not pass --timestamp when signing with identity
  --help                     Show this help
EOF
}

log() {
    printf '[sign-macos-bundle] %s\n' "$1"
}

fail() {
    printf '[sign-macos-bundle][FAIL] %s\n' "$1" >&2
    exit 1
}

classify_codesign_failure() {
    local output="$1"
    if grep -qi "code object is not signed" <<<"$output"; then
        printf 'diagnostic: unsigned nested code object\n' >&2
    elif grep -qi "a sealed resource is missing or invalid" <<<"$output"; then
        printf 'diagnostic: sealed resources were modified after signing\n' >&2
    elif grep -qi "code has no resources but signature indicates they must be present" <<<"$output"; then
        printf 'diagnostic: malformed bundle signature metadata\n' >&2
    elif grep -qi "bundle format is ambiguous" <<<"$output"; then
        printf 'diagnostic: invalid bundle layout for signing\n' >&2
    elif grep -qi "invalid or unsupported format for signature" <<<"$output"; then
        printf 'diagnostic: malformed or unsupported Mach-O code object\n' >&2
    else
        printf 'diagnostic: codesign failed (see output above)\n' >&2
    fi
}

run_codesign() {
    local target="$1"
    shift

    local output
    if ! output=$(codesign "$@" "$target" 2>&1); then
        printf '[sign-macos-bundle][FAIL] codesign failed for %s\n' "$target" >&2
        printf '[sign-macos-bundle][FAIL] command: codesign' >&2
        printf ' %q' "$@" >&2
        printf ' %q\n' "$target" >&2
        printf '%s\n' "$output" >&2
        classify_codesign_failure "$output"
        exit 1
    fi
}

is_macho_file() {
    local path="$1"
    local descriptor
    descriptor="$(file -b "$path" 2>/dev/null || true)"
    [[ "$descriptor" == *"Mach-O"* ]]
}

sign_macho_files() {
    local sign_args=("$@")
    local -a candidates=()
    local -a macho_files=()

    # Sort DEEPEST-FIRST (by path-component count, descending). codesign requires
    # every nested code object to be signed before the code that contains it, so
    # the bundled Python venv's deep *.so (pydantic_core, watchfiles, …) must be
    # signed before the shallow Contents/MacOS/<main-exe>. Plain alphabetical sort
    # signed MacOS/AnimicaWallet first and failed with "unsigned nested code object".
    while IFS= read -r candidate; do
        candidates+=("$candidate")
    done < <(find "$APP_BUNDLE/Contents" -type f \( -perm -111 -o -name "*.dylib" -o -name "*.so" \) -print | awk -F/ '{print NF"\t"$0}' | LC_ALL=C sort -rn | cut -f2-)

    for candidate in "${candidates[@]}"; do
        if is_macho_file "$candidate"; then
            macho_files+=("$candidate")
        fi
    done

    if [ "${#macho_files[@]}" -eq 0 ]; then
        fail "No Mach-O files found under $APP_BUNDLE/Contents"
    fi

    for macho in "${macho_files[@]}"; do
        run_codesign "$macho" "${sign_args[@]}"
    done
    log "Signed ${#macho_files[@]} nested Mach-O file(s)"
}

sign_nested_bundles() {
    local sign_args=("$@")
    local -a bundle_targets=()

    while IFS= read -r bundle; do
        bundle_targets+=("$bundle")
    done < <(
        find "$APP_BUNDLE/Contents" -type d \
            \( -name "*.framework" -o -name "*.app" -o -name "*.xpc" -o -name "*.appex" -o -name "*.plugin" \) \
            -print \
            | awk -F'/' '{ print NF "\t" $0 }' \
            | LC_ALL=C sort -t $'\t' -k1,1nr -k2,2 \
            | cut -f2-
    )

    for bundle in "${bundle_targets[@]}"; do
        run_codesign "$bundle" "${sign_args[@]}"
    done

    log "Signed ${#bundle_targets[@]} nested bundle target(s)"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --app)
                [ "$#" -ge 2 ] || fail "--app requires a value"
                APP_BUNDLE="$2"
                shift 2
                ;;
            --adhoc-sign)
                SIGN_MODE="adhoc"
                SIGN_IDENTITY="-"
                shift
                ;;
            --identity)
                [ "$#" -ge 2 ] || fail "--identity requires a value"
                SIGN_MODE="identity"
                SIGN_IDENTITY="$2"
                shift 2
                ;;
            --entitlements)
                [ "$#" -ge 2 ] || fail "--entitlements requires a value"
                ENTITLEMENTS_PATH="$2"
                shift 2
                ;;
            --no-runtime)
                ENABLE_RUNTIME=false
                shift
                ;;
            --no-timestamp)
                ENABLE_TIMESTAMP=false
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
    [ -n "$SIGN_MODE" ] || fail "Choose exactly one signing mode: --adhoc-sign or --identity"
    command -v codesign >/dev/null 2>&1 || fail "codesign not found (run on macOS with Xcode command line tools)"
    command -v file >/dev/null 2>&1 || fail "file command not found"

    APP_BUNDLE="$(cd "$(dirname "$APP_BUNDLE")" && pwd)/$(basename "$APP_BUNDLE")"

    local -a nested_sign_args=(--force --sign "$SIGN_IDENTITY")
    local -a app_sign_args=(--force --sign "$SIGN_IDENTITY")

    if [ "$SIGN_MODE" = "identity" ]; then
        if [ "$ENABLE_RUNTIME" = true ]; then
            nested_sign_args+=(--options runtime)
            app_sign_args+=(--options runtime)
        fi
        if [ "$ENABLE_TIMESTAMP" = true ]; then
            nested_sign_args+=(--timestamp)
            app_sign_args+=(--timestamp)
        fi
        [ -f "$ENTITLEMENTS_PATH" ] || fail "Entitlements file not found: $ENTITLEMENTS_PATH"
        app_sign_args+=(--entitlements "$ENTITLEMENTS_PATH")
    fi

    log "Signing mode: $SIGN_MODE"
    log "Target app bundle: $APP_BUNDLE"

    sign_macho_files "${nested_sign_args[@]}"
    sign_nested_bundles "${nested_sign_args[@]}"
    run_codesign "$APP_BUNDLE" "${app_sign_args[@]}"

    log "Top-level app signing complete"
}

main "$@"
