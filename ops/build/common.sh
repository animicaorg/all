#!/usr/bin/env bash
# Common helper functions for build scripts
# Defensive bash utilities for Animica build automation

# Strict mode
set -euo pipefail

# ============================================================================
# Logging helpers
# ============================================================================

log() {
    printf "\033[1;34m[build]\033[0m %s\n" "$*" >&2
}

warn() {
    printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2
}

err() {
    printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2
}

die() {
    err "$*"
    exit 1
}

# ============================================================================
# Utility checks
# ============================================================================

# Check if a command exists
have() {
    command -v "$1" >/dev/null 2>&1
}

# Require a command to exist
require() {
    local cmd="$1"
    local pkg="${2:-$1}"
    if ! have "$cmd"; then
        die "Required command '$cmd' not found. Install: $pkg"
    fi
}

# ============================================================================
# Path resolution
# ============================================================================

# Get repository root (relative to this script)
get_repo_root() {
    local script_path
    script_path="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    # ops/build -> ops -> root
    cd "$script_path/../.." && pwd
}

# Get ops/build directory
get_build_dir() {
    local repo_root
    repo_root="$(get_repo_root)"
    echo "$repo_root/ops/build"
}

# ============================================================================
# Version and Git helpers
# ============================================================================

# Get git short SHA (8 chars) or "unknown"
get_git_sha() {
    if have git && [[ -d "$(get_repo_root)/.git" ]]; then
        git -C "$(get_repo_root)" rev-parse --short=8 HEAD 2>/dev/null || echo "unknown"
    else
        echo "unknown"
    fi
}

# Get git tag or "dev"
get_git_tag() {
    if have git && [[ -d "$(get_repo_root)/.git" ]]; then
        git -C "$(get_repo_root)" describe --tags --exact-match 2>/dev/null || echo "dev"
    else
        echo "dev"
    fi
}

# Compute version string (tag or dev+sha)
compute_version() {
    local tag sha
    tag="$(get_git_tag)"
    if [[ "$tag" != "dev" ]]; then
        echo "$tag"
    else
        sha="$(get_git_sha)"
        echo "dev+${sha}"
    fi
}

# Get current timestamp (ISO8601)
get_timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

# ============================================================================
# Safe file operations
# ============================================================================

# Safe rm -rf with guards
safe_rm_rf() {
    local target="$1"
    
    # Guard: never allow empty path
    if [[ -z "$target" ]]; then
        die "safe_rm_rf: empty path"
    fi
    
    # Guard: never allow / or $HOME
    if [[ "$target" == "/" ]] || [[ "$target" == "$HOME" ]]; then
        die "safe_rm_rf: refusing to remove $target"
    fi
    
    # Guard: path must not be a system directory
    case "$target" in
        /bin|/sbin|/usr|/etc|/var|/lib|/lib64|/boot|/sys|/proc|/dev)
            die "safe_rm_rf: refusing to remove system directory $target"
            ;;
    esac
    
    # Only remove if exists
    if [[ -e "$target" ]]; then
        log "Removing: $target"
        rm -rf "$target"
    fi
}

# ============================================================================
# Python environment helpers
# ============================================================================

# Find best Python 3 executable
find_python3() {
    local repo_root
    repo_root="$(get_repo_root)"
    
    # Prefer active virtualenv
    if [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/python3" ]]; then
        echo "${VIRTUAL_ENV}/bin/python3"
        return 0
    fi
    
    # Prefer repo venv
    if [[ -x "$repo_root/.venv/bin/python3" ]]; then
        echo "$repo_root/.venv/bin/python3"
        return 0
    fi
    
    # Fall back to system python3
    if have python3; then
        command -v python3
        return 0
    fi
    
    return 1
}

# Ensure we have Python 3.10+
check_python_version() {
    local py="$1"
    local version
    
    if ! version="$("$py" --version 2>&1 | awk '{print $2}')"; then
        die "Could not get Python version"
    fi
    
    local major minor
    major="$(echo "$version" | cut -d. -f1)"
    minor="$(echo "$version" | cut -d. -f2)"
    
    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 10 ]]; }; then
        die "Python 3.10+ required, found $version"
    fi
    
    log "Using Python $version ($py)"
}

# ============================================================================
# Build environment setup
# ============================================================================

# Set defensive environment variables for Python builds
setup_python_build_env() {
    export PYTHONNOUSERSITE=1
    export PYTHONDONTWRITEBYTECODE=1
    log "Python build environment configured"
}

# ============================================================================
# Manifest generation
# ============================================================================

# Create a build manifest JSON file
create_manifest() {
    local output_file="$1"
    local artifact_name="$2"
    local version="$3"
    
    local git_sha git_tag timestamp platform
    git_sha="$(get_git_sha)"
    git_tag="$(get_git_tag)"
    timestamp="$(get_timestamp)"
    platform="$(uname -s)-$(uname -m)"
    
    cat > "$output_file" <<EOF
{
  "artifact": "$artifact_name",
  "version": "$version",
  "git_sha": "$git_sha",
  "git_tag": "$git_tag",
  "build_time": "$timestamp",
  "platform": "$platform"
}
EOF
    
    log "Manifest written to: $output_file"
}

# ============================================================================
# Verification helpers
# ============================================================================

# Verify a file exists and is executable
verify_executable() {
    local file="$1"
    
    if [[ ! -f "$file" ]]; then
        die "Expected file does not exist: $file"
    fi
    
    if [[ ! -x "$file" ]]; then
        warn "File is not executable: $file"
        chmod +x "$file"
        log "Made executable: $file"
    fi
    
    log "Verified executable: $file"
}

# Verify a directory exists
verify_directory() {
    local dir="$1"
    
    if [[ ! -d "$dir" ]]; then
        die "Expected directory does not exist: $dir"
    fi
    
    log "Verified directory: $dir"
}

# ============================================================================
# Python project helpers
# ============================================================================

# Find and validate the main Python package directory
# Returns the path to the Python package containing pyproject.toml
find_python_package_dir() {
    local repo_root="$1"
    local python_pkg_dir="$repo_root/python"
    
    if [[ -f "$python_pkg_dir/pyproject.toml" ]]; then
        echo "$python_pkg_dir"
        return 0
    fi
    
    # Fallback: check if repo root has pyproject.toml
    if [[ -f "$repo_root/pyproject.toml" ]]; then
        echo "$repo_root"
        return 0
    fi
    
    # Not found
    err "Cannot find Python package. Searched:"
    err "  - $repo_root/python/pyproject.toml"
    err "  - $repo_root/pyproject.toml"
    return 1
}

# Validate that a directory contains a Python project
validate_python_package() {
    local pkg_dir="$1"
    local pkg_name="${2:-Python package}"
    
    if [[ ! -f "$pkg_dir/pyproject.toml" ]]; then
        die "$pkg_name not found at: $pkg_dir/pyproject.toml"
    fi
    
    log "Found $pkg_name at: $pkg_dir"
}

# ============================================================================
# Dependency installation helpers
# ============================================================================

# Install Python package with pip
pip_install() {
    local py="$1"
    shift
    
    log "Installing Python packages: $*"
    "$py" -m pip install --quiet --upgrade "$@" || die "Failed to install: $*"
}

# ============================================================================
# Export functions for use in other scripts
# ============================================================================

# Note: Functions are automatically available when this file is sourced
