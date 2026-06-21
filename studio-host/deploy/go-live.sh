#!/bin/bash
# Flip the hosted Studio live on studio.animica.org.
#
#   sudo bash deploy/go-live.sh            # authenticated users only (anon OFF) — recommended
#   sudo bash deploy/go-live.sh --allow-anon   # also enable anonymous sessions (public shell)
#
# This installs a boot-persistent systemd service for the broker AND repoints
# nginx at it, i.e. it exposes a shell-capable agent on the public internet.
# Run it yourself, deliberately. Stop/undo with deploy/stop.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
CONF=/etc/nginx/sites-available/studio.animica.org.conf
ALLOW_ANON=0
[ "${1:-}" = "--allow-anon" ] && ALLOW_ANON=1

# 1) anon policy into .env
touch .env
grep -q '^STUDIO_ALLOW_ANON=' .env && sed -i "s/^STUDIO_ALLOW_ANON=.*/STUDIO_ALLOW_ANON=$ALLOW_ANON/" .env || echo "STUDIO_ALLOW_ANON=$ALLOW_ANON" >> .env
echo "anonymous sessions: $([ $ALLOW_ANON = 1 ] && echo ENABLED || echo disabled)"

# 2) systemd service with the real node path
NODE="$(command -v node)"
sed "s#^ExecStart=.*#ExecStart=${NODE} src/server.js#" deploy/studio-host.service > /etc/systemd/system/studio-host.service
systemctl daemon-reload
systemctl enable --now studio-host
sleep 2
systemctl is-active --quiet studio-host || { echo "broker failed to start"; journalctl -u studio-host -n 30 --no-pager; exit 1; }

# 3) repoint nginx (swap the marked static block for the broker proxy)
python3 - "$CONF" "$ROOT/deploy/nginx-location.conf" <<'PY'
import sys, re
conf, snippet = sys.argv[1], sys.argv[2]
text = open(conf).read()
proxy = open(snippet).read()
proxy = "\n".join(l for l in proxy.splitlines() if not l.startswith('#'))
# Replace the first `location / { ... }` block (and any preceding NOTE comment).
pat = re.compile(r'(?:[ \t]*#[^\n]*\n)*[ \t]*location\s*/\s*\{.*?\n[ \t]*\}\n', re.S)
new = pat.sub("    " + proxy.strip().replace("\n", "\n    ") + "\n", text, count=1)
open(conf, 'w').write(new)
print("nginx vhost updated")
PY
nginx -t
systemctl reload nginx
echo "LIVE: https://studio.animica.org/  (broker active, nginx reloaded)"
