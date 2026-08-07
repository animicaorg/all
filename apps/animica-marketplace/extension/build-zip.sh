#!/usr/bin/env bash
# Package the Animica Internet browser extension into a distributable zip.
set -euo pipefail
cd "$(dirname "$0")"
OUT=dist/animica-browser.zip
mkdir -p dist
rm -f "$OUT"
zip -rq "$OUT" manifest.json background.js rules.json popup.html popup.js icons \
  -x '*/.DS_Store'
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
unzip -l "$OUT" | tail -n +2
