#!/usr/bin/env bash
# Host-side auto-updater for the docker/bind-mount mainnet seed.
#
# The seed runs from a READ-ONLY bind-mount of this repo (/root/animica -> /app),
# so the in-node autoupdater (pip install + self-SIGTERM) cannot update it: it
# can neither write the ro mount nor restart its own container. This host timer
# does it correctly and conservatively:
#
#   * Adopt only a strictly-newer release TAG (vX.Y.Z), never a branch tip.
#   * Honor a minimum release age (default 6h) — time to yank a bad release.
#   * Deploy ONLY the node paths (core p2p rpc python) so an unrelated dirty
#     working tree (e.g. vendored venv files) never blocks or pollutes a deploy.
#   * Health-check after restart and AUTO-ROLLBACK to the previous tag if the
#     node doesn't come back healthy (and the new DB-writable probe passes).
#   * flock so overlapping timer ticks can't race.
#
# Install: see scripts/seed_autoupdate.timer / .service (systemd).
set -euo pipefail

REPO="${ANIMICA_SEED_REPO:-/root/animica}"
CONTAINER="${ANIMICA_SEED_CONTAINER:-animica-mainnet-node}"
RPC="${ANIMICA_SEED_RPC:-http://127.0.0.1:8545/rpc}"
# Default 0 = adopt new release tags immediately (no min-age yank window).
# Safety still comes from the post-restart health-check + auto-rollback below.
# Set ANIMICA_SEED_MIN_AGE_H=<hours> to restore a buffer.
MIN_AGE_H="${ANIMICA_SEED_MIN_AGE_H:-0}"
PATHS=(core p2p rpc python)
LOG="${ANIMICA_SEED_AUTOUPDATE_LOG:-/var/log/animica-seed-autoupdate.log}"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG" >&2; }

git_c() { git -C "$REPO" "$@"; }

semver_gt() { # $1 > $2 ?
  [ "$1" = "$2" ] && return 1
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}

current_version() { grep -E '^version' "$REPO/python/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/'; }

health_ok() { # node responds and reports a head height
  local out
  out=$(curl -s --max-time 6 "$RPC" -X POST -H 'content-type: application/json' \
        -d '{"jsonrpc":"2.0","id":1,"method":"node.health","params":[]}' 2>/dev/null || true)
  echo "$out" | grep -q '"head_height"'
}

deploy_paths_from() { # $1 = git ref; check out node paths from it + refresh installed animica.*
  git_c checkout "$1" -- "${PATHS[@]}" || return 1
  # Deploy-gap fix: the node imports animica.* from the in-container INSTALLED
  # site-packages (e.g. /data/.local), NOT /app/python — so a git checkout of
  # `python/` alone never updates animica.* (sync/readiness.py, etc.). Refresh
  # the installed copy from the just-deployed /app/python. Best-effort and
  # non-fatal; core/p2p/rpc load directly from /app so they're already updated.
  local site
  site=$(docker exec "$CONTAINER" python3 -c "import animica,os;print(os.path.dirname(animica.__file__))" 2>/dev/null || true)
  case "$site" in
    ""|/app/*) : ;;  # not installed separately, or already loaded from /app
    *) if docker exec "$CONTAINER" sh -c "cp -rf /app/python/animica/. '$site'/ 2>/dev/null"; then
         log "refreshed installed animica.* at $site from /app/python"
       else
         log "animica.* refresh failed (non-fatal)"
       fi ;;
  esac
}

restart_and_verify() {
  docker restart "$CONTAINER" >/dev/null 2>&1 || { log "docker restart failed"; return 1; }
  for _ in $(seq 1 30); do
    if health_ok; then
      # confirm the DB-writable probe did NOT fire CRITICAL on this boot
      if docker logs "$CONTAINER" --since 2m 2>&1 | grep -qi "CHAIN DB IS NOT WRITABLE"; then
        log "post-restart: DB-writable probe reported CRITICAL"; return 1
      fi
      return 0
    fi
    sleep 3
  done
  return 1
}

main() {
  command -v docker >/dev/null || { log "docker not found"; exit 0; }
  git_c rev-parse --git-dir >/dev/null 2>&1 || { log "not a git repo: $REPO"; exit 0; }

  git_c fetch --tags --quiet origin || { log "git fetch failed"; exit 0; }

  local cur latest_tag latest_ver
  cur="$(current_version)"
  latest_tag="$(git_c tag -l 'v*' | sed 's/^v//' | sort -V | tail -1)"
  [ -n "$latest_tag" ] || { log "no release tags"; exit 0; }
  latest_ver="$latest_tag"
  latest_tag="v$latest_tag"

  if ! semver_gt "$latest_ver" "$cur"; then
    log "up to date (deployed $cur, latest $latest_ver)"; exit 0
  fi

  # min-age gate using the tag's commit time
  local tag_ts now age_h
  tag_ts="$(git_c log -1 --format=%ct "$latest_tag" 2>/dev/null || echo 0)"
  now="$(date -u +%s)"
  age_h=$(( (now - tag_ts) / 3600 ))
  if [ "$tag_ts" -gt 0 ] && [ "$age_h" -lt "$MIN_AGE_H" ]; then
    log "new release $latest_ver is only ${age_h}h old (< ${MIN_AGE_H}h) — waiting"; exit 0
  fi

  local prev_tag="v$cur"
  git_c rev-parse "$prev_tag" >/dev/null 2>&1 || prev_tag="HEAD"

  log "updating seed $cur -> $latest_ver (rollback target: $prev_tag)"
  if ! deploy_paths_from "$latest_tag"; then
    log "checkout of $latest_tag failed; aborting (no change)"; exit 1
  fi

  if restart_and_verify; then
    log "update to $latest_ver OK; head=$(curl -s --max-time 5 "$RPC" -X POST -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | sed -E 's/.*"height":([0-9]+).*/\1/')"
    exit 0
  fi

  log "ROLLBACK: $latest_ver unhealthy, reverting to $prev_tag"
  deploy_paths_from "$prev_tag" || log "rollback checkout failed!"
  if restart_and_verify; then
    log "rollback to $prev_tag OK"
  else
    log "ALERT: rollback also unhealthy — manual intervention needed"
  fi
  exit 1
}

exec 9>"/tmp/animica-seed-autoupdate.lock"
flock -n 9 || { echo "another run in progress"; exit 0; }
main "$@"
