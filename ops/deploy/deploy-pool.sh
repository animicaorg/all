#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
POOL_WEB_DIR="${POOL_WEB_DIR:-${REPO_ROOT}/pool-web}"
WEBSITE_DIR="${WEBSITE_DIR:-${REPO_ROOT}/website}"
POOL_WEB_ROOT="${POOL_WEB_ROOT:-/var/www/pool.animica.org}"
MAIN_SITE_ROOT="${MAIN_SITE_ROOT:-/var/www/animica.org}"
POOL_ENV_FILE="${POOL_ENV_FILE:-/etc/animica/pool.env}"
POOL_SERVICE="${POOL_SERVICE:-animica-pool.service}"
POOL_ADDRESS_LABEL="${POOL_ADDRESS_LABEL:-pool}"
POOL_ADDRESS="${ANIMICA_POOL_ADDRESS:-}"

log() { printf '[deploy-pool] %s\n' "$*"; }
fail() { printf '[deploy-pool] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "run as root so the script can update /etc/nginx, systemd, firewall, and /var/www"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

resolve_pool_address() {
  if [[ -n "${POOL_ADDRESS}" ]]; then
    printf '%s\n' "${POOL_ADDRESS}"
    return 0
  fi

  if [[ -f "${POOL_ENV_FILE}" ]]; then
    local from_env
    from_env="$(grep -E '^ANIMICA_POOL_ADDRESS=' "${POOL_ENV_FILE}" | tail -n 1 | cut -d= -f2- || true)"
    if [[ -n "${from_env}" ]]; then
      printf '%s\n' "${from_env}"
      return 0
    fi
  fi

  python3 - "${POOL_ADDRESS_LABEL}" <<'PY'
import json
import sys
from pathlib import Path

label = sys.argv[1]
path = Path.home() / ".animica" / "wallets.json"
if not path.exists():
    sys.exit(1)
store = json.loads(path.read_text())
for entry in store.get("wallets", []):
    if entry.get("label") == label and entry.get("address"):
        print(entry["address"])
        sys.exit(0)
default_address = store.get("default_address")
if default_address:
    print(default_address)
    sys.exit(0)
sys.exit(1)
PY
}

write_pool_env() {
  local resolved_address="$1"
  install -d -m 0750 /etc/animica
  umask 077
  if [[ ! -f "${POOL_ENV_FILE}" ]]; then
    cat > "${POOL_ENV_FILE}" <<EOF_ENV
# Managed by ${REPO_ROOT}/ops/deploy/deploy-pool.sh
# Public payout address only. Do not put wallet seeds, private keys, or RPC passwords here.
EOF_ENV
  else
    cp -p "${POOL_ENV_FILE}" "${POOL_ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  fi

  set_pool_env ANIMICA_POOL_ADDRESS "${resolved_address}"
  set_pool_env ANIMICA_POOL_MODE "${ANIMICA_POOL_MODE:-pps}"
  set_pool_env ANIMICA_POOL_ENABLED "1"
  set_pool_env ANIMICA_PUBLIC_DOMAIN "pool.animica.org"
  set_pool_env ANIMICA_PUBLIC_STRATUM_HOST "pool.animica.org"
  set_pool_env ANIMICA_PUBLIC_STRATUM_PORT "3333"
  set_pool_env ANIMICA_PUBLIC_STRATUM_URL "stratum+tcp://pool.animica.org:3333"
  set_pool_env ANIMICA_PUBLIC_STRATUM_SCHEME "stratum+tcp"
  set_pool_env ANIMICA_PUBLIC_POOL_API_URL "https://pool.animica.org"
  chmod 0600 "${POOL_ENV_FILE}"
}

set_pool_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "${POOL_ENV_FILE}"; then
    perl -0pi -e "s#^${key}=.*\$#${key}=${value}#m" "${POOL_ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${POOL_ENV_FILE}"
  fi
}

build_sites() {
  require_cmd pnpm
  log "building pool-web"
  if [[ ! -d "${POOL_WEB_DIR}/node_modules" ]]; then
    pnpm -C "${POOL_WEB_DIR}" install --no-frozen-lockfile
  fi
  POOL_URL="stratum+tcp://pool.animica.org:3333" \
    MINING_API_BASE_URL="https://pool.animica.org" \
    pnpm -C "${POOL_WEB_DIR}" build

  log "building main website"
  if [[ ! -d "${WEBSITE_DIR}/node_modules" ]]; then
    pnpm -C "${WEBSITE_DIR}" install --no-frozen-lockfile
  fi
  SITE_URL="https://animica.org" \
    ANIMICA_POOL_URL="https://pool.animica.org" \
    pnpm -C "${WEBSITE_DIR}" build
}

sync_sites() {
  require_cmd rsync
  install -d -m 0755 "${POOL_WEB_ROOT}" "${MAIN_SITE_ROOT}"
  log "rsync pool-web/dist -> ${POOL_WEB_ROOT}"
  rsync -a --delete "${POOL_WEB_DIR}/dist/" "${POOL_WEB_ROOT}/"
  chmod -R u=rwX,go=rX "${POOL_WEB_ROOT}"
  log "rsync website/dist -> ${MAIN_SITE_ROOT}"
  rsync -a --delete "${WEBSITE_DIR}/dist/" "${MAIN_SITE_ROOT}/"
  chmod -R u=rwX,go=rX "${MAIN_SITE_ROOT}"
}

install_systemd_unit() {
  local target="/etc/systemd/system/${POOL_SERVICE}"
  log "installing systemd unit ${target}"
  sed "s#/root/animica#${REPO_ROOT}#g" \
    "${REPO_ROOT}/ops/systemd/animica-pool.service" > "${target}"
  chmod 0644 "${target}"
  systemctl daemon-reload
  systemctl enable "${POOL_SERVICE}"
}

install_nginx() {
  install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/snippets

  log "installing pool.animica.org Nginx config"
  install -m 0644 "${REPO_ROOT}/ops/nginx/pool.animica.org.conf" \
    /etc/nginx/sites-available/pool.animica.org.conf
  ln -sfn /etc/nginx/sites-available/pool.animica.org.conf \
    /etc/nginx/sites-enabled/pool.animica.org.conf

  log "installing /mine redirect snippet"
  install -m 0644 "${REPO_ROOT}/ops/nginx/animica-mine-redirect.conf" \
    /etc/nginx/snippets/animica-mine-redirect.conf

  local animica_conf="/etc/nginx/sites-available/animica.org.conf"
  if [[ -f "${animica_conf}" ]] && ! grep -q 'animica-mine-redirect.conf' "${animica_conf}"; then
    cp -p "${animica_conf}" "${animica_conf}.bak.$(date +%Y%m%d%H%M%S)"
    awk '
      { print }
      /client_max_body_size[[:space:]]+100M;/ && inserted == 0 {
        print ""
        print "    include /etc/nginx/snippets/animica-mine-redirect.conf;"
        inserted = 1
      }
    ' "${animica_conf}" > "${animica_conf}.tmp"
    mv "${animica_conf}.tmp" "${animica_conf}"
  fi

  nginx -t
  systemctl reload nginx
}

configure_firewall() {
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -qi '^Status: active'; then
    log "allowing 80/tcp, 443/tcp, 3333/tcp via ufw"
    ufw allow 80/tcp >/dev/null || true
    ufw allow 443/tcp >/dev/null || true
    ufw allow 3333/tcp >/dev/null || true
  elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    log "allowing 80/tcp, 443/tcp, 3333/tcp via firewalld"
    firewall-cmd --permanent --add-port=80/tcp >/dev/null || true
    firewall-cmd --permanent --add-port=443/tcp >/dev/null || true
    firewall-cmd --permanent --add-port=3333/tcp >/dev/null || true
    firewall-cmd --reload >/dev/null || true
  else
    log "no active ufw/firewalld detected; ensure upstream firewall allows TCP 80, 443, and 3333"
  fi
}

restart_pool() {
  log "restarting ${POOL_SERVICE}"
  systemctl restart "${POOL_SERVICE}"
  sleep 2
  systemctl --no-pager --full status "${POOL_SERVICE}" || true
}

print_final() {
  cat <<'MSG'

Pool website: https://pool.animica.org
Stratum: pool.animica.org:3333
Old mining page redirect: https://animica.org/mine -> https://pool.animica.org

Miner commands:
python3 -m pip install --upgrade animica
animica miner setup
animica miner mine-blocks --count 0 --pool-stratum stratum+tcp://pool.animica.org:3333

Verification:
curl -I https://pool.animica.org
curl -I https://animica.org/mine
nc -vz pool.animica.org 3333
MSG
}

main() {
  require_root
  require_cmd python3
  local resolved_address
  resolved_address="$(resolve_pool_address)" || fail "set ANIMICA_POOL_ADDRESS, create /etc/animica/pool.env, or create a local wallet label named '${POOL_ADDRESS_LABEL}'"
  write_pool_env "${resolved_address}"
  build_sites
  sync_sites
  install_systemd_unit
  install_nginx
  configure_firewall
  restart_pool
  print_final
}

main "$@"
