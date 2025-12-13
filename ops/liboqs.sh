#!/usr/bin/env bash
# Shared liboqs tooling (version, paths, build helpers)
# shellcheck shell=bash

LIBOQS_VERSION="0.14.0"
LIBOQS_REPO="https://github.com/open-quantum-safe/liboqs.git"

# Emit a log line (caller can override liboqs_log)
liboqs_log() {
  printf '[liboqs] %s\n' "$*"
}

liboqs_warn() {
  >&2 printf '[liboqs] WARN: %s\n' "$*"
}

liboqs_err() {
  >&2 printf '[liboqs] ERROR: %s\n' "$*"
}

# Resolve repo root from caller or this file
liboqs_repo_root() {
  if [[ -n ${REPO_ROOT:-} ]]; then
    echo "$REPO_ROOT"
    return
  fi
  local here
  here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  echo "$(cd "$here/.." && pwd)"
}

# Installation prefix for vendored liboqs
liboqs_prefix() {
  local root
  root="$(liboqs_repo_root)"
  echo "$root/.deps/liboqs/${LIBOQS_VERSION}"
}

liboqs_env_file() {
  echo "$(liboqs_prefix)/env.sh"
}

liboqs_version_file() {
  echo "$(liboqs_prefix)/.version"
}

# Locate built shared library under prefix
liboqs_locate_lib() {
  local prefix
  prefix="$(liboqs_prefix)"
  for candidate in \
    "$prefix/lib/liboqs.so" \
    "$prefix/lib64/liboqs.so" \
    "$prefix/lib/liboqs.dylib" \
    "$prefix/lib64/liboqs.dylib" \
    "$prefix/lib/liboqs.so.5" \
    "$prefix/lib64/liboqs.so.5"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

liboqs_write_env_file() {
  local prefix lib env_file
  prefix="$(liboqs_prefix)"
  lib="$(liboqs_locate_lib || true)"
  env_file="$(liboqs_env_file)"
  mkdir -p "$(dirname "$env_file")"
  cat >"$env_file" <<EOF2
# Auto-generated liboqs environment (version ${LIBOQS_VERSION})
export OQS_INSTALL_PATH="$prefix"
export LIBOQS_PATH="${lib:-$prefix/lib/liboqs.so}"
export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:\${LD_LIBRARY_PATH:-}"
if [[ "$(uname -s)" == "Darwin" ]]; then
  export DYLD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:\${DYLD_LIBRARY_PATH:-}"
fi
EOF2
  chmod +x "$env_file"
}

liboqs_export_env() {
  local prefix lib
  prefix="$(liboqs_prefix)"
  lib="$(liboqs_locate_lib || true)"
  export OQS_INSTALL_PATH="$prefix"
  export LIBOQS_PATH="${lib:-$prefix/lib/liboqs.so}"
  export LD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:${LD_LIBRARY_PATH:-}"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    export DYLD_LIBRARY_PATH="$prefix/lib:$prefix/lib64:${DYLD_LIBRARY_PATH:-}"
  fi
}

liboqs_build() {
  local root work src build prefix version_file
  root="$(liboqs_repo_root)"
  work="$root/.deps/liboqs"
  src="$work/src"
  build="$work/build"
  prefix="$(liboqs_prefix)"
  version_file="$(liboqs_version_file)"

  mkdir -p "$work"

  if [[ -f "$version_file" ]]; then
    local recorded
    recorded="$(cat "$version_file" 2>/dev/null || true)"
    if [[ "$recorded" == "$LIBOQS_VERSION" ]] && [[ -f "$prefix/lib/liboqs.so" || -f "$prefix/lib64/liboqs.so" ]]; then
      liboqs_log "liboqs ${LIBOQS_VERSION} already built at $prefix"
      liboqs_export_env
      liboqs_write_env_file
      return 0
    else
      liboqs_warn "Existing liboqs at $prefix is stale (found '$recorded'); rebuilding"
      rm -rf "$work"
      mkdir -p "$work"
      src="$work/src"
      build="$work/build"
    fi
  fi

  liboqs_log "Cloning liboqs ${LIBOQS_VERSION} into $src"
  rm -rf "$src" "$build" "$prefix"
  git clone --depth 1 --branch "${LIBOQS_VERSION}" "$LIBOQS_REPO" "$src"

  mkdir -p "$build"
  pushd "$build" >/dev/null
    local generator
    if command -v ninja >/dev/null 2>&1; then
      generator="Ninja"
    else
      generator="Unix Makefiles"
    fi
    liboqs_log "Configuring with CMake generator: $generator"
    cmake -G"$generator" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$prefix" \
      -DBUILD_SHARED_LIBS=ON \
      -DOQS_USE_OPENSSL=ON \
      "$src"
    liboqs_log "Building and installing liboqs ${LIBOQS_VERSION}"
    if [[ "$generator" == "Ninja" ]]; then
      ninja install
    else
      cmake --build . --target install -- -j"${NPROC:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)}"
    fi
  popd >/dev/null

  echo "$LIBOQS_VERSION" >"$version_file"
  liboqs_export_env
  liboqs_write_env_file
  liboqs_log "liboqs ${LIBOQS_VERSION} installed to $prefix"
}

# Print summary for logging
liboqs_summary() {
  local prefix lib
  prefix="$(liboqs_prefix)"
  lib="$(liboqs_locate_lib || true)"
  liboqs_log "LIBOQS_VERSION=${LIBOQS_VERSION}"
  liboqs_log "OQS_INSTALL_PATH=${prefix}"
  liboqs_log "LIBOQS_PATH=${lib:-<missing>}"
}

