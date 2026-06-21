#!/bin/sh
# Animica one-command miner — earn ANM with spare CPU/GPU.
#
#   curl -sL https://animica.org/mine.sh | sh -s -- --address anim1youraddr [--name worker1]
#
# It downloads the OFFICIAL signed miner bundle from the pool, verifies its
# sha256, and starts it pointed at the live stratum endpoint. No build, no config.
# The same network you mine for also sells cheap inference (see: animica-mcp).
set -eu

POOL="${ANIMICA_POOL_URL:-https://pool.animica.org}"
ADDRESS="${ANIMICA_ADDRESS:-}"
WORKER="${ANIMICA_WORKER:-bot-$(hostname 2>/dev/null | cut -c1-8 || echo node)}"
DEST="${ANIMICA_MINER_DIR:-$HOME/.animica-miner}"

while [ $# -gt 0 ]; do
  case "$1" in
    --address|-a) ADDRESS="$2"; shift 2 ;;
    --name|--worker|-w) WORKER="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$ADDRESS" ]; then
  echo "ERROR: pass your payout address:  sh mine.sh --address anim1..." >&2
  echo "Get a wallet at https://wallet.animica.org" >&2
  exit 1
fi
case "$ADDRESS" in anim1*) : ;; *) echo "ERROR: address must start with anim1..." >&2; exit 1 ;; esac

# Detect platform.
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in linux*) PLAT=linux ;; darwin*) PLAT=macos ;; *) echo "Unsupported OS: $OS (use the Windows installer from $POOL/mine)"; exit 1 ;; esac

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required tool: $1" >&2; exit 1; }; }
need curl; need unzip; need python3

echo ">> Fetching the signed Animica miner manifest…"
MANIFEST="$(curl -fsSL "$POOL/api/mining/downloads")"
# Pull the matching platform's url + sha256 + stratum endpoint (no jq dependency).
PY="import sys,json
d=json.load(sys.stdin)
ep=d.get('endpoint','stratum+tcp://pool.animica.org:3333')
it=next((i for i in d.get('items',[]) if i.get('platform')=='$PLAT'),None)
print(ep)
print((it or {}).get('url',''))
print((it or {}).get('sha256',''))
print((it or {}).get('filename',''))
print((it or {}).get('launcher',''))"
set -- $(printf '%s' "$MANIFEST" | python3 -c "$PY")
ENDPOINT="$1"; URL="$2"; SHA="$3"; FILE="$4"; LAUNCHER="$5"
[ -n "$URL" ] || { echo "No miner build for $PLAT yet — see $POOL/mine"; exit 1; }

mkdir -p "$DEST"; cd "$DEST"
echo ">> Downloading $FILE…"
curl -fsSL "$URL" -o miner.zip

if [ -n "$SHA" ]; then
  echo ">> Verifying signature (sha256)…"
  GOT="$( (sha256sum miner.zip 2>/dev/null || shasum -a 256 miner.zip) | awk '{print $1}')"
  [ "$GOT" = "$SHA" ] || { echo "CHECKSUM MISMATCH — refusing to run. expected $SHA got $GOT" >&2; exit 1; }
fi

unzip -o -q miner.zip
echo ">> Starting miner as $ADDRESS.$WORKER  →  $ENDPOINT"
export ANIMICA_ADDRESS="$ADDRESS" ANIMICA_WORKER="$WORKER" ANIMICA_POOL="$ENDPOINT"

# Prefer the bundle's launcher (it knows the right entrypoint + python fallback);
# pass the address every common way so it works regardless of its CLI shape.
if [ -n "$LAUNCHER" ] && [ -f "$LAUNCHER" ]; then
  chmod +x "$LAUNCHER" 2>/dev/null || true
  exec sh "$LAUNCHER" --address "$ADDRESS" --worker "$WORKER" --pool "$ENDPOINT"
fi
# Fallback: a *.sh launcher or a python miner in the bundle.
L="$(ls start_mining.sh start*.sh 2>/dev/null | head -1 || true)"
if [ -n "$L" ]; then chmod +x "$L" 2>/dev/null || true; exec sh "$L" --address "$ADDRESS" --worker "$WORKER" --pool "$ENDPOINT"; fi
PYM="$(ls *.py miner*.py 2>/dev/null | head -1 || true)"
if [ -n "$PYM" ]; then exec python3 "$PYM" -o "$ENDPOINT" -u "$ADDRESS.$WORKER" -p x; fi
echo "Downloaded to $DEST but could not auto-detect the launcher. Run it manually with: -o $ENDPOINT -u $ADDRESS.$WORKER" >&2
exit 1
