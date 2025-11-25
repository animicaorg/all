#!/usr/bin/env bash
set -euo pipefail

# ==============================================================
# dump.sh — Combine project files into one TXT
# Outputs in CWD:
#   animica_dump_YYYYmmdd_HHMMSS.txt        (contents + metadata)
#   animica_dump_index_YYYYmmdd_HHMMSS.txt  (flat path list)
# Tunables (env vars):
#   ROOT="."                # root to scan
#   MAX_TEXT_MB=2           # max MiB per text file included
#   INCLUDE_BINARIES=0      # 1 = include first 256KB hexdump of binaries
# ==============================================================

ROOT="${ROOT:-.}"
MAX_TEXT_MB="${MAX_TEXT_MB:-2}"
INCLUDE_BINARIES="${INCLUDE_BINARIES:-0}"

timestamp="$(date +%Y%m%d_%H%M%S)"
OUTFILE="animica_dump_${timestamp}.txt"
INDEXFILE="animica_dump_index_${timestamp}.txt"
TMPFILES="$(mktemp)"

# Prune heavy/cache dirs entirely
PRUNE_DIRS=(
  node_modules .git dist build .next .nuxt .cache .turbo .pnpm .yarn .venv
  __pycache__ coverage .parcel-cache .svelte-kit .gradle tmp .idea .vscode
  target vendor .pytest_cache .ipynb_checkpoints .vercel .docusaurus
)

# Skip common binary/large extensions (still overridden by INCLUDE_BINARIES)
SKIP_EXTS='
  .png .jpg .jpeg .gif .webp .bmp .ico .svg .svgz
  .mp4 .mov .avi .mkv .webm .mp3 .wav .flac .ogg
  .pdf .zip .tar .gz .tgz .bz2 .xz .7z .rar .dmg .iso
  .otf .ttf .woff .woff2
  .sqlite .db
'

log() { echo "[dump] $*" >&2; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "NA"
  fi
}

is_text_file() {
  local f="$1"
  if command -v file >/dev/null 2>&1; then
    local mt
    mt="$(file -b --mime-type "$f" || true)"
    case "$mt" in
      text/*|application/json|application/javascript|application/x-javascript|application/xml|application/sql|application/typescript) return 0 ;;
      application/x-sh|text/x-shellscript|application/x-yaml|text/yaml|text/x-yaml) return 0 ;;
      *) return 1 ;;
    esac
  else
    head -c 8192 -- "$f" | LC_ALL=C grep -Iq . 2>/dev/null
  fi
}

ext_is_skipped() {
  local f="$1" base ext
  base="$(basename -- "$f")"
  ext="${base##*.}"
  [ "$ext" = "$base" ] && return 1
  ext=".$ext"
  [[ " $SKIP_EXTS " == *" $ext "* ]]
}

mtime_iso() {
  local f="$1"
  if stat --version >/dev/null 2>&1; then
    stat -c '%y' "$f" | sed 's/ /T/' | cut -d'.' -f1
  else
    stat -f '%Sm' -t '%Y-%m-%dT%H:%M:%S' "$f"
  fi
}

relpath() {
  local root="$1" p="$2" out
  if command -v realpath >/dev/null 2>&1; then
    out="$(realpath --relative-to="$root" "$p" 2>/dev/null || true)"
    [ -n "$out" ] && { printf "%s\n" "$out"; return; }
  fi
  python3 - "$root" "$p" <<'PY'
import os,sys
root, path = sys.argv[1], sys.argv[2]
try: print(os.path.relpath(path, root))
except Exception: print(path)
PY
}

emit_file_block() {
  local f="$1" rel size sha mtime bytes_limit
  rel="$(relpath "$ROOT" "$f")"
  size="$(wc -c <"$f" | tr -d ' ')"
  sha="$(sha256_file "$f")"
  mtime="$(mtime_iso "$f")"
  bytes_limit=$(( MAX_TEXT_MB * 1024 * 1024 ))

  {
    printf "%s\n" ""
    printf "%s\n" "===== FILE: $rel ====="
    printf "%s\n" "--- meta: { size_bytes: $size, sha256: $sha, mtime: $mtime }"

    if is_text_file "$f"; then
      if [ "$size" -le "$bytes_limit" ]; then
        printf "%s\n" "-----8<----- BEGIN CONTENT -----8<-----"
        cat -- "$f"
        printf "%s\n" "-----8<----- END CONTENT -----8<-----"
      else
        printf "%s\n" "-----8<----- BEGIN CONTENT (TRUNCATED to ${MAX_TEXT_MB} MiB) -----8<-----"
        if command -v head >/dev/null 2>&1; then
          head -c "$bytes_limit" -- "$f"
        else
          dd if="$f" bs=1 count="$bytes_limit" status=none
        fi
        printf "%s\n" "-----8<----- [TRUNCATED: original $size bytes] -----8<-----"
      fi
    else
      if [ "$INCLUDE_BINARIES" = "1" ]; then
        printf "%s\n" "-----8<----- BEGIN BINARY HEXDUMP (first 262144 bytes) -----8<-----"
        hexdump -C -n 262144 -- "$f" || true
        printf "%s\n" "-----8<----- END BINARY HEXDUMP -----8<-----"
      else
        printf "%s\n" "[SKIPPED BINARY: mime/text-unknown or known binary ext]"
      fi
    fi
  } >> "$OUTFILE"
}

# ---------- Build file list ----------
log "Scanning: $ROOT"
: > "$OUTFILE"
: > "$INDEXFILE"

PRUNE_EXPR=()
for d in "${PRUNE_DIRS[@]}"; do PRUNE_EXPR+=( -name "$d" -o ); done
[ "${#PRUNE_EXPR[@]}" -gt 0 ] && unset 'PRUNE_EXPR[${#PRUNE_EXPR[@]}-1]'

if [ "${#PRUNE_EXPR[@]}" -gt 0 ]; then
  FIND_CMD=(find "$ROOT" \( -type d \( "${PRUNE_EXPR[@]}" \) -prune \) -o -type f -print0)
else
  FIND_CMD=(find "$ROOT" -type f -print0)
fi

"${FIND_CMD[@]}" > "$TMPFILES"

TOTAL_FILES=$(tr -cd '\0' < "$TMPFILES" | wc -c | tr -d ' ')
log "Found $TOTAL_FILES files (before extension/binary filters)."

{
  printf "%s\n" "# Animica Project Unified Dump"
  printf "%s\n" "# Root: $({ command -v realpath >/dev/null 2>&1 && realpath "$ROOT"; } || echo "$ROOT")"
  printf "%s\n" "# Generated: $(date -Iseconds)"
  printf "%s\n" "# Policy: skipping binaries=${INCLUDE_BINARIES}, max_text_mb=${MAX_TEXT_MB}"
  printf "%s\n" ""
} >> "$OUTFILE"

COUNT=0 INCLUDED=0 SKIPPED=0
while IFS= read -r -d '' f; do
  COUNT=$((COUNT+1))

  if ext_is_skipped "$f"; then
    SKIPPED=$((SKIPPED+1))
    continue
  fi

  relp="$(relpath "$ROOT" "$f")"
  printf "%s\n" "$relp" >> "$INDEXFILE"

  emit_file_block "$f"
  INCLUDED=$((INCLUDED+1))

  if (( INCLUDED % 200 == 0 )); then log "Processed $INCLUDED files..."; fi
done < "$TMPFILES"

rm -f "$TMPFILES"

log "Done. Included: $INCLUDED, Skipped: $SKIPPED, Total seen: $COUNT"
log "Output:"
log "  - $OUTFILE"
log "  - $INDEXFILE"

printf "%s\n" ""
printf "%s\n" "All set!"
printf "%s\n" "  • Combined dump: $OUTFILE"
printf "%s\n" "  • File index    : $INDEXFILE"
