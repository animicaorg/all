#!/usr/bin/env bash
set -euo pipefail

# push_images.sh — Build & push multi-arch images for Animica services via docker buildx.
#
# Features
# - Multi-arch (linux/amd64, linux/arm64) with BuildKit
# - Auto-discovers services with Dockerfiles (or accept an allowlist)
# - Version tagging from git describe (override via VERSION=…)
# - Optional :latest tag, SBOM/provenance if supported
# - Per-service overrides (context / dockerfile)
#
# Usage:
#   ./ops/scripts/push_images.sh                     # autodiscover, push all found
#   SERVICES="rpc,da,aicf,studio-services" ./ops/scripts/push_images.sh
#   REGISTRY=ghcr.io NAMESPACE=myorg ./ops/scripts/push_images.sh --latest
#   PLATFORMS=linux/amd64 ./ops/scripts/push_images.sh --no-cache
#
# Env Vars:
#   REGISTRY   (default: docker.io)
#   NAMESPACE  (default: animica)
#   VERSION    (default: git describe --tags --dirty --always)
#   PLATFORMS  (default: linux/amd64,linux/arm64)
#   PUSH       (default: true) set to "false" to do a dry run (no push)
#   BUILDER    (default: animica-builder)
#   SERVICES   Optional CSV allowlist (e.g. "rpc,da,studio-services")
#   LATEST     (default: false) set to "true" or pass --latest to add :latest tag
#
# Per-service overrides (env):
#   <SERVICE>_CONTEXT    (e.g. RPC_CONTEXT=./rpc)
#   <SERVICE>_DOCKERFILE (e.g. RPC_DOCKERFILE=./rpc/Dockerfile.ci)
#
# Notes:
# - If a mapped Dockerfile is missing, the service is skipped (warning).
# - This script only orchestrates builds; Dockerfiles live in each service.

REGISTRY="${REGISTRY:-docker.io}"
NAMESPACE="${NAMESPACE:-animica}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH="${PUSH:-true}"
BUILDER="${BUILDER:-animica-builder}"
LATEST="${LATEST:-false}"
NO_CACHE="false"

# Parse flags
while [[ "${1:-}" =~ ^- ]]; do
  case "$1" in
    --latest) LATEST="true"; shift ;;
    --no-cache) NO_CACHE="true"; shift ;;
    -h|--help)
      sed -n '1,120p' "$0"; exit 0 ;;
    *)
      echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Compute VERSION
if [[ -z "${VERSION:-}" ]]; then
  if git rev-parse --git-dir >/dev/null 2>&1; then
    VERSION="$(git describe --tags --dirty --always 2>/dev/null || true)"
  fi
  VERSION="${VERSION:-0.0.0+local}"
fi

# Ensure docker & buildx
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found in PATH" >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "ERROR: docker buildx not available (Docker 19.03+ required)" >&2
  exit 1
fi

# Create/ensure builder
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi
docker buildx use "$BUILDER" >/dev/null

# ---------- Service mapping ---------------------------------------------------
# Known services (may be skipped if their Dockerfile is absent):
#  - rpc               ./rpc
#  - da                ./da
#  - aicf              ./aicf
#  - randomness        ./randomness
#  - studio-services   ./studio-services (Dockerfile listed in tree)
#  - explorer-web      ./explorer-web
#  - studio-web        ./studio-web
#  - p2p               ./p2p
#  - mining            ./mining
#  - node              ./
#
# You can override context & dockerfile via env (RPC_CONTEXT, RPC_DOCKERFILE, etc).

declare -A MAP_CONTEXT=(
  [rpc]="${RPC_CONTEXT:-./rpc}"
  [da]="${DA_CONTEXT:-./da}"
  [aicf]="${AICF_CONTEXT:-./aicf}"
  [randomness]="${RANDOMNESS_CONTEXT:-./randomness}"
  [studio-services]="${STUDIO_SERVICES_CONTEXT:-./studio-services}"
  [explorer-web]="${EXPLORER_WEB_CONTEXT:-./explorer-web}"
  [studio-web]="${STUDIO_WEB_CONTEXT:-./studio-web}"
  [p2p]="${P2P_CONTEXT:-./p2p}"
  [mining]="${MINING_CONTEXT:-./mining}"
  [node]="${NODE_CONTEXT:-./}"
)

declare -A MAP_DOCKERFILE=(
  [rpc]="${RPC_DOCKERFILE:-${MAP_CONTEXT[rpc]}/Dockerfile}"
  [da]="${DA_DOCKERFILE:-${MAP_CONTEXT[da]}/Dockerfile}"
  [aicf]="${AICF_DOCKERFILE:-${MAP_CONTEXT[aicf]}/Dockerfile}"
  [randomness]="${RANDOMNESS_DOCKERFILE:-${MAP_CONTEXT[randomness]}/Dockerfile}"
  [studio-services]="${STUDIO_SERVICES_DOCKERFILE:-${MAP_CONTEXT[studio-services]}/Dockerfile}"
  [explorer-web]="${EXPLORER_WEB_DOCKERFILE:-${MAP_CONTEXT[explorer-web]}/Dockerfile}"
  [studio-web]="${STUDIO_WEB_DOCKERFILE:-${MAP_CONTEXT[studio-web]}/Dockerfile}"
  [p2p]="${P2P_DOCKERFILE:-${MAP_CONTEXT[p2p]}/Dockerfile}"
  [mining]="${MINING_DOCKERFILE:-${MAP_CONTEXT[mining]}/Dockerfile}"
  [node]="${NODE_DOCKERFILE:-${MAP_CONTEXT[node]}/Dockerfile}"
)

# Build allowlist
IFS=',' read -r -a ALL_IDS <<< "rpc,da,aicf,randomness,studio-services,explorer-web,studio-web,p2p,mining,node"
SERVICES_CSV="${SERVICES:-}"
if [[ -n "$SERVICES_CSV" ]]; then
  IFS=',' read -r -a IDS <<< "$SERVICES_CSV"
else
  # Autodiscover: include those whose Dockerfile exists
  IDS=()
  for id in "${ALL_IDS[@]}"; do
    df="${MAP_DOCKERFILE[$id]}"
    if [[ -f "$df" ]]; then
      IDS+=("$id")
    fi
  done
fi

if [[ "${#IDS[@]}" -eq 0 ]]; then
  echo "No services selected and no Dockerfiles found for defaults. Nothing to do." >&2
  exit 0
fi

echo "▶ Registry: ${REGISTRY}/${NAMESPACE}"
echo "▶ Builder:  ${BUILDER}"
echo "▶ Version:  ${VERSION}"
echo "▶ Platforms:${PLATFORMS}"
echo "▶ Services: ${IDS[*]}"
echo

# Check SBOM/provenance support (Buildx 0.10+)
USE_META_FLAGS="false"
if docker buildx build --help | grep -q -- '--provenance'; then
  USE_META_FLAGS="true"
fi

# Build args common
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GIT_COMMIT="$(git rev-parse --short=12 HEAD 2>/dev/null || echo 'unknown')"

# Keep a summary
BUILT_IMAGES=()
SKIPPED_SERVICES=()

build_one() {
  local id="$1"
  local ctx="${MAP_CONTEXT[$id]}"
  local df="${MAP_DOCKERFILE[$id]}"

  if [[ ! -f "$df" ]]; then
    echo "⚠  Skip ${id}: Dockerfile not found at ${df}" >&2
    SKIPPED_SERVICES+=("$id")
    return 0
  fi

  local image_base="${REGISTRY}/${NAMESPACE}/${id}"
  local tag_ver="${image_base}:${VERSION}"
  local tag_latest="${image_base}:latest"

  echo "────────────────────────────────────────────────────────"
  echo "🚢 Building ${id}"
  echo "  Context:    ${ctx}"
  echo "  Dockerfile: ${df}"
  echo "  Tags:       ${tag_ver}$( [[ "$LATEST" == "true" ]] && echo ", ${tag_latest}")"
  echo

  # assemble args
  local args=(
    --platform "${PLATFORMS}"
    --file "${df}"
    --tag "${tag_ver}"
    --build-arg "VERSION=${VERSION}"
    --build-arg "BUILD_DATE=${BUILD_DATE}"
    --build-arg "GIT_COMMIT=${GIT_COMMIT}"
    --label "org.opencontainers.image.title=${id}"
    --label "org.opencontainers.image.description=Animica ${id} service"
    --label "org.opencontainers.image.version=${VERSION}"
    --label "org.opencontainers.image.revision=${GIT_COMMIT}"
    --label "org.opencontainers.image.created=${BUILD_DATE}"
  )
  if [[ "$LATEST" == "true" ]]; then
    args+=( --tag "${tag_latest}" )
  fi
  if [[ "$NO_CACHE" == "true" ]]; then
    args+=( --no-cache )
  fi
  if [[ "$USE_META_FLAGS" == "true" ]]; then
    # Best-effort SBOM/provenance
    args+=( --provenance=true --sbom=true )
  fi
  if [[ "$PUSH" == "true" ]]; then
    args+=( --push )
  else
    echo "  (dry-run: not pushing; will load local image for primary arch if possible)"
    args+=( --load )
  fi

  docker buildx build "${args[@]}" "${ctx}"

  BUILT_IMAGES+=("$tag_ver")
  if [[ "$LATEST" == "true" ]]; then
    BUILT_IMAGES+=("$tag_latest")
  fi
}

for id in "${IDS[@]}"; do
  build_one "$id"
done

echo
echo "✅ Done. Built/published images:"
for img in "${BUILT_IMAGES[@]:-}"; do
  echo "  - ${img}"
done

if [[ "${#SKIPPED_SERVICES[@]}" -gt 0 ]]; then
  echo
  echo "ℹ Skipped services (no Dockerfile found): ${SKIPPED_SERVICES[*]}"
fi
