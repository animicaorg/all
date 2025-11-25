#!/usr/bin/env bash
# format.sh — One-shot code formatter & linter for mixed repos (TS/JS, Python, Shell, Markdown)
# Usage:
#   ./format.sh [--check|--write] [--staged|--all|--path DIR] [--js] [--python] [--shell] [--md]
# Examples:
#   ./format.sh                     # format everything under the repo (write/fix mode)
#   ./format.sh --check             # check only (CI), no changes written
#   ./format.sh --staged            # run only on staged files
#   ./format.sh --path contracts    # limit to a subdir
#   ./format.sh --python --check    # python only, check mode
set -euo pipefail
IFS=$'\n\t'

# ---- colors & logging --------------------------------------------------------
if [[ -t 1 ]]; then
  bold=$'\e[1m'; dim=$'\e[2m'; red=$'\e[31m'; green=$'\e[32m'; yellow=$'\e[33m'; blue=$'\e[34m'; reset=$'\e[0m'
else
  bold=''; dim=''; red=''; green=''; yellow=''; blue=''; reset=''
fi

log()   { printf '%s\n' "${dim}[$(date +%H:%M:%S)]${reset} $*"; }
info()  { printf '%s\n' "${blue}ℹ${reset}  $*"; }
good()  { printf '%s\n' "${green}✔${reset}  $*"; }
warn()  { printf '%s\n' "${yellow}⚠${reset}  $*"; }
err()   { printf '%s\n' "${red}✖${reset}  $*" >&2; }

# ---- defaults & CLI args -----------------------------------------------------
MODE="write"     # write|check
SCOPE="repo"     # repo|staged|all|path
PATH_ARG=""      # when SCOPE=path
DO_JS=1
DO_PY=1
DO_SH=1
DO_MD=1

while (( "$#" )); do
  case "$1" in
    --check) MODE="check"; shift ;;
    --write) MODE="write"; shift ;;
    --staged) SCOPE="staged"; shift ;;
    --all) SCOPE="all"; shift ;;
    --path) SCOPE="path"; PATH_ARG="${2:-}"; [[ -z "${PATH_ARG}" ]] && { err "Missing DIR after --path"; exit 2; }; shift 2 ;;
    --js) DO_JS=1; shift ;;
    --no-js) DO_JS=0; shift ;;
    --python|--py) DO_PY=1; shift ;;
    --no-python|--no-py) DO_PY=0; shift ;;
    --shell|--sh) DO_SH=1; shift ;;
    --no-shell|--no-sh) DO_SH=0; shift ;;
    --md|--markdown) DO_MD=1; shift ;;
    --no-md|--no-markdown) DO_MD=0; shift ;;
    -h|--help)
      sed -n '1,40p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) err "Unknown arg: $1"; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

# ---- helpers -----------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

# Efficient file collection with find. Excludes common vendor/build dirs.
_find_files() {
  local base="$1"; shift
  # shellcheck disable=SC2038
  find "$base" \
    -type d \( \
      -name .git -o -name node_modules -o -name dist -o -name build -o -name .venv -o -name venv \
      -o -name .mypy_cache -o -name .pytest_cache -o -name __pycache__ -o -name coverage -o -name vendor \
    \) -prune -false -o "$@"
}

# Resolve target paths based on scope
_target_roots=()
case "$SCOPE" in
  repo|all)
    _target_roots+=("$ROOT")
    ;;
  staged)
    if ! have git; then err "--staged requires git"; exit 2; fi
    mapfile -t _staged < <(git diff --name-only --cached --diff-filter=ACMRT)
    if [[ ${#_staged[@]} -eq 0 ]]; then
      warn "No staged files. Nothing to do."
      exit 0
    fi
    ;;
  path)
    if [[ -z "$PATH_ARG" ]]; then err "Missing --path DIR"; exit 2; fi
    _target_roots+=("$PATH_ARG")
    ;;
  *)
    _target_roots+=("$ROOT")
    ;;
esac

# Build file lists
declare -a JS_FILES PY_FILES SH_FILES MD_FILES

_collect_files() {
  local base="$1"
  # JS/TS/JSON/CSS/HTML/YAML (Prettier)
  mapfile -t _js < <(_find_files "$base" -type f \( \
      -name "*.js" -o -name "*.jsx" -o -name "*.ts" -o -name "*.tsx" -o \
      -name "*.json" -o -name "*.jsonc" -o -name "*.json5" -o \
      -name "*.css" -o -name "*.scss" -o \
      -name "*.html" -o -name "*.yml" -o -name "*.yaml" \
    \))
  JS_FILES+=("${_js[@]}")

  # Python
  mapfile -t _py < <(_find_files "$base" -type f -name "*.py")
  PY_FILES+=("${_py[@]}")

  # Shell scripts
  mapfile -t _sh < <(_find_files "$base" -type f \( -name "*.sh" -o -name "*.bash" \))
  SH_FILES+=("${_sh[@]}")

  # Markdown
  mapfile -t _md < <(_find_files "$base" -type f \( -name "*.md" -o -name "*.mdx" \))
  MD_FILES+=("${_md[@]}")
}

if [[ "$SCOPE" == "staged" ]]; then
  # Filter by staged set
  mapfile -t _all_targets < <(printf '%s\n' "${_staged[@]}" | sed -E "s|^|$ROOT/|")
  for f in "${_all_targets[@]}"; do
    case "$f" in
      *.js|*.jsx|*.ts|*.tsx|*.json|*.jsonc|*.json5|*.css|*.scss|*.html|*.yml|*.yaml) JS_FILES+=("$f");;
      *.py) PY_FILES+=("$f");;
      *.sh|*.bash) SH_FILES+=("$f");;
      *.md|*.mdx) MD_FILES+=("$f");;
    esac
  done
else
  for r in "${_target_roots[@]}"; do _collect_files "$r"; done
fi

# Unique & sorted
_unique() { awk '!seen[$0]++' | LC_ALL=C sort; }
mapfile -t JS_FILES < <(printf '%s\n' "${JS_FILES[@]:-}" | _unique || true)
mapfile -t PY_FILES < <(printf '%s\n' "${PY_FILES[@]:-}" | _unique || true)
mapfile -t SH_FILES < <(printf '%s\n' "${SH_FILES[@]:-}" | _unique || true)
mapfile -t MD_FILES < <(printf '%s\n' "${MD_FILES[@]:-}" | _unique || true)

# Chunked runner to avoid "Argument list too long"
_run_chunked() {
  local chunk_size="$1"; shift
  local cmd=( "$@" )
  local tmplist
  tmplist="$(mktemp)"
  cat > "$tmplist"
  local ret=0
  if [[ -s "$tmplist" ]]; then
    while IFS= read -r -d '' chunk; do
      mapfile -t files < <(printf '%s\0' "$chunk" | xargs -0 -I{} echo "{}")
      if ! "${cmd[@]}" "${files[@]}"; then
        ret=1
      fi
    done < <(python3 - <<'PY' "$tmplist" "$chunk_size"
import sys, os
fn, n = sys.argv[1], int(sys.argv[2])
with open(fn, 'rb') as f:
    items = [line.strip().decode() for line in f if line.strip()]
# pack into N-sized null-separated chunks
for i in range(0, len(items), n):
    sys.stdout.buffer.write(('\0'.join(items[i:i+n])+'\0').encode())
PY
    ); fi
  rm -f "$tmplist"
  return "$ret"
}

# ---- tools: Prettier & ESLint (JS/TS/JSON/CSS/HTML/YAML) --------------------
run_prettier() {
  local mode="$1"; shift
  local bin="prettier"
  local npx_bin=(npx --yes --no-install prettier)
  if have prettier; then :; elif have npx; then bin="${npx_bin[@]}"; else warn "Prettier not found; skipping."; return 0; fi
  local args=(--loglevel warn)
  if [[ "$mode" == "check" ]]; then
    args+=(--check)
  else
    args+=(--write)
  fi
  info "Prettier (${mode}) on ${#JS_FILES[@]} files"
  printf '%s\n' "${JS_FILES[@]}" | _run_chunked 150 $bin "${args[@]}" || return 1
  return 0
}

run_eslint() {
  local mode="$1"; shift
  # Only TS/JS files
  mapfile -t _eslint_targets < <(printf '%s\n' "${JS_FILES[@]}" | grep -E '\.(js|jsx|ts|tsx)$' || true)
  [[ ${#_eslint_targets[@]} -eq 0 ]] && { info "ESLint: no JS/TS files to lint"; return 0; }
  local npx_cmd=(npx --yes --no-install eslint)
  local bin="eslint"
  if have eslint; then :; elif have npx; then bin="${npx_cmd[@]}"; else warn "ESLint not found; skipping."; return 0; fi
  local args=(--max-warnings=0)
  if [[ "$mode" == "check" ]]; then
    :
  else
    args+=(--fix)
  fi
  info "ESLint (${mode}) on ${#_eslint_targets[@]} files"
  printf '%s\n' "${_eslint_targets[@]}" | _run_chunked 70 $bin "${args[@]}" || return 1
  return 0
}

# ---- tools: Python (Ruff formatter + linter; Black/Isort fallbacks) ----------
run_python_format() {
  local mode="$1"; shift
  [[ ${#PY_FILES[@]} -eq 0 ]] && { info "Python: no files to process"; return 0; }

  local fail=0

  if have ruff; then
    if [[ "$mode" == "check" ]]; then
      info "Ruff format (check)"
      ruff format --check --quiet "${PY_FILES[@]}" || fail=1
    else
      info "Ruff format (write)"
      ruff format --quiet "${PY_FILES[@]}" || fail=1
    fi
    # Lints/fixes
    if [[ "$mode" == "check" ]]; then
      info "Ruff check (no-fix)"
      ruff check --quiet "${PY_FILES[@]}" || fail=1
    else
      info "Ruff check (--fix)"
      ruff check --fix --quiet "${PY_FILES[@]}" || fail=1
    fi
  else
    warn "ruff not found; falling back to black/isort if available"
    if have black; then
      if [[ "$mode" == "check" ]]; then black --check "${PY_FILES[@]}" || fail=1
      else black "${PY_FILES[@]}" || fail=1; fi
    fi
    if have isort; then
      if [[ "$mode" == "check" ]]; then isort --check-only "${PY_FILES[@]}" || fail=1
      else isort "${PY_FILES[@]}" || fail=1; fi
    fi
  fi
  return "$fail"
}

# ---- tools: Shell (shfmt + shellcheck (advisory)) ---------------------------
run_shell_tools() {
  local mode="$1"; shift
  [[ ${#SH_FILES[@]} -eq 0 ]] && { info "Shell: no files to process"; return 0; }
  local fail=0
  if have shfmt; then
    if [[ "$mode" == "check" ]]; then
      info "shfmt (check)"
      shfmt -d "${SH_FILES[@]}" || fail=1
    else
      info "shfmt (write)"
      shfmt -w "${SH_FILES[@]}" || fail=1
    fi
  else
    warn "shfmt not found; skipping shell formatting"
  fi
  if have shellcheck; then
    info "shellcheck (advisory)"
    shellcheck "${SH_FILES[@]}" || true
  fi
  return "$fail"
}

# ---- tools: Markdown (Prettier + markdownlint if present) -------------------
run_markdown() {
  local mode="$1"; shift
  [[ ${#MD_FILES[@]} -eq 0 ]] && { info "Markdown: no files"; return 0; }
  local fail=0
  # Prettier already covers MD as part of JS_FILES. Ensure MD files are there too.
  if have prettier || have npx; then
    local bin="prettier"
    if ! have prettier; then bin="npx --yes --no-install prettier"; fi
    local args=()
    if [[ "$mode" == "check" ]]; then args+=(--check); else args+=(--write); fi
    info "Prettier (${mode}) for Markdown"
    printf '%s\n' "${MD_FILES[@]}" | _run_chunked 200 $bin "${args[@]}" || fail=1
  fi
  if have markdownlint; then
    info "markdownlint (strict)"
    if [[ "$mode" == "check" ]]; then
      markdownlint "${MD_FILES[@]}" || fail=1
    else
      if have markdownlint-fix; then markdownlint-fix "${MD_FILES[@]}" || true; fi
      markdownlint "${MD_FILES[@]}" || fail=1
    fi
  elif have npx; then
    info "markdownlint via npx (strict)"
    if [[ "$mode" == "check" ]]; then
      npx --yes --no-install markdownlint-cli "${MD_FILES[@]}" || fail=1
    else
      npx --yes --no-install markdownlint-cli "${MD_FILES[@]}" || true
    fi
  fi
  return "$fail"
}

# ---- orchestration -----------------------------------------------------------
overall_fail=0

if (( DO_JS )); then
  if [[ ${#JS_FILES[@]} -gt 0 ]]; then
    run_prettier "$MODE" || overall_fail=1
    run_eslint  "$MODE" || overall_fail=1
  else
    info "No JS/TS/JSON/CSS/HTML/YAML files"
  fi
fi

if (( DO_PY )); then
  run_python_format "$MODE" || overall_fail=1
fi

if (( DO_SH )); then
  run_shell_tools "$MODE" || overall_fail=1
fi

if (( DO_MD )); then
  run_markdown "$MODE" || overall_fail=1
fi

if [[ "$MODE" == "check" ]]; then
  if (( overall_fail )); then
    err "Formatting/lint checks FAILED."
    exit 1
  else
    good "All formatting/lint checks passed."
  fi
else
  good "Formatting complete."
fi
