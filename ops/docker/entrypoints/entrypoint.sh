#!/usr/bin/env sh
set -eu

: "${ANIMICA_DATA_DIR:=/data}"
: "${ANIMICA_CHAIN_ID:=1}"
: "${ANIMICA_RUNTIME_UID:=${ANIMICA_UID:-10001}}"
: "${ANIMICA_RUNTIME_GID:=${ANIMICA_GID:-10001}}"
: "${ANIMICA_VOLUME_STRATEGY:=named}"
# AICF dispatch mode (rpc/methods/aicf_jobs.py, 11.1.1): K per job by kind,
# kinds-filtered claims, idle/fast-worker routing. CHAT_K=3 keeps chat racing
# as before — only 1-token probes and embed/classify/batch jobs go K=1.
# Set ANIMICA_AICF_DISPATCH=0 in the container env to revert (schema is inert).
: "${ANIMICA_AICF_DISPATCH:=1}"
export ANIMICA_AICF_DISPATCH
: "${ANIMICA_AICF_DISPATCH_CHAT_K:=3}"
export ANIMICA_AICF_DISPATCH_CHAT_K

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

# ── operator env overlay ────────────────────────────────────────────────────
# `docker restart` re-runs this entrypoint but CANNOT change the container's
# baked environment — only a recreate can, and recreating the live mainnet node
# is a far riskier operation than restarting it. So consensus rollout flags
# (ANIMICA_FORK_*_HEIGHT, ANIMICA_USEFUL_WORK_SHADOW, killswitches) are read here
# from a file on the WRITABLE data volume, which survives restarts and can be
# edited without touching the read-only /app mount or the compose file.
#
# Shell-variable syntax only, one KEY=value per line, no `export`, no command
# substitution — it is sourced, so treat it as code. Anyone who can write it
# already controls the node's database, so this adds no new privilege surface;
# it does mean the file must have the same protection as the data volume.
#
#   /data/node.env      (ANIMICA_DATA_DIR defaults to /data)
#
# Existing container environment WINS: the overlay only fills in variables that
# are unset or empty, so a value baked into the container can never be silently
# overridden by a file. Remove the file and restart to revert.
ENV_OVERLAY="${ANIMICA_ENV_OVERLAY:-${ANIMICA_DATA_DIR%/}/node.env}"
if [ -f "$ENV_OVERLAY" ]; then
  echo ">> env overlay: ${ENV_OVERLAY}"
  while IFS= read -r _line || [ -n "$_line" ]; do
    case "$_line" in
      ''|'#'*) continue ;;
    esac
    _key="${_line%%=*}"
    _val="${_line#*=}"
    case "$_key" in
      *[!A-Za-z0-9_]*|'') echo ">> env overlay: skipping malformed line"; continue ;;
    esac
    # Strip one layer of surrounding quotes if present.
    case "$_val" in
      \"*\") _val="${_val#\"}"; _val="${_val%\"}" ;;
      \'*\') _val="${_val#\'}"; _val="${_val%\'}" ;;
    esac
    eval "_cur=\${$_key:-}"
    if [ -n "${_cur:-}" ]; then
      echo ">> env overlay: ${_key} already set in container env, keeping it"
      continue
    fi
    export "$_key=$_val"
    echo ">> env overlay: ${_key}=${_val}"
  done < "$ENV_OVERLAY"
  unset _line _key _val _cur
fi

if [ "$(id -u)" = "0" ]; then
  exec gosu "${ANIMICA_RUNTIME_UID}:${ANIMICA_RUNTIME_GID}" "$@"
fi

exec "$@"
