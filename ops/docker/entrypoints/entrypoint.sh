#!/usr/bin/env sh
set -eu

: "${ANIMICA_USER:=animica}"
: "${ANIMICA_UID:=10001}"
: "${ANIMICA_GID:=10001}"
: "${ANIMICA_DATA_DIR:=/data}"
: "${ANIMICA_CHAIN_ID:=1}"

p2p_dir_default="${ANIMICA_DATA_DIR%/}/chain-${ANIMICA_CHAIN_ID}/p2p"
P2P_DIR="${ANIMICA_P2P_DATA_DIR:-${p2p_dir_default}}"
CHAIN_DIR="${ANIMICA_DATA_DIR%/}/chain-${ANIMICA_CHAIN_ID}"

ensure_dir() {
  dir="$1"
  if [ -z "$dir" ]; then
    return
  fi
  mkdir -p "$dir" || true
  chmod 0755 "$dir" || true
  if [ "$(id -u)" = "0" ]; then
    chown -R "${ANIMICA_UID}:${ANIMICA_GID}" "$dir" || true
  fi
}

ensure_dir "${ANIMICA_DATA_DIR}"
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

if [ "$(id -u)" = "0" ]; then
  exec gosu "${ANIMICA_USER}" "$@"
fi

exec "$@"
