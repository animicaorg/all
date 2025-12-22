#!/usr/bin/env sh
set -eu

: "${ANIMICA_USER:=animica}"
: "${ANIMICA_UID:=10001}"
: "${ANIMICA_GID:=10001}"
: "${ANIMICA_DATA_DIR:=/data}"
: "${ANIMICA_CHAIN_ID:=1}"

p2p_dir_default="${ANIMICA_DATA_DIR%/}/chain-${ANIMICA_CHAIN_ID}/p2p"
P2P_DIR="${ANIMICA_P2P_DATA_DIR:-${p2p_dir_default}}"

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
ensure_dir "${P2P_DIR}"

if [ "$(id -u)" = "0" ]; then
  exec gosu "${ANIMICA_USER}" "$@"
fi

exec "$@"
