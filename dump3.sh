#!/usr/bin/env bash
set -euo pipefail

# ==============================================================
# dump.sh — Combine project files into THREE TXTs of ~equal size
# with HARD CAPS per part to prevent runaway sizes.
#
# Outputs in CWD:
#   animica_dump_PART1_YYYYmmdd_HHMMSS.txt   (contents + metadata)
#   animica_dump_PART2_YYYYmmdd_HHMMSS.txt   (contents + metadata)
#   animica_dump_PART3_YYYYmmdd_HHMMSS.txt   (contents + metadata)
#   animica_dump_index_YYYYmmdd_HHMMSS.txt   (flat path list)
#
# Tunables (env vars):
#   ROOT="."                 # root to scan
#   MAX_TEXT_MB=2            # max MiB included per TEXT file
#   INCLUDE_BINARIES=0       # 1 = include first 64KB hexdump of binaries
#   MAX_PART_MB=200          # hard cap per output file (safety)
#   PARTS=3                  # number of output parts (3 by default)
# ==============================================================

ROOT="${ROOT:-.}"
MAX_TEXT_MB="${MAX_TEXT_MB:-2}"
INCLUDE_BINARIES="${INCLUDE_BINARIES:-0}"
MAX_PART_MB="${MAX_PART_MB:-200}"
PARTS="${PARTS:-3}"

timestamp="$(date +%Y%m%d_%H%M%S)"
OUT1="animica_dump_PART1_${timestamp}.txt"
OUT2="animica_dump_PART2_${timestamp}.txt"
OUT3="animica_dump_PART3_${timestamp}.txt"
INDEXFILE="animica_dump_index_${timestamp}.txt"
TMPFILES="$(mktemp)"
GEN_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
DUMP_PREFIX="animica_dump_"

# Prune heavy/cache dirs entirely
PRUNE_DIRS=(
  node_modules .git dist build .next .nuxt .cache .turbo .pnpm .yarn .venv
  __pycache__ coverage .parcel-cache .svelte-kit .gradle tmp .idea .vscode
  target vendor .pytest_cache .ipynb_checkpoints .vercel .docusaurus
)

# Skip common binary/large extensions (overridden by INCLUDE_BINARIES=1)
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
  # Don't re-ingest our own dump outputs
  [[ "$base" == ${DUMP_PREFIX}* ]] && return 0
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

get_size() {
  local f="$1"
  [ -f "$f" ] || { echo 0; return; }
  if stat --version >/dev/null 2>&1; then
    stat -c %s "$f"
  else
    stat -f %z "$f"
  fi
}

# Rough estimate of BYTES a file adds to the dump
estimate_added_bytes() {
  local f="$1" size content=0 header_overhead=256 bytes_limit hexdump_cap=65536
  size="$(wc -c <"$f" | tr -d ' ')"
  bytes_limit=$(( MAX_TEXT_MB * 1024 * 1024 ))

  if is_text_file "$f"; then
    if [ "$size" -le "$bytes_limit" ]; then
      content="$size"
    else
      content="$bytes_limit"
    fi
  else
    if [ "$INCLUDE_BINARIES" = "1" ]; then
      # hexdump expands ~4–5x; under-estimate with 4x
      local cap="$hexdump_cap"
      [ "$size" -lt "$hexdump_cap" ] && cap="$size"
      content=$(( cap * 4 ))
    else
      content=64
    fi
  fi

  echo $(( header_overhead + content ))
}

emit_file_block() {
  local f="$1" dest="$2" rel size sha mtime bytes_limit
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
        printf "%s\n" "-----8<----- BEGIN BINARY HEXDUMP (first 65536 bytes) -----8<-----"
        hexdump -C -n 65536 "$f" || true
        printf "%s\n" "-----8<----- END BINARY HEXDUMP -----8<-----"
      else
        printf "%s\n" "[SKIPPED BINARY: mime/text-unknown or known binary ext]"
      fi
    fi
  } >> "$dest"
}

# ---------- Build file list (all files, then filter) ----------
log "Scanning: $ROOT"
: > "$OUT1"; : > "$OUT2"; : > "$OUT3"; : > "$INDEXFILE"

PRUNE_EXPR=()
for d in "${PRUNE_DIRS[@]}"; do PRUNE_EXPR+=( -name "$d" -o ); done
[ "${#PRUNE_EXPR[@]}" -gt 0 ] && unset 'PRUNE_EXPR[${#PRUNE_EXPR[@]}-1]'

if [ "${#PRUNE_EXPR[@]}" -gt 0 ]; then
  FIND_CMD=(find "$ROOT" \( -type d \( "${PRUNE_EXPR[@]}" \) -prune \) -o -type f -print0)
else
  FIND_CMD=(find "$ROOT" -type f -print0)
fi

# macOS 'sort' lacks -z; normalize via newline and back to NUL
"${FIND_CMD[@]}" | tr '\0' '\n' | LC_ALL=C sort | tr '\n' '\0' > "$TMPFILES"

TOTAL_FILES=$(tr -cd '\0' < "$TMPFILES" | wc -c | tr -d ' ')
log "Found $TOTAL_FILES files (before extension/binary filters)."

# ---------- Preambles ----------
ROOT_ABS="$({ command -v realpath >/dev/null 2>&1 && realpath "$ROOT"; } || echo "$ROOT")"
POLICY_LINE="# Policy: skip_binaries=$((INCLUDE_BINARIES==0)), max_text_mb=${MAX_TEXT_MB}, max_part_mb=${MAX_PART_MB}"

for part in 1 2 3; do
  dest="OUT${part}"
  {
    printf "%s\n" "# Animica Project Unified Dump — PART ${part}/3"
    printf "%s\n" "# Root: $ROOT_ABS"
    printf "%s\n" "# Generated: $GEN_TS"
    printf "%s\n" "$POLICY_LINE"
    printf "%s\n" ""
  } >> "${!dest}"
done

# ---------- First pass: collect eligible files + estimates ----------
declare -a FILES=()
declare -a ESTS=()
COUNT=0 INCLUDED=0 SKIPPED=0

while IFS= read -r -d '' f; do
  COUNT=$((COUNT+1))
  if ext_is_skipped "$f"; then
    SKIPPED=$((SKIPPED+1))
    continue
  fi
  FILES+=("$f")
  printf "%s\n" "$(relpath "$ROOT" "$f")" >> "$INDEXFILE"
  ESTS+=( "$(estimate_added_bytes "$f")" )
  INCLUDED=$((INCLUDED+1))
  if (( INCLUDED % 400 == 0 )); then log "First pass… considered $INCLUDED files"; fi
done < "$TMPFILES"
rm -f "$TMPFILES"

# ---------- Compute targets & caps ----------
TOTAL_EST=0
for e in "${ESTS[@]}"; do TOTAL_EST=$(( TOTAL_EST + e )); done
TARGET1=$(( TOTAL_EST / 3 ))
TARGET2=$(( (TOTAL_EST * 2) / 3 ))
MAX_PART_BYTES=$(( MAX_PART_MB * 1024 * 1024 ))

log "Estimated total bytes: $TOTAL_EST (targets ~ $TARGET1, $TARGET2), hard cap per part: ${MAX_PART_MB}MB"

# ---------- Emit with real-time caps ----------
DESTS=( "$OUT1" "$OUT2" "$OUT3" )
CUR_PART=0

size_of_part() { get_size "${DESTS[$1]}"; }

for i in "${!FILES[@]}"; do
  # Choose destination: prefer staying in current part if below target and cap
  # Move to next part if either cap would be exceeded.
  # Initial part selection for first file(s):
  if (( i == 0 )); then CUR_PART=0; fi

  est="${ESTS[$i]}"
  cur_size="$(size_of_part "$CUR_PART")"
  cur_target=$TARGET1
  (( CUR_PART == 1 )) && cur_target=$(( TARGET2 - TARGET1 ))
  (( CUR_PART == 2 )) && cur_target=$(( TOTAL_EST - TARGET2 ))

  # If this file would blow the cap or we've exceeded approx target by 20%, advance
  if (( cur_size + est > MAX_PART_BYTES )) || (( cur_size > (cur_target + cur_target/5) )) ; then
    (( CUR_PART++ ))
    if (( CUR_PART >= PARTS )); then CUR_PART=$((PARTS-1)); fi
    cur_size="$(size_of_part "$CUR_PART")"
  fi

  emit_file_block "${FILES[$i]}" "${DESTS[$CUR_PART]}"

  # Progress logs
  if (( (i+1) % 200 == 0 )); then
    log "Emitted $((i+1)) files so far… sizes: P1=$(get_size "${DESTS[0]}")B, P2=$(get_size "${DESTS[1]}")B, P3=$(get_size "${DESTS[2]}")B"
  fi
done

# ---------- Done ----------
log "Done. Included: $INCLUDED, Skipped (by ext): $SKIPPED, Total seen: $COUNT"
log "Final sizes: P1=$(get_size "${DESTS[0]}") bytes, P2=$(get_size "${DESTS[1]}") bytes, P3=$(get_size "${DESTS[2]}") bytes"
log "Outputs:"
log "  - ${DESTS[0]}"
log "  - ${DESTS[1]}"
log "  - ${DESTS[2]}"
log "  - $INDEXFILE"

printf "%s\n" ""
printf "%s\n" "All set!"
printf "%s\n" "  • Combined dump part 1 : ${DESTS[0]}"
printf "%s\n" "  • Combined dump part 2 : ${DESTS[1]}"
printf "%s\n" "  • Combined dump part 3 : ${DESTS[2]}"
printf "%s\n" "  • File index           : $INDEXFILE"

