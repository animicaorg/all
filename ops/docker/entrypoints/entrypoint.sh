#!/usr/bin/env sh
set -eu

: "${ANIMICA_USER:=animica}"
: "${ANIMICA_UID:=10001}"
: "${ANIMICA_GID:=10001}"
: "${ANIMICA_CHAIN_ID:=0}"
: "${ANIMICA_DATA_DIR:=/data/chain-${ANIMICA_CHAIN_ID}}"
: "${HOME:=/data}"

export HOME

CHAIN_DIR="${ANIMICA_DATA_DIR%/}"
P2P_DIR="${ANIMICA_P2P_DATA_DIR:-${CHAIN_DIR%/}/p2p}"

ensure_dir() {
  dir="$1"
  if [ -z "$dir" ]; then
    return
  fi
  mkdir -p "$dir"
  if [ "$(id -u)" = "0" ]; then
    chown -R "${ANIMICA_UID}:${ANIMICA_GID}" "$dir"
  fi
}

ensure_dir "${CHAIN_DIR}"
ensure_dir "${P2P_DIR}"

check_writable() {
  dir="$1"
  if [ -z "$dir" ]; then
    return
  fi
  test_file="${dir%/}/.animica_write_check"
  if [ "$(id -u)" = "0" ]; then
    if ! gosu "${ANIMICA_USER}" sh -c "touch \"$test_file\" 2>/dev/null"; then
      echo "!! ERROR: data directory is not writable by ${ANIMICA_USER} (uid ${ANIMICA_UID})."
      echo "!! Path: ${dir}"
      echo "!! Fix by adjusting host permissions or use a named Docker volume."
      exit 1
    fi
    gosu "${ANIMICA_USER}" sh -c "rm -f \"$test_file\" 2>/dev/null" || true
  else
    if ! touch "$test_file" 2>/dev/null; then
      echo "!! ERROR: data directory is not writable by current user."
      echo "!! Path: ${dir}"
      echo "!! Fix by adjusting host permissions or use a named Docker volume."
      exit 1
    fi
    rm -f "$test_file" 2>/dev/null || true
  fi
}

check_writable "${CHAIN_DIR}"
check_writable "${P2P_DIR}"

if [ "$(id -u)" = "0" ]; then
  exec gosu "${ANIMICA_USER}" "$@"
fi

exec "$@"
