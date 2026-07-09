#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WALLET_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$WALLET_ROOT/.." && pwd)"

BUILD_DIR="$WALLET_ROOT/build/windows-cross"
STAGE_DIR="$BUILD_DIR/stage"
DIST_DIR="$WALLET_ROOT/dist/windows"
TOOLCHAIN_FILE="$WALLET_ROOT/cmake/toolchains/mingw64.cmake"

QT_ROOT="${WINDOWS_QT_ROOT:-${QT_WINDOWS_ROOT:-}}"
QT_HOST_PATH="${WINDOWS_QT_HOST_PATH:-${QT_HOST_PATH:-}}"
OPENSSL_ROOT="${WINDOWS_OPENSSL_ROOT:-}"
COMPILER_PREFIX="${WINDOWS_COMPILER_PREFIX:-x86_64-w64-mingw32}"

BUILD_TYPE="Release"
JOBS="$(nproc 2>/dev/null || echo 4)"
CLEAN=false
CHECK_ONLY=false
INSTALL_SCOPE="per-user"

ARCH_LABEL="x86_64"
DIST_INSTALLER_NAME="animica-wallet-setup-x64.exe"
DIST_ZIP_NAME="animica-wallet-windows-x64.zip"
DIST_CHECKSUM_NAME="SHA256SUMS.txt"
STAGE_RUNTIME_MODE="hosted-rpc"

usage() {
    cat <<'EOF'
Usage: ./scripts/build-windows-cross.sh [options]

Cross-compile wallet-qt for Windows from Linux, deploy the runtime, and produce:
  wallet-qt/dist/windows/animica-wallet-setup-x64.exe
  wallet-qt/dist/windows/animica-wallet-windows-x64.zip
  wallet-qt/dist/windows/SHA256SUMS.txt

Options:
  --clean                   Remove the previous windows-cross build directory first
  --debug                   Build Debug artifacts instead of Release
  --check                   Validate prerequisites only, then exit
  --jobs <n>                Parallel build jobs
  --qt-root <path>          Windows-target Qt prefix (required unless WINDOWS_QT_ROOT is set)
  --qt-host-path <path>     Host Qt prefix used for moc/rcc/uic
  --openssl-root <path>     Windows-target OpenSSL prefix (required unless WINDOWS_OPENSSL_ROOT is set)
  --compiler-prefix <name>  MinGW compiler prefix (default: x86_64-w64-mingw32)
  --per-machine             Generate a per-machine installer (admin/UAC)
  --help                    Show this help
EOF
}

log() {
    printf '[windows-cross] %s\n' "$1"
}

fail() {
    printf 'build-windows-cross.sh: %s\n' "$1" >&2
    exit 1
}

canonicalize_dir() {
    local path="$1"
    [ -n "$path" ] || return 1
    [ -d "$path" ] || return 1
    (
        cd "$path"
        pwd
    )
}

resolve_version() {
    if git -C "$REPO_ROOT" describe --tags --exact-match >/dev/null 2>&1; then
        git -C "$REPO_ROOT" describe --tags --exact-match
        return
    fi

    local base_version
    base_version="$(git -C "$REPO_ROOT" describe --tags --abbrev=0 2>/dev/null || printf 'v0.1.1')"
    printf '%s-%s\n' "$base_version" "$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
}

version_to_dotted() {
    local version="${1#v}"
    version="${version%%-*}"
    local major minor patch
    IFS=. read -r major minor patch <<EOF
$version
EOF
    printf '%s.%s.%s.0\n' "${major:-0}" "${minor:-0}" "${patch:-0}"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --clean)
                CLEAN=true
                shift
                ;;
            --debug)
                BUILD_TYPE="Debug"
                shift
                ;;
            --check)
                CHECK_ONLY=true
                shift
                ;;
            --jobs)
                [ "$#" -ge 2 ] || fail "--jobs requires a value"
                JOBS="$2"
                shift 2
                ;;
            --qt-root)
                [ "$#" -ge 2 ] || fail "--qt-root requires a value"
                QT_ROOT="$2"
                shift 2
                ;;
            --qt-host-path)
                [ "$#" -ge 2 ] || fail "--qt-host-path requires a value"
                QT_HOST_PATH="$2"
                shift 2
                ;;
            --openssl-root)
                [ "$#" -ge 2 ] || fail "--openssl-root requires a value"
                OPENSSL_ROOT="$2"
                shift 2
                ;;
            --compiler-prefix)
                [ "$#" -ge 2 ] || fail "--compiler-prefix requires a value"
                COMPILER_PREFIX="$2"
                shift 2
                ;;
            --per-machine)
                INSTALL_SCOPE="per-machine"
                shift
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
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$2"
}

find_qt_host_path() {
    if [ -n "$QT_HOST_PATH" ] && canonicalize_dir "$QT_HOST_PATH" >/dev/null 2>&1; then
        canonicalize_dir "$QT_HOST_PATH"
        return
    fi

    if command -v qtpaths6 >/dev/null 2>&1; then
        local candidate
        candidate="$(qtpaths6 --query QT_INSTALL_PREFIX 2>/dev/null || true)"
        if canonicalize_dir "$candidate" >/dev/null 2>&1; then
            canonicalize_dir "$candidate"
            return
        fi
    fi

    if command -v qmake6 >/dev/null 2>&1; then
        local candidate
        candidate="$(qmake6 -query QT_INSTALL_PREFIX 2>/dev/null || true)"
        if canonicalize_dir "$candidate" >/dev/null 2>&1; then
            canonicalize_dir "$candidate"
            return
        fi
    fi

    fail "Qt host tools were not found. Install qt6-base-dev qt6-base-dev-tools, or pass --qt-host-path."
}

validate_qt_root() {
    local root
    root="$(canonicalize_dir "$QT_ROOT" 2>/dev/null || true)"
    [ -n "$root" ] || fail "Windows Qt root is required. Pass --qt-root or set WINDOWS_QT_ROOT."
    [ -f "$root/lib/cmake/Qt6/Qt6Config.cmake" ] || fail "Qt6Config.cmake not found under $root/lib/cmake/Qt6"
    [ -d "$root/bin" ] || fail "Qt Windows runtime bin/ directory not found under $root"
    [ -d "$root/plugins" ] || fail "Qt Windows plugins/ directory not found under $root"
    QT_ROOT="$root"
}

find_matching_file() {
    local root="$1"
    shift
    local pattern
    for pattern in "$@"; do
        local match
        match="$(find "$root" -type f -iname "$pattern" -print | LC_ALL=C sort | head -n 1)"
        if [ -n "$match" ]; then
            printf '%s\n' "$match"
            return 0
        fi
    done
    return 1
}

validate_openssl_root() {
    local root
    root="$(canonicalize_dir "$OPENSSL_ROOT" 2>/dev/null || true)"
    [ -n "$root" ] || fail "Windows OpenSSL root is required. Pass --openssl-root or set WINDOWS_OPENSSL_ROOT."
    [ -f "$root/include/openssl/ssl.h" ] || fail "OpenSSL headers not found under $root/include/openssl"
    find_matching_file "$root" 'libssl*.dll.a' 'libssl*.a' >/dev/null 2>&1 || fail "OpenSSL SSL import libraries not found under $root"
    find_matching_file "$root" 'libcrypto*.dll.a' 'libcrypto*.a' >/dev/null 2>&1 || fail "OpenSSL Crypto import libraries not found under $root"
    find_matching_file "$root" 'libssl*.dll' >/dev/null 2>&1 || fail "OpenSSL SSL runtime DLLs not found under $root"
    find_matching_file "$root" 'libcrypto*.dll' >/dev/null 2>&1 || fail "OpenSSL Crypto runtime DLLs not found under $root"
    OPENSSL_ROOT="$root"
}

runtime_file_from_compiler() {
    local compiler="$1"
    local filename="$2"
    local resolved
    resolved="$("$compiler" -print-file-name="$filename")"
    if [ -n "$resolved" ] && [ "$resolved" != "$filename" ] && [ -f "$resolved" ]; then
        printf '%s\n' "$resolved"
    fi
}

check_prerequisites() {
    require_command cmake "CMake 3.16+ is required."
    require_command ninja "ninja-build is required."
    require_command zip "zip is required."
    require_command sha256sum "sha256sum is required."
    require_command sed "sed is required."
    require_command find "find is required."
    require_command "${COMPILER_PREFIX}-g++" "MinGW C++ compiler ${COMPILER_PREFIX}-g++ was not found. Install g++-mingw-w64-x86-64."
    require_command "${COMPILER_PREFIX}-gcc" "MinGW C compiler ${COMPILER_PREFIX}-gcc was not found. Install gcc-mingw-w64-x86-64."
    require_command "${COMPILER_PREFIX}-windres" "MinGW windres ${COMPILER_PREFIX}-windres was not found. Install binutils-mingw-w64-x86-64."
    require_command "${COMPILER_PREFIX}-objdump" "MinGW objdump ${COMPILER_PREFIX}-objdump was not found. Install binutils-mingw-w64-x86-64."
    require_command makensis "makensis is required to build the Windows installer. Install the nsis package."

    [ -f "$TOOLCHAIN_FILE" ] || fail "Toolchain file not found at $TOOLCHAIN_FILE"

    QT_HOST_PATH="$(find_qt_host_path)"
    validate_qt_root
    validate_openssl_root

}

copy_required_file() {
    local source_path="$1"
    local destination_path="$2"
    [ -f "$source_path" ] || fail "Required runtime file not found: $source_path"
    install -m 0644 "$source_path" "$destination_path"
}

copy_optional_file() {
    local source_path="$1"
    local destination_path="$2"
    if [ -f "$source_path" ]; then
        install -m 0644 "$source_path" "$destination_path"
    fi
}

copy_plugin_dir() {
    local source_dir="$1"
    local destination_dir="$2"
    local required="$3"

    if [ ! -d "$source_dir" ]; then
        if [ "$required" = "required" ]; then
            fail "Required Qt plugin directory not found: $source_dir"
        fi
        return
    fi

    mkdir -p "$destination_dir"
    find "$source_dir" -maxdepth 1 -type f -name '*.dll' -print | LC_ALL=C sort | while IFS= read -r dll; do
        install -m 0644 "$dll" "$destination_dir/$(basename "$dll")"
    done
}

create_nsis_file_lists() {
    local install_include="$1"
    local uninstall_include="$2"
    local last_dir=""

    : > "$install_include"
    : > "$uninstall_include"

    while IFS= read -r relative_path; do
        local relative_dir
        relative_dir="$(dirname "$relative_path")"
        local nsis_dir="$relative_dir"
        nsis_dir="${nsis_dir//\//\\}"

        if [ "$relative_dir" = "." ]; then
            if [ "$last_dir" != "." ]; then
                printf '  SetOutPath "$INSTDIR"\n' >> "$install_include"
                last_dir="."
            fi
        elif [ "$relative_dir" != "$last_dir" ]; then
            printf '  SetOutPath "$INSTDIR\\%s"\n' "$nsis_dir" >> "$install_include"
            last_dir="$relative_dir"
        fi

        printf '  File "/oname=%s" "%s/%s"\n' "$(basename "$relative_path")" "$STAGE_DIR" "$relative_path" >> "$install_include"
    done < <(cd "$STAGE_DIR" && find . -type f ! -name 'uninstall.exe' -printf '%P\n' | LC_ALL=C sort)

    while IFS= read -r relative_path; do
        printf '  Delete "$INSTDIR\\%s"\n' "${relative_path//\//\\}" >> "$uninstall_include"
    done < <(cd "$STAGE_DIR" && find . -type f ! -name 'uninstall.exe' -printf '%P\n' | LC_ALL=C sort -r)

    while IFS= read -r relative_dir; do
        [ -n "$relative_dir" ] || continue
        printf '  RMDir "$INSTDIR\\%s"\n' "${relative_dir//\//\\}" >> "$uninstall_include"
    done < <(cd "$STAGE_DIR" && find . -depth -type d -printf '%P\n' | LC_ALL=C sort -r)
}

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'
}

render_nsis_script() {
    local template_path="$WALLET_ROOT/resources/windows/installer.nsi.in"
    local output_path="$1"
    local install_include="$2"
    local uninstall_include="$3"
    local version="$4"

    local install_dir reg_root shell_var_context request_execution_level uninstall_reg_key
    if [ "$INSTALL_SCOPE" = "per-machine" ]; then
        install_dir='$PROGRAMFILES64\Animica Wallet'
        reg_root='HKLM'
        shell_var_context='all'
        request_execution_level='admin'
        uninstall_reg_key='Software\Microsoft\Windows\CurrentVersion\Uninstall\AnimicaWallet'
    else
        install_dir='$LOCALAPPDATA\Programs\Animica Wallet'
        reg_root='HKCU'
        shell_var_context='current'
        request_execution_level='user'
        uninstall_reg_key='Software\Microsoft\Windows\CurrentVersion\Uninstall\AnimicaWallet'
    fi

    sed \
        -e "s|@OUTPUT_FILE@|$(escape_sed_replacement "$DIST_DIR/$DIST_INSTALLER_NAME")|g" \
        -e "s|@INSTALL_DIR@|$(escape_sed_replacement "$install_dir")|g" \
        -e "s|@REG_ROOT@|$(escape_sed_replacement "$reg_root")|g" \
        -e "s|@REQUEST_EXECUTION_LEVEL@|$(escape_sed_replacement "$request_execution_level")|g" \
        -e "s|@SHELL_VAR_CONTEXT@|$(escape_sed_replacement "$shell_var_context")|g" \
        -e "s|@UNINSTALL_REG_KEY@|$(escape_sed_replacement "$uninstall_reg_key")|g" \
        -e "s|@ICON_FILE@|$(escape_sed_replacement "$WALLET_ROOT/resources/icons/animica.ico")|g" \
        -e "s|@DISPLAY_VERSION@|$(escape_sed_replacement "$version")|g" \
        -e "s|@VERSION_DOTTED@|$(escape_sed_replacement "$(version_to_dotted "$version")")|g" \
        -e "s|@INSTALL_FILES_INCLUDE@|$(escape_sed_replacement "$install_include")|g" \
        -e "s|@UNINSTALL_FILES_INCLUDE@|$(escape_sed_replacement "$uninstall_include")|g" \
        "$template_path" > "$output_path"
}

deploy_runtime_dependencies() {
    local qt_bin_dir="$QT_ROOT/bin"
    local qt_plugins_dir="$QT_ROOT/plugins"
    local runtime_dll
    local runtime_dir
    runtime_dll="$(runtime_file_from_compiler "${COMPILER_PREFIX}-g++" libstdc++-6.dll || true)"
    [ -n "$runtime_dll" ] || fail "Could not locate libstdc++-6.dll via ${COMPILER_PREFIX}-g++"
    runtime_dir="$(dirname "$runtime_dll")"

    copy_plugin_dir "$qt_plugins_dir/platforms" "$STAGE_DIR/platforms" required
    copy_plugin_dir "$qt_plugins_dir/sqldrivers" "$STAGE_DIR/sqldrivers" required
    copy_plugin_dir "$qt_plugins_dir/imageformats" "$STAGE_DIR/imageformats" optional
    copy_plugin_dir "$qt_plugins_dir/styles" "$STAGE_DIR/styles" optional
    copy_plugin_dir "$qt_plugins_dir/tls" "$STAGE_DIR/tls" optional
    copy_plugin_dir "$qt_plugins_dir/networkinformation" "$STAGE_DIR/networkinformation" optional

    copy_optional_file "$qt_bin_dir/opengl32sw.dll" "$STAGE_DIR/opengl32sw.dll"
    copy_optional_file "$qt_bin_dir/d3dcompiler_47.dll" "$STAGE_DIR/d3dcompiler_47.dll"

    local openssl_bin_dir="$OPENSSL_ROOT/bin"
    local openssl_lib_dir="$OPENSSL_ROOT/lib"

    local search_dirs=(
        "$qt_bin_dir"
        "$runtime_dir"
        "$openssl_bin_dir"
        "$openssl_lib_dir"
    )

    declare -A system_dlls=(
        [advapi32.dll]=1
        [authz.dll]=1
        [bcrypt.dll]=1
        [cfgmgr32.dll]=1
        [combase.dll]=1
        [comctl32.dll]=1
        [comdlg32.dll]=1
        [crypt32.dll]=1
        [d2d1.dll]=1
        [d3d11.dll]=1
        [d3d9.dll]=1
        [dnsapi.dll]=1
        [dwmapi.dll]=1
        [dwrite.dll]=1
        [dxgi.dll]=1
        [gdi32.dll]=1
        [imagehlp.dll]=1
        [imm32.dll]=1
        [iphlpapi.dll]=1
        [kernel32.dll]=1
        [libpq.dll]=1
        [mimapi64.dll]=1
        [mpr.dll]=1
        [msimg32.dll]=1
        [msvcrt.dll]=1
        [ncrypt.dll]=1
        [netapi32.dll]=1
        [odbc32.dll]=1
        [ole32.dll]=1
        [oleaut32.dll]=1
        [rpcrt4.dll]=1
        [sechost.dll]=1
        [secur32.dll]=1
        [setupapi.dll]=1
        [shcore.dll]=1
        [shell32.dll]=1
        [shlwapi.dll]=1
        [user32.dll]=1
        [userenv.dll]=1
        [uxtheme.dll]=1
        [version.dll]=1
        [winhttp.dll]=1
        [winmm.dll]=1
        [ws2_32.dll]=1
        [wtsapi32.dll]=1
        [api-ms-win-core-synch-l1-2-0.dll]=1
        [api-ms-win-core-winrt-l1-1-0.dll]=1
        [api-ms-win-core-winrt-string-l1-1-0.dll]=1
    )

    local queue=()
    while IFS= read -r binary; do
        queue+=("$binary")
    done < <(find "$STAGE_DIR" -type f \( -iname '*.exe' -o -iname '*.dll' \) | LC_ALL=C sort)

    local unresolved=()
    declare -A queued=()
    for binary in "${queue[@]}"; do
        queued["$binary"]=1
    done

    local index=0
    while [ "$index" -lt "${#queue[@]}" ]; do
        local current="${queue[$index]}"
        index=$((index + 1))

        while IFS= read -r dependency; do
            [ -n "$dependency" ] || continue
            local dependency_lower="${dependency,,}"

            # MSVC/UCRT runtime DLLs pulled in by the prebuilt (MSVC-built) OpenSSL:
            # api-ms-win-* and ucrtbase are part of the Windows 10/11 Universal CRT;
            # vcruntime140/msvcp140 ship with the (near-universal) VC++ redist. None
            # are bundleable from a Linux cross-build, so treat them as system DLLs.
            case "$dependency_lower" in
                api-ms-win-*|ucrtbase.dll|vcruntime140*.dll|msvcp140*.dll|concrt140.dll)
                    continue ;;
            esac

            if [ -n "${system_dlls[$dependency_lower]:-}" ]; then
                continue
            fi

            if find "$STAGE_DIR" -type f -iname "$dependency" -print -quit | grep -q .; then
                continue
            fi

            local resolved=""
            # The app is built with the host GCC mingw (newer than the Qt-mingw
            # that built Qt's DLLs). Ship the HOST compiler's runtime DLLs — a
            # superset — so the binary can't hit a missing libstdc++ entry point
            # against Qt's older bundled runtime. Resolve these from the compiler
            # FIRST, before the Qt bin in search_dirs.
            case "$dependency_lower" in
                libstdc++-6.dll|libgcc_s_seh-1.dll|libgcc_s_dw2-1.dll|libwinpthread-1.dll)
                    resolved="$(runtime_file_from_compiler "${COMPILER_PREFIX}-g++" "$dependency" || true)" ;;
            esac
            local search_dir
            if [ -z "$resolved" ]; then
                for search_dir in "${search_dirs[@]}"; do
                    if [ -f "$search_dir/$dependency" ]; then
                        resolved="$search_dir/$dependency"
                        break
                    fi
                done
            fi

            if [ -z "$resolved" ]; then
                resolved="$(runtime_file_from_compiler "${COMPILER_PREFIX}-g++" "$dependency" || true)"
            fi

            if [ -z "$resolved" ]; then
                unresolved+=("$dependency (needed by $(basename "$current"))")
                continue
            fi

            install -m 0644 "$resolved" "$STAGE_DIR/$dependency"
            if [ -z "${queued[$STAGE_DIR/$dependency]:-}" ]; then
                queue+=("$STAGE_DIR/$dependency")
                queued["$STAGE_DIR/$dependency"]=1
            fi
        done < <("${COMPILER_PREFIX}-objdump" -p "$current" | awk '/DLL Name: / { print $3 }' | LC_ALL=C sort -u)
    done

    if [ "${#unresolved[@]}" -gt 0 ]; then
        {
            printf 'Unresolved Windows runtime dependencies:\n'
            printf '  - %s\n' "${unresolved[@]}"
        } >&2
        exit 1
    fi
}

configure_and_build() {
    rm -rf "$STAGE_DIR"
    mkdir -p "$BUILD_DIR" "$STAGE_DIR" "$DIST_DIR"

    cmake -S "$WALLET_ROOT" -B "$BUILD_DIR" \
        -G Ninja \
        -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
        -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN_FILE" \
        -DMINGW_COMPILER_PREFIX="$COMPILER_PREFIX" \
        -DQT_HOST_PATH="$QT_HOST_PATH" \
        -DWINDOWS_EXTRA_PREFIX_PATHS="$QT_ROOT;$OPENSSL_ROOT" \
        -DCMAKE_PREFIX_PATH="$QT_ROOT;$OPENSSL_ROOT" \
        -DOPENSSL_ROOT_DIR="$OPENSSL_ROOT" \
        -DWALLET_ENABLE_QT_INSTALL_DEPLOYMENT=OFF \
        -DWALLET_BUNDLE_PYTHON_RUNTIME=OFF \
        -DCMAKE_DISABLE_FIND_PACKAGE_Vulkan=TRUE \
        -DCMAKE_DISABLE_FIND_PACKAGE_WrapVulkanHeaders=TRUE \
        -DBUILD_TESTING=OFF

    cmake --build "$BUILD_DIR" --parallel "$JOBS"
    cmake --install "$BUILD_DIR" --prefix "$STAGE_DIR"
}

package_artifacts() {
    local version="$1"
    local install_include="$BUILD_DIR/installer-files.nsh"
    local uninstall_include="$BUILD_DIR/uninstaller-files.nsh"
    local installer_script="$BUILD_DIR/installer.nsi"

    rm -f \
        "$DIST_DIR/$DIST_INSTALLER_NAME" \
        "$DIST_DIR/$DIST_ZIP_NAME" \
        "$DIST_DIR/$DIST_CHECKSUM_NAME"

    (
        cd "$STAGE_DIR"
        zip -r -9 "$DIST_DIR/$DIST_ZIP_NAME" .
    )

    create_nsis_file_lists "$install_include" "$uninstall_include"
    render_nsis_script "$installer_script" "$install_include" "$uninstall_include" "$version"
    makensis "$installer_script" >/dev/null

    (
        cd "$DIST_DIR"
        sha256sum "$DIST_INSTALLER_NAME" "$DIST_ZIP_NAME" > "$DIST_CHECKSUM_NAME"
        sha256sum -c "$DIST_CHECKSUM_NAME"
    )
}

main() {
    parse_args "$@"
    check_prerequisites

    if [ "$CHECK_ONLY" = true ]; then
        log "Prerequisites OK"
        log "Qt target:      $QT_ROOT"
        log "Qt host tools:  $QT_HOST_PATH"
        log "OpenSSL target: $OPENSSL_ROOT"
        log "Compiler prefix: $COMPILER_PREFIX"
        log "Runtime mode:   $STAGE_RUNTIME_MODE"
        exit 0
    fi

    if [ "$CLEAN" = true ]; then
        rm -rf "$BUILD_DIR"
    fi

    log "======================================"
    log "Animica Wallet - Windows Cross Build"
    log "======================================"
    log "Build type:      $BUILD_TYPE"
    log "Install scope:   $INSTALL_SCOPE"
    log "Runtime mode:    $STAGE_RUNTIME_MODE"
    log "Architecture:    $ARCH_LABEL"
    log "Version:         $(resolve_version)"

    configure_and_build
    deploy_runtime_dependencies

    # Bundle a relocatable Windows Python + animica so the wallet is fully
    # self-contained (no system Python needed). Cross-builds can't create a
    # valid Windows venv, so we stage a prepared python-build-standalone tree
    # from $WINDOWS_NODE_BUNDLE (dir containing venv/python.exe + site-packages).
    if [ -n "${WINDOWS_NODE_BUNDLE:-}" ] && [ -d "${WINDOWS_NODE_BUNDLE}/venv" ]; then
        log "Bundling self-contained Python from ${WINDOWS_NODE_BUNDLE}"
        rm -rf "$STAGE_DIR/node"
        cp -a "${WINDOWS_NODE_BUNDLE}" "$STAGE_DIR/node"
    fi

    "$SCRIPT_DIR/verify-bundle-layout.py" --platform windows --path "$STAGE_DIR" --skip-bundled-node

    package_artifacts "$(resolve_version)"

    log "======================================"
    log "Summary"
    log "======================================"
    log "Build status:    success"
    log "Packaging status: success"
    log "Website publish: not requested"
    log "Stage directory: $STAGE_DIR"
    log "Installer:       $DIST_DIR/$DIST_INSTALLER_NAME"
    log "Portable ZIP:    $DIST_DIR/$DIST_ZIP_NAME"
    log "Checksums:       $DIST_DIR/$DIST_CHECKSUM_NAME"
    log "To publish:      ./scripts/publish-wallet-downloads.sh --platform windows --version $(resolve_version)"
}

main "$@"
