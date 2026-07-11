#!/bin/bash
# Refresh the tiny CORS JSON the network-upgrade banner polls for live height.
set -uo pipefail
OUT=/var/www/animica.dev/net-height.json
TMP="$(mktemp)"
h=$(curl -s -m 8 -X POST http://127.0.0.1:8545/rpc -H 'Content-Type: application/json' -H 'Host: rpc.animica.org' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' 2>/dev/null | \
  python3 -c 'import sys,json; print(int(json.load(sys.stdin)["result"]["height"]))' 2>/dev/null)
[ -n "${h:-}" ] || { rm -f "$TMP"; exit 0; }
printf '{"height":%s,"chainId":1,"ts":%s}\n' "$h" "$(date +%s)" > "$TMP"
chmod 644 "$TMP"; chown www-data:www-data "$TMP" 2>/dev/null
mv "$TMP" "$OUT"
