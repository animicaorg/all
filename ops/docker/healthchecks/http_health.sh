#!/usr/bin/env sh
# ------------------------------------------------------------------------------
# http_health.sh — generic HTTP(S) and TCP health probe for containers
#
# Usage examples:
#   # HTTP OK if 200..399
#   http_health.sh --url http://localhost:8545/healthz
#
#   # HTTP HEAD expecting 200 or 204, with substring check and 30s timeout
#   http_health.sh --url https://svc/readyz --method HEAD --status 200,204 --timeout 30
#
#   # HTTP GET expecting 200 and body to contain "ok", retry 10 times with 500ms backoff
#   http_health.sh --url http://svc/status --status 200 --body-contains ok --retries 10 --backoff 500
#
#   # TCP check (no HTTP), 5s timeout, 20 retries
#   http_health.sh --tcp node:8545 --timeout 5 --retries 20
#
# Exit codes:
#   0 = healthy
#   1 = unhealthy (probe failed)
#   2 = misconfiguration / unsupported environment
# ------------------------------------------------------------------------------

set -eu

# ------------------------ defaults ------------------------
URL=""
TCP_TARGET=""
METHOD="GET"
EXPECTED_STATUS=""     # comma list; if empty → accept 200..399
BODY_SUBSTR=""
TIMEOUT="10"           # seconds
RETRIES="5"
BACKOFF_MS="250"       # between attempts
INSECURE="false"       # skip TLS verify (curl -k / wget --no-check-certificate)
QUIET="false"

# ------------------------ helpers -------------------------
usage() {
  cat <<USAGE
Usage:
  $0 --url URL [--method GET|HEAD] [--status CSV] [--body-contains STR]
     [--timeout SEC] [--retries N] [--backoff MS] [--insecure] [--quiet]
  $0 --tcp HOST:PORT [--timeout SEC] [--retries N] [--backoff MS] [--quiet]
USAGE
}

log() {
  [ "${QUIET}" = "true" ] && return 0
  printf '%s\n' "$*"
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

to_bool() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|on|ON) echo "true" ;;
    *) echo "false" ;;
  esac
}

sleep_backoff() {
  ms="${1:-250}"
  # Convert ms→s (floor) with a minimum of 0.1s if ms>0
  if have_cmd awk; then
    secs="$(awk "BEGIN {printf \"%.3f\", (${ms})/1000}")"
    # BusyBox sleep generally supports fractional seconds
    sleep "${secs}"
  else
    # Fallback: integer seconds (at least 1 if ms>0)
    if [ "${ms}" -gt 0 ]; then
      s=$(( (ms + 999) / 1000 ))
      [ "${s}" -le 0 ] && s=1
      sleep "${s}"
    fi
  fi
}

parse_host_port() {
  # input "host:port" -> HOST, PORT (exported)
  hp="$1"
  HOST="${hp%:*}"
  PORT="${hp##*:}"
  if [ -z "${HOST}" ] || [ -z "${PORT}" ] || [ "${HOST}" = "${PORT}" ]; then
    log "ERROR: invalid --tcp '${hp}', expected host:port"
    exit 2
  fi
  export HOST PORT
}

status_ok() {
  code="$1"
  if [ -z "${EXPECTED_STATUS}" ]; then
    # default: any 2xx/3xx
    case "${code}" in
      2*|3*) return 0 ;;
      *)     return 1 ;;
    esac
  else
    # CSV match
    IFS=','; set -- ${EXPECTED_STATUS}; unset IFS
    for want in "$@"; do
      [ "${code}" = "${want}" ] && return 0
    done
    return 1
  fi
}

http_probe_curl() {
  url="$1"
  method="$2"
  insecure_flag=""
  [ "${INSECURE}" = "true" ] && insecure_flag="-k"

  body_file="/tmp/http_health_body.$$"
  code="$(curl -sS ${insecure_flag} -X "${method}" -m "${TIMEOUT}" -H 'Accept: */*' \
          -o "${body_file}" -w '%{http_code}' "${url}" || echo "000")"

  if ! status_ok "${code}"; then
    log "HTTP FAIL (curl): status=${code} url=${url}"
    rm -f "${body_file}"
    return 1
  fi

  if [ -n "${BODY_SUBSTR}" ]; then
    if ! grep -q -- "${BODY_SUBSTR}" "${body_file}" 2>/dev/null; then
      log "HTTP FAIL (curl): body missing substring '${BODY_SUBSTR}'"
      rm -f "${body_file}"
      return 1
    fi
  fi

  rm -f "${body_file}"
  log "HTTP OK (curl): ${code} ${url}"
  return 0
}

http_probe_wget() {
  url="$1"
  method="$2"
  [ "${method}" = "HEAD" ] && spider_flag="--spider" || spider_flag=""
  insecure_flag=""
  [ "${INSECURE}" = "true" ] && insecure_flag="--no-check-certificate"

  body_file="/tmp/http_health_body.$$"
  # Capture headers to extract status code
  out="$(wget -q -S ${spider_flag} ${insecure_flag} -T "${TIMEOUT}" -O "${body_file}" "${url}" 2>&1 || true)"
  # Parse last HTTP status line
  code="$(printf '%s\n' "${out}" | awk '/^  HTTP\/|^HTTP\//{c=$2} END{print c+0}')"
  [ -z "${code}" ] && code="000"

  if ! status_ok "${code}"; then
    log "HTTP FAIL (wget): status=${code} url=${url}"
    rm -f "${body_file}"
    return 1
  fi

  if [ -n "${BODY_SUBSTR}" ] && [ -z "${spider_flag}" ]; then
    if ! grep -q -- "${BODY_SUBSTR}" "${body_file}" 2>/dev/null; then
      log "HTTP FAIL (wget): body missing substring '${BODY_SUBSTR}'"
      rm -f "${body_file}"
      return 1
    fi
  fi

  rm -f "${body_file}"
  log "HTTP OK (wget): ${code} ${url}"
  return 0
}

tcp_probe() {
  hp="$1"
  parse_host_port "${hp}"

  if have_cmd nc; then
    # Try different nc variants
    if nc -h 2>&1 | grep -qi 'gnu netcat'; then
      # GNU: -z not supported; attempt connect with timeout
      nc -w "${TIMEOUT}" "${HOST}" "${PORT}" </dev/null >/dev/null 2>&1
    else
      # BusyBox/OpenBSD: -z for scan mode
      nc -z -w "${TIMEOUT}" "${HOST}" "${PORT}" >/dev/null 2>&1
    fi
    rc=$?
  elif [ -e /dev/tcp/localhost/80 ] 2>/dev/null || ( [ -n "${BASH_VERSION:-}" ] && : ); then
    # Bash /dev/tcp fallback
    ( exec 3>/dev/tcp/"${HOST}"/"${PORT}" ) >/dev/null 2>&1
    rc=$?
  else
    log "ERROR: no tcp client available (need nc or bash /dev/tcp)."
    return 2
  fi

  if [ "${rc}" -eq 0 ]; then
    log "TCP OK: ${HOST}:${PORT}"
    return 0
  else
    log "TCP FAIL: ${HOST}:${PORT}"
    return 1
  fi
}

probe_once() {
  if [ -n "${TCP_TARGET}" ]; then
    tcp_probe "${TCP_TARGET}"
    return $?
  elif [ -n "${URL}" ]; then
    if have_cmd curl; then
      http_probe_curl "${URL}" "${METHOD}"
      return $?
    elif have_cmd wget; then
      http_probe_wget "${URL}" "${METHOD}"
      return $?
    else
      log "ERROR: neither curl nor wget found for HTTP probing."
      return 2
    fi
  else
    log "ERROR: must specify --url or --tcp"
    return 2
  fi
}

# ------------------------ arg parsing ---------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --tcp) TCP_TARGET="$2"; shift 2 ;;
    --method) METHOD="$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')"; shift 2 ;;
    --status) EXPECTED_STATUS="$2"; shift 2 ;;
    --body-contains) BODY_SUBSTR="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --retries) RETRIES="$2"; shift 2 ;;
    --backoff) BACKOFF_MS="$2"; shift 2 ;;
    --insecure) INSECURE="true"; shift 1 ;;
    --quiet|-q) QUIET="true"; shift 1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      log "Unknown argument: $1"; usage; exit 2 ;;
  esac
done

# Validate basics
if [ -z "${URL}" ] && [ -z "${TCP_TARGET}" ]; then
  log "ERROR: specify either --url or --tcp"; usage; exit 2
fi
if [ -n "${URL}" ] && [ -n "${TCP_TARGET}" ]; then
  log "ERROR: --url and --tcp are mutually exclusive"; exit 2
fi
case "${METHOD}" in GET|HEAD) : ;; *) if [ -n "${URL}" ]; then log "ERROR: --method must be GET or HEAD"; exit 2; fi ;; esac

# ------------------------ retry loop ----------------------
attempt=1
max="${RETRIES}"
while : ; do
  if probe_once; then
    exit 0
  fi

  if [ "${attempt}" -ge "${max}" ]; then
    log "Giving up after ${attempt} attempt(s)."
    exit 1
  fi

  attempt=$((attempt + 1))
  sleep_backoff "${BACKOFF_MS}"
done
