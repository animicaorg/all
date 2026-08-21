#!/usr/bin/env sh
set -eu

: "${ANIMICA_DATA_DIR:=/data}"
: "${ANIMICA_CHAIN_ID:=1}"
: "${ANIMICA_RUNTIME_UID:=${ANIMICA_UID:-10001}}"
: "${ANIMICA_RUNTIME_GID:=${ANIMICA_GID:-10001}}"
: "${ANIMICA_VOLUME_STRATEGY:=named}"
# Report the version this tree actually RUNS. The runtime here was hand-patched
# up to 10.4.4 (consensus/, core/, execution/, coretx/, mempool/, vm_py/, l2/ and
# rpc/ are byte-identical to tag v10.4.4), but nothing told the node so: with no
# override core.version fell through to DEFAULT_VERSION and it reported "0.1.0",
# while python/pyproject.toml still said 9.7.1. Both were labels, not behaviour.
# An explicit ANIMICA_VERSION in the environment still wins over this default.
: "${ANIMICA_VERSION:=10.4.4}"
export ANIMICA_VERSION

export HOME="${HOME:-${ANIMICA_DATA_DIR}}"
CHAIN_DIR="${ANIMICA_DATA_DIR%/}/chain-${ANIMICA_CHAIN_ID}"
export ANIMICA_HOME="${ANIMICA_HOME:-${CHAIN_DIR}}"
P2P_DIR="${ANIMICA_P2P_DATA_DIR:-${CHAIN_DIR}/p2p}"
SNAPSHOTS_DIR="${ANIMICA_SNAPSHOT_DIR:-${ANIMICA_DATA_DIR%/}/snapshots}"

ensure_dir() {
  dir="$1"
  label="$2"
  if [ -z "$dir" ]; then
    return
  fi
  if ! mkdir -p "$dir" 2>/dev/null; then
    echo "!! ERROR: could not create ${label}."
    echo "!! Path: ${dir}"
    echo "!! Storage strategy: ${ANIMICA_VOLUME_STRATEGY}"
    echo "!! Mount source: ${ANIMICA_DATA_MOUNT_SOURCE:-unknown}"
    exit 1
  fi
}

echo ">> storage strategy: ${ANIMICA_VOLUME_STRATEGY}"
echo ">> container data dir: ${ANIMICA_DATA_DIR}"
echo ">> container chain dir: ${CHAIN_DIR}"
echo ">> container p2p dir: ${P2P_DIR}"
echo ">> container snapshots dir: ${SNAPSHOTS_DIR}"
echo ">> host data dir: ${ANIMICA_HOST_DATA_DIR:-n/a}"
echo ">> host chain dir: ${ANIMICA_HOST_CHAIN_DIR:-n/a}"
echo ">> host snapshots dir: ${ANIMICA_HOST_SNAPSHOTS_DIR:-n/a}"
echo ">> mount source: ${ANIMICA_DATA_MOUNT_SOURCE:-unknown}"

ensure_dir "${ANIMICA_DATA_DIR}" "container data directory"
ensure_dir "${CHAIN_DIR}" "container chain directory"
ensure_dir "${P2P_DIR}" "container p2p directory"
ensure_dir "${SNAPSHOTS_DIR}" "container snapshots directory"

check_writable() {
  dir="$1"
  label="$2"
  if [ -z "$dir" ]; then
    return
  fi
  test_file="${dir%/}/.animica_write_check"
  if [ "$(id -u)" = "0" ]; then
    if ! gosu "${ANIMICA_RUNTIME_UID}:${ANIMICA_RUNTIME_GID}" sh -c "touch \"$test_file\" 2>/dev/null"; then
      echo "!! ERROR: ${label} is not writable by runtime uid ${ANIMICA_RUNTIME_UID}:${ANIMICA_RUNTIME_GID}."
      echo "!! Path: ${dir}"
      echo "!! Storage strategy: ${ANIMICA_VOLUME_STRATEGY}"
      echo "!! Mount source: ${ANIMICA_DATA_MOUNT_SOURCE:-unknown}"
      echo "!! Use a named Docker volume or prepare the bind mount with matching ownership."
      exit 1
    fi
    gosu "${ANIMICA_RUNTIME_UID}:${ANIMICA_RUNTIME_GID}" sh -c "rm -f \"$test_file\" 2>/dev/null" || true
  else
    if ! touch "$test_file" 2>/dev/null; then
      echo "!! ERROR: ${label} is not writable by current user."
      echo "!! Path: ${dir}"
      echo "!! Storage strategy: ${ANIMICA_VOLUME_STRATEGY}"
      echo "!! Mount source: ${ANIMICA_DATA_MOUNT_SOURCE:-unknown}"
      echo "!! Use a named Docker volume or prepare the bind mount with matching ownership."
      exit 1
    fi
    rm -f "$test_file" 2>/dev/null || true
  fi
}

check_writable "${ANIMICA_DATA_DIR}" "container data directory"
check_writable "${CHAIN_DIR}" "container chain directory"
check_writable "${P2P_DIR}" "container p2p directory"
check_writable "${SNAPSHOTS_DIR}" "container snapshots directory"

if [ "$(id -u)" = "0" ]; then
  exec gosu "${ANIMICA_RUNTIME_UID}:${ANIMICA_RUNTIME_GID}" "$@"
fi

exec "$@"
