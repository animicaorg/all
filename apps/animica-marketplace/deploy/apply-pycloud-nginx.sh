#!/usr/bin/env bash
# Wire the Animica Python Cloud routes into animica.dev's nginx config.
#
#   sudo ./deploy/apply-pycloud-nginx.sh          # apply
#   sudo ./deploy/apply-pycloud-nginx.sh --check  # show what would change, touch nothing
#
# Idempotent: re-running is a no-op. Always backs up, always validates with `nginx -t` BEFORE
# reloading, and restores the backup if validation fails — a bad config here takes animica.dev
# (and the rest of the vhosts) offline.
set -euo pipefail

SITE=/etc/nginx/sites-enabled/animica.dev.conf
ZONES=/etc/nginx/conf.d/anm-cloud-ratelimit.conf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLOCKS="$HERE/animica.dev-pycloud.nginx.conf"
MARKER='# >>> animica python cloud >>>'
END_MARKER='# <<< animica python cloud <<<'
CHECK=0
[[ "${1:-}" == "--check" ]] && CHECK=1

[[ -f "$SITE" ]] || { echo "missing $SITE" >&2; exit 1; }
[[ -f "$BLOCKS" ]] || { echo "missing $BLOCKS" >&2; exit 1; }

if grep -qF "$MARKER" "$SITE"; then
  echo "already wired: $MARKER present in $SITE"
  if [[ $CHECK -eq 1 ]]; then exit 0; fi
  # Still (re)write the zone file so a rate-limit retune lands without hand-editing.
else
  echo "will insert Python Cloud location blocks into $SITE"
fi

if [[ $CHECK -eq 1 ]]; then
  echo "--- zones that would be written to $ZONES ---"
  cat <<'ZONEEOF'
limit_req_zone $binary_remote_addr zone=anmcloud:10m rate=120r/m;
limit_req_zone $binary_remote_addr zone=anminvoke:10m rate=60r/m;
ZONEEOF
  echo "--- blocks that would be inserted ---"
  sed -n '1,20p' "$BLOCKS"
  echo "... (see $BLOCKS)"
  exit 0
fi

# Rate-limit zones must live in the http{} context, which is what conf.d/ is included into.
# Mirrors the existing conf.d/anm-ai-ratelimit.conf pattern (zone=anmai 30r/m, anmbuild 12r/m).
cat > "$ZONES" <<'ZONEEOF'
# Animica Python Cloud rate-limit zones (http context).
#   anmcloud  — the control-plane API (/api/cloud/): list, deploy, configure. Generous.
#   anminvoke — the public execution endpoint (/api/cloud/v1/fn/): this is where untrusted
#               internet traffic runs developer code, so it is the tighter budget. Per-account
#               and free-tier limits are ALSO enforced in the app (lib/cloud/ratelimit.ts);
#               this zone is the cheap edge defense that keeps a flood off the Node process.
limit_req_zone $binary_remote_addr zone=anmcloud:10m rate=120r/m;
limit_req_zone $binary_remote_addr zone=anminvoke:10m rate=60r/m;
ZONEEOF
echo "wrote $ZONES"

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="${SITE}.bak.pre-pycloud-${STAMP}"

cp -a "$SITE" "$BACKUP"
echo "backed up -> $BACKUP"

# Insert into the server block that ACTUALLY SERVES THE SITE.
#
# This file contains more than one `server {}`: the real TLS vhost, plus a Certbot-managed
# port-80 block that only 301s to https. Inserting before the FILE's last closing brace lands
# the locations in the redirect block, where they are silently inert — the routes keep serving
# the static homepage and nothing looks broken. So we locate the block containing
# `root /var/www/animica.dev` and insert before ITS closing brace, and we relocate any previous
# mis-insertion rather than leaving a dead copy behind.
python3 - "$SITE" "$BLOCKS" "$MARKER" "$END_MARKER" <<'PYEOF'
import re, sys
site, blocks_path, marker, end_marker = sys.argv[1:5]
src = open(site).read()

# 1. Remove any previous insertion, wherever it landed (makes this idempotent + self-healing).
pat = re.compile(r'\n[ \t]*' + re.escape(marker) + r'.*?' + re.escape(end_marker) + r'[ \t]*\n', re.S)
src, removed = pat.subn('\n', src)
if removed:
    print(f'removed {removed} previous insertion(s)')

lines = src.split('\n')

# 2. Find the server block that serves the site, and its closing brace.
starts = [i for i, l in enumerate(lines) if re.match(r'^\s*server\s*\{', l)]
if not starts:
    sys.exit('no server block found')
target_end = None
for s in starts:
    depth = 0
    for i in range(s, len(lines)):
        depth += lines[i].count('{') - lines[i].count('}')
        if depth == 0 and i > s:
            body = '\n'.join(lines[s:i])
            if 'root /var/www/animica.dev' in body and 'listen 80;' not in body:
                target_end = i
            break
    if target_end is not None:
        break
if target_end is None:
    sys.exit('could not identify the serving server block (expected one with root /var/www/animica.dev)')

blocks = open(blocks_path).read().rstrip('\n')
indented = '\n'.join(('    ' + l) if l.strip() else '' for l in blocks.split('\n'))
ins = ['', '    ' + marker, indented, '    ' + end_marker, '']
out = lines[:target_end] + ins + lines[target_end:]
open(site, 'w').write('\n'.join(out))
print(f'inserted Python Cloud blocks before line {target_end + 1} (the serving server block)')
PYEOF

if nginx -t 2>&1; then
  systemctl reload nginx
  echo "nginx validated and reloaded"
else
  echo "nginx -t FAILED — restoring the backup and leaving nginx untouched" >&2
  [[ -f "$BACKUP" ]] && cp -a "$BACKUP" "$SITE"
  rm -f "$ZONES"
  nginx -t >&2 || true
  exit 1
fi

# Prove the routes actually reach the app rather than the static catch-all.
#
# Checking the status code is NOT enough: the catch-all serves the static homepage with a
# perfectly healthy 200. The homepage is ~130KB, so we compare each route's body size against
# the homepage's — a route that matches it byte-for-byte is being swallowed, not served.
# We must also use https, because port 80 only 301s.
echo "--- smoke ---"
home=$(curl -sk --max-time 20 https://animica.dev/ | wc -c)
echo "  (static homepage is ${home} bytes — a route matching that size is NOT being proxied)"
fail=0
for p in /apps /cloud /functions /compute /developers /api/cloud/v1/stats; do
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 "https://animica.dev$p" || echo 000)
  size=$(curl -sk --max-time 20 "https://animica.dev$p" | wc -c)
  if [[ "$size" == "$home" ]]; then
    echo "  $p -> $code ${size}b  *** SWALLOWED BY THE STATIC CATCH-ALL ***"
    fail=1
  else
    echo "  $p -> $code ${size}b  ok"
  fi
done
[[ $fail -eq 0 ]] && echo "all Python Cloud routes are proxied to the app" || {
  echo "one or more routes are still served by the static root — check the insertion point" >&2
  exit 1
}
