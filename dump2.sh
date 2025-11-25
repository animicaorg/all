#!/usr/bin/env bash
set -euo pipefail

# ==============================================================
# dump.sh — Combine project files into TWO TXTs of ~equal size
# Outputs in CWD:
#   animica_dump_PART1_YYYYmmdd_HHMMSS.txt   (contents + metadata)
#   animica_dump_PART2_YYYYmmdd_HHMMSS.txt   (contents + metadata)
#   animica_dump_index_YYYYmmdd_HHMMSS.txt   (flat path list)
# Tunables (env vars):
#   ROOT="."                # root to scan
#   MAX_TEXT_MB=2           # max MiB per text file included (per FILE)
#   INCLUDE_BINARIES=0      # 1 = include first 256KB hexdump of binaries
# ==============================================================

ROOT="${ROOT:-.}"
MAX_TEXT_MB="${MAX_TEXT_MB:-2}"
INCLUDE_BINARIES="${INCLUDE_BINARIES:-0}"

timestamp="$(date +%Y%m%d_%H%M%S)"
OUT1="animica_dump_PART1_${timestamp}.txt"
OUT2="animica_dump_PART2_${timestamp}.txt"
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

# Roughly estimate how many BYTES a file will add to the dump
estimate_added_bytes() {
  local f="$1" size content=0 header_overhead=256 bytes_limit hexdump_cap=262144
  size="$(wc -c <"$f" | tr -d ' ')"
  bytes_limit=$(( MAX_TEXT_MB * 1024 * 1024 ))

  if is_text_file "$f"; then
    # include up to MAX_TEXT_MB MiB of content
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
      # just a one-line notice
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
        printf "%s\n" "-----8<----- BEGIN BINARY HEXDUMP (first 262144 bytes) -----8<-----"
        hexdump -C -n 262144 -- "$f" || true
        printf "%s\n" "-----8<----- END BINARY HEXDUMP -----8<-----"
      else
        printf "%s\n" "[SKIPPED BINARY: mime/text-unknown or known binary ext]"
      fi
    fi
  } >> "$dest"
}

# ---------- Build file list (all files, then filter) ----------
log "Scanning: $ROOT"
: > "$OUT1"; : > "$OUT2"; : > "$INDEXFILE"

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

# ---------- Preambles ----------
{
  printf "%s\n" "# Animica Project Unified Dump — PART 1/2"
  printf "%s\n" "# Root: $({ command -v realpath >/dev/null 2>&1 && realpath "$ROOT"; } || echo "$ROOT")"
  printf "%s\n" "# Generated: $(date -Iseconds)"
  printf "%s\n" "# Policy: skipping binaries=${INCLUDE_BINARIES}, max_text_mb=${MAX_TEXT_MB}"
  printf "%s\n" ""
} >> "$OUT1"

{
  printf "%s\n" "# Animica Project Unified Dump — PART 2/2"
  printf "%s\n" "# Root: $({ command -v realpath >/dev/null 2>&1 && realpath "$ROOT"; } || echo "$ROOT")"
  printf "%s\n" "# Generated: $(date -Iseconds)"
  printf "%s\n" "# Policy: skipping binaries=${INCLUDE_BINARIES}, max_text_mb=${MAX_TEXT_MB}"
  printf "%s\n" ""
} >> "$OUT2"

# ---------- First pass: collect eligible files + estimates ----------
declare -a FILES=()
declare -a ESTS=()
COUNT=0 INCLUDED=0 SKIPPED=0
bytes_limit=$(( MAX_TEXT_MB * 1024 * 1024 ))

while IFS= read -r -d '' f; do
  COUNT=$((COUNT+1))
  if ext_is_skipped "$f"; then
    SKIPPED=$((SKIPPED+1))
    continue
  fi
  # Keep for index and potential emission
  FILES+=("$f")
  printf "%s\n" "$(relpath "$ROOT" "$f")" >> "$INDEXFILE"

  # Estimate contribution
  ESTS+=( "$(estimate_added_bytes "$f")" )
  INCLUDED=$((INCLUDED+1))

  if (( INCLUDED % 400 == 0 )); then log "First pass… considered $INCLUDED files"; fi
done < "$TMPFILES"

rm -f "$TMPFILES"

# ---------- Decide split point ----------
TOTAL_EST=0
for e in "${ESTS[@]}"; do TOTAL_EST=$(( TOTAL_EST + e )); done
HALF_EST=$(( TOTAL_EST / 2 ))

log "Estimated total output bytes: $TOTAL_EST (target per part ≈ $HALF_EST)"

CUM=0
SPLIT_IDX=0
for i in "${!ESTS[@]}"; do
  local_add="${ESTS[$i]}"
  # Put as many as possible into PART 1 without exceeding HALF_EST (best-effort)
  if (( CUM + local_add <= HALF_EST )); then
    CUM=$(( CUM + local_add ))
    SPLIT_IDX=$(( i + 1 ))
  else
    break
  fi
done

log "Split index at $SPLIT_IDX of ${#FILES[@]} files (part1 est ~ $CUM bytes, part2 est ~ $(( TOTAL_EST - CUM )) bytes)"

# ---------- Second pass: emit to the chosen part ----------
emit_range() {
  local start="$1" end="$2" dest="$3"
  local n=0
  for (( i=start; i<end; i++ )); do
    emit_file_block "${FILES[$i]}" "$dest"
    n=$((n+1))
    if (( n % 200 == 0 )); then log "Emitted $n files to $(basename "$dest")…"; fi
  done
}

emit_range 0 "$SPLIT_IDX" "$OUT1"
emit_range "$SPLIT_IDX" "${#FILES[@]}" "$OUT2"

# ---------- Done ----------
log "Done. Included: $INCLUDED, Skipped (by ext): $SKIPPED, Total seen: $COUNT"
log "Outputs:"
log "  - $OUT1"
log "  - $OUT2"
log "  - $INDEXFILE"

printf "%s\n" ""
printf "%s\n" "All set!"
printf "%s\n" "  • Combined dump part 1 : $OUT1"
printf "%s\n" "  • Combined dump part 2 : $OUT2"
printf "%s\n" "  • File index           : $INDEXFILE"
