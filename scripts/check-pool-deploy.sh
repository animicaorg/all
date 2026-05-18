#!/usr/bin/env bash
set -u -o pipefail

status=0
run() {
  printf '\n$ %s\n' "$*"
  "$@" || status=$?
}

check_tcp() {
  local host="$1" port="$2"
  printf '\n$ nc -vz %s %s\n' "${host}" "${port}"
  if command -v nc >/dev/null 2>&1; then
    nc -vz "${host}" "${port}" || status=$?
  else
    timeout 5 bash -c "cat < /dev/null > /dev/tcp/${host}/${port}" \
      && printf 'tcp-ok %s:%s\n' "${host}" "${port}" \
      || status=$?
  fi
}

run curl -I https://pool.animica.org
run curl -I https://animica.org/mine
check_tcp pool.animica.org 3333

printf '\n$ ss -lntp | grep :3333\n'
ss -lntp 2>/dev/null | grep ':3333' || status=$?

printf '\n$ systemctl status nginx --no-pager || true\n'
systemctl status nginx --no-pager || true

printf '\n$ docker ps || true\n'
docker ps || true
if command -v docker >/dev/null 2>&1; then
  printf '\n$ docker compose ps\n'
  docker compose ps || true
fi

exit "${status}"
