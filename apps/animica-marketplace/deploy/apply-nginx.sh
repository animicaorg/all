#!/usr/bin/env bash
# Safely wire animica.dev/marketplace into nginx. Idempotent + auto-rollback.
# Run:  ! bash /root/animica/apps/animica-marketplace/deploy/apply-nginx.sh
set -euo pipefail

CONF=/etc/nginx/sites-available/animica.dev.conf
MARK="Animica AI Marketplace (Next.js on 127.0.0.1:4950)"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/root/site-backups/animica.dev.conf.pre-marketplace.$STAMP
mkdir -p /root/site-backups

if grep -q "$MARK" "$CONF"; then
  echo "[=] Marketplace blocks already present in $CONF — nothing to do."
  exit 0
fi

cp "$CONF" "$BACKUP"
echo "[+] Backed up $CONF -> $BACKUP"

# Insert the marketplace location blocks immediately before the first `location /v1/`.
BLOCKS=/root/animica/apps/animica-marketplace/deploy/animica.dev-marketplace.nginx.conf
awk -v blocksfile="$BLOCKS" '
  BEGIN { inserted=0 }
  /location \/v1\// && !inserted {
    while ((getline line < blocksfile) > 0) {
      if (line ~ /^#/) continue;       # skip the header comments in the blocks file
      print "    " line;
    }
    print "";
    inserted=1
  }
  { print }
' "$CONF" > "$CONF.new"

mv "$CONF.new" "$CONF"

# Hard assertion: the internal-only endpoints MUST be denied at the edge. internal/* returns
# unsealed social tokens, so if this deny is missing the whole isolation model is void. Fail loudly
# rather than silently exposing them.
if ! grep -q '/api/mkt/v1/animal/internal/' "$CONF"; then
  echo "[✗] SECURITY: internal/* edge-deny is MISSING from $CONF — refusing to apply."
  echo "    Add: location ^~ /api/mkt/v1/animal/internal/ { return 404; }  (above location ^~ /api/mkt/)"
  cp "$BACKUP" "$CONF"
  exit 1
fi

if nginx -t 2>/tmp/nginx-test.$STAMP; then
  systemctl reload nginx
  echo "[✓] nginx reloaded. Marketplace is live at https://animica.dev/marketplace"
else
  echo "[✗] nginx -t FAILED — rolling back:"
  cat /tmp/nginx-test.$STAMP
  cp "$BACKUP" "$CONF"
  nginx -t && systemctl reload nginx || true
  echo "[✓] Rolled back to $BACKUP. No changes applied."
  exit 1
fi
