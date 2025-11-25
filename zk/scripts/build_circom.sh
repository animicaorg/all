#!/usr/bin/env bash
set -Eeuo pipefail

# ----------------------------------------
# Config & defaults
# ----------------------------------------
CIRCOM="${CIRCOM:-circom}"
SNARKJS="${SNARKJS:-snarkjs}"
NODE="${NODE:-node}"
ROOT="${ROOT:-zk/circuits}"
SRS_DIR="${SRS_DIR:-${ROOT}/.srs}"

# Default PTAU mirror (Hermez public). You can override with PTAU_URL env.
PTAU_URL="${PTAU_URL:-https://hermez.s3-eu-west-1.amazonaws.com}"
PTAU_POW="${PTAU_POW:-14}" # ~2^14 constraints for quick local builds
PTAU_NAME="${PTAU_NAME:-powersOfTau28_hez_final_${PTAU_POW}.ptau}"

ONLY_FILTER=""
DO_PROVE=0
FORCE=0
PTAU_OVERRIDE=""
PTAU_POW_CLI=""

# ----------------------------------------
# UX helpers
# ----------------------------------------
red()    { printf "\033[31m%s\033[0m\n" "$*" ; }
green()  { printf "\033[32m%s\033[0m\n" "$*" ; }
yellow() { printf "\033[33m%s\033[0m\n" "$*" ; }
blue()   { printf "\033[34m%s\033[0m\n" "$*" ; }

die() { red "✖ $*"; exit 1; }

usage() {
  cat <<USAGE
Animica: Circom/SnarkJS local build helper

Options:
  --only <substr>      Build only circuits whose "system/circuit" contains <substr>.
  --prove              Also generate witness/proof/public (if input_example.json exists).
  --force              Rebuild even if outputs exist.
  --ptau <path>        Use a specific Powers-of-Tau file (.ptau).
  --ptau-pow <n>       Choose PoT power (default: ${PTAU_POW}); will fetch if missing.
  -h, --help           Show this help.

Env overrides:
  CIRCOM, SNARKJS, NODE, ROOT, SRS_DIR, PTAU_URL, PTAU_NAME

Examples:
  bash zk/scripts/build_circom.sh --prove
  bash zk/scripts/build_circom.sh --only groth16/embedding
  bash zk/scripts/build_circom.sh --ptau-pow 16
USAGE
}

# ----------------------------------------
# Arg parse
# ----------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) ONLY_FILTER="$2"; shift 2;;
    --prove) DO_PROVE=1; shift;;
    --force) FORCE=1; shift;;
    --ptau) PTAU_OVERRIDE="$2"; shift 2;;
    --ptau-pow) PTAU_POW_CLI="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) die "Unknown arg: $1 (see --help)";;
  endesac # (kept for shellcheck compatibility)
done
# bash doesn't like 'endesac' but we keep the comment above for readability
true

if [[ -n "${PTAU_POW_CLI:-}" ]]; then
  PTAU_POW="$PTAU_POW_CLI"
  PTAU_NAME="powersOfTau28_hez_final_${PTAU_POW}.ptau"
fi

# ----------------------------------------
# Dependency checks
# ----------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "Missing dependency: $1"; }

need "$CIRCOM"
need "$SNARKJS"
need "$NODE"

blue "Animica Circom build"
echo "  root    : $ROOT"
echo "  srs dir : $SRS_DIR"
echo "  circom  : $($CIRCOM --version 2>/dev/null || echo '?')"
echo "  snarkjs : $($SNARKJS -v 2>/dev/null || echo '?')"
echo "  node    : $($NODE --version 2>/dev/null || echo '?')"
echo "  prove   : $DO_PROVE"
echo "  force   : $FORCE"
[[ -n "$ONLY_FILTER" ]] && echo "  filter  : $ONLY_FILTER"

mkdir -p "$SRS_DIR"

# ----------------------------------------
# Powers-of-Tau management
# ----------------------------------------
ensure_ptau() {
  local ptau="$1"
  if [[ -f "$ptau" ]]; then
    echo "✔ Using PoT: $ptau"
    return 0
  fi
  local name="$(basename "$ptau")"
  local url="${PTAU_URL%/}/$name"
  yellow "PoT not found, fetching:"
  echo "  $url"
  curl -fL "$url" -o "$ptau".part
  mv "$ptau".part "$ptau"
  green "↓ Saved PoT to $ptau"
}

get_ptau() {
  # selects override or default in SRS_DIR
  if [[ -n "$PTAU_OVERRIDE" ]]; then
    echo "$PTAU_OVERRIDE"
  else
    echo "$SRS_DIR/$PTAU_NAME"
  fi
}

# ----------------------------------------
# Build primitives
# ----------------------------------------
# compile_circom <circom-file> <outdir/build>
compile_circom() {
  local cir="$1" out_build="$2"
  mkdir -p "$out_build"
  yellow "circom → r1cs/wasm  ($cir)"
  "$CIRCOM" "$cir" --r1cs --wasm --output "$out_build"
}

# setup_zkey <system: groth16|plonk_kzg> <r1cs> <ptau> <out.zkey>
setup_zkey() {
  local sys="$1" r1cs="$2" ptau="$3" zkey_out="$4"
  case "$sys" in
    groth16)
      yellow "snarkjs groth16 setup"
      "$SNARKJS" groth16 setup "$r1cs" "$ptau" "$zkey_out"
      ;;
    plonk_kzg)
      yellow "snarkjs plonk setup"
      "$SNARKJS" plonk setup "$r1cs" "$ptau" "$zkey_out"
      ;;
    *) die "Unsupported system for setup_zkey: $sys";;
  esac
}

# export_vk <zkey> <vk.json>
export_vk() {
  local zkey="$1" vk_json="$2"
  "$SNARKJS" zkey export verificationkey "$zkey" "$vk_json"
  green "✓ wrote $vk_json"
}

# gen_witness <wasm> <input.json> <wtns>
gen_witness() {
  local wasm="$1" input="$2" wtns="$3"
  local gen_js="${wasm%.wasm}_js/generate_witness.js"
  [[ -f "$gen_js" ]] || die "Missing witness generator: $gen_js"
  "$NODE" "$gen_js" "$wasm" "$input" "$wtns"
}

# prove <system> <zkey> <wtns> <proof.json> <public.json>
prove() {
  local sys="$1" zkey="$2" wtns="$3" proof="$4" public="$5"
  case "$sys" in
    groth16)
      "$SNARKJS" groth16 prove "$zkey" "$wtns" "$proof" "$public"
      ;;
    plonk_kzg)
      "$SNARKJS" plonk prove "$zkey" "$wtns" "$proof" "$public"
      ;;
    *) die "Unsupported system for prove: $sys";;
  esac
  green "✓ wrote $proof and $public"
}

# ----------------------------------------
# Circuit registry
#   Declare circuits we know how to build via circom/snarkjs.
#   Format: "system|circuit|file"
# ----------------------------------------
REGISTRY=(
  "groth16|embedding|${ROOT}/groth16/embedding/embedding.circom"
  "groth16|storage_porep_stub|${ROOT}/groth16/storage_porep_stub/circuit.circom"
  "plonk_kzg|poseidon_hash|${ROOT}/plonk_kzg/poseidon_hash/circuit.circom"
)

# ----------------------------------------
# Build loop
# ----------------------------------------
any_built=0
ptau_file="$(get_ptau)"

for entry in "${REGISTRY[@]}"; do
  IFS='|' read -r sys name cir_path <<<"$entry"
  key="${sys}/${name}"
  [[ -n "$ONLY_FILTER" && "$key" != *"$ONLY_FILTER"* ]] && continue

  [[ -f "$cir_path" ]] || { yellow "skip (missing): $key ($cir_path)"; continue; }

  out_dir="${ROOT}/${sys}/${name}"
  build_dir="${out_dir}/build"
  mkdir -p "$build_dir"

  # Determine base names produced by circom:
  # circom names r1cs/wasm by the file's basename (without extension).
  base="$(basename "$cir_path" .circom)"
  r1cs="${build_dir}/${base}.r1cs"
  wasm="${build_dir}/${base}.wasm"
  zkey="${build_dir}/${base}.zkey"
  vk_json="${out_dir}/vk.json"
  proof_json="${out_dir}/proof.json"
  public_json="${out_dir}/public.json"
  wtns="${build_dir}/witness.wtns"
  input_json_candidate="${out_dir}/input_example.json"

  blue "→ Building $key"
  echo "  circom : $cir_path"
  echo "  outdir : $out_dir"

  # Compile step (idempotent unless --force)
  if [[ $FORCE -eq 1 || ! -f "$r1cs" || ! -f "$wasm" ]]; then
    compile_circom "$cir_path" "$build_dir"
  else
    echo "✔ compile up-to-date ($r1cs, $wasm)"
  fi

  # Ensure PoT
  ensure_ptau "$ptau_file"

  # Setup ZKey
  if [[ $FORCE -eq 1 || ! -f "$zkey" ]]; then
    setup_zkey "$sys" "$r1cs" "$ptau_file" "$zkey"
    green "✓ wrote $zkey"
  else
    echo "✔ zkey up-to-date ($zkey)"
  fi

  # Export VK
  if [[ $FORCE -eq 1 || ! -f "$vk_json" ]]; then
    export_vk "$zkey" "$vk_json"
  else
    echo "✔ vk up-to-date ($vk_json)"
  fi

  # Optionally prove if we have an input file
  if [[ $DO_PROVE -eq 1 ]]; then
    if [[ -f "$input_json_candidate" ]]; then
      yellow "Proving with $input_json_candidate"
      gen_witness "$wasm" "$input_json_candidate" "$wtns"
      prove "$sys" "$zkey" "$wtns" "$proof_json" "$public_json"
    else
      yellow "No input_example.json found at $out_dir — skipping proof."
    fi
  fi

  any_built=1
  echo
done

if [[ $any_built -eq 0 ]]; then
  yellow "No circuits built. Check --only filter or circuit paths."
else
  green "Done."
fi
