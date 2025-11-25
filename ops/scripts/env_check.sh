#!/usr/bin/env bash
# Animica Ops — environment sanity checks
# Checks for: docker, docker compose, kubectl, helm, terraform
#
# Usage:
#   ./ops/scripts/env_check.sh           # require Docker(+Compose); others are warnings
#   ./ops/scripts/env_check.sh --strict  # require ALL tools (fail if any missing)
#
# Env:
#   STRICT=1    # same as --strict

set -euo pipefail

STRICT="${STRICT:-0}"
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
  shift || true
fi

# -------------------- pretty output --------------------
RED=$'\033[31m'
GRN=$'\033[32m'
YEL=$'\033[33m'
CYN=$'\033[36m'
BOLD=$'\033[1m'
RST=$'\033[0m'

ok()   { printf "%b✓%b %s\n" "$GRN" "$RST" "$*"; }
warn() { printf "%b!%b %s\n" "$YEL" "$RST" "$*"; }
fail() { printf "%b✗%b %s\n" "$RED" "$RST" "$*"; }

headline() { printf "\n%s%s%s\n" "$BOLD" "$*" "$RST"; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# Semver compare using sort -V (good enough for typical tool versions)
version_ge() {
  # returns 0 if $1 >= $2
  [[ "$(printf "%s\n%s\n" "$1" "$2" | sort -V | tail -n1)" == "$1" ]]
}

# -------------------- checks ---------------------------
FAILED=0
OPTIONAL_MISSING=0

headline "Animica Ops — Environment Check"

# Docker (required)
if have_cmd docker; then
  # docker --version doesn't need daemon; docker info tests permissions/daemon
  DOCKER_VER_RAW="$(docker --version 2>/dev/null || true)"
  ok "docker found: ${CYN}${DOCKER_VER_RAW}${RST}"
  if ! docker info >/dev/null 2>&1; then
    warn "docker daemon not accessible. Ensure it is running and your user is in the 'docker' group."
  fi
else
  fail "docker not found. Install Docker Engine (20.10+ recommended)."
  FAILED=$((FAILED+1))
fi

# Docker Compose (plugin preferred)
COMPOSE_OK=0
if have_cmd docker && docker compose version >/dev/null 2>&1; then
  ok "docker compose (v2) found: $(docker compose version 2>/dev/null | head -n1)"
  COMPOSE_OK=1
elif have_cmd docker-compose; then
  warn "docker-compose (v1) found: $(docker-compose --version 2>/dev/null | head -n1) — consider upgrading to 'docker compose' v2."
  COMPOSE_OK=1
else
  fail "Docker Compose not found (neither 'docker compose' nor 'docker-compose')."
  FAILED=$((FAILED+1))
fi

# kubectl (optional unless STRICT=1)
if have_cmd kubectl; then
  K_VER="$(kubectl version --client --short 2>/dev/null || true)"
  ok "kubectl found: ${K_VER}"
  # Light connectivity hint (does not fail on no cluster)
  if kubectl config current-context >/dev/null 2>&1; then
    CTX="$(kubectl config current-context 2>/dev/null || echo '?')"
    warn "kubectl current-context: ${CTX} (cluster access not strictly required for local devnet)"
  else
    warn "kubectl has no current context configured (ok if you are not using k8s)."
  fi
else
  if [[ "$STRICT" == "1" ]]; then
    fail "kubectl not found."
    FAILED=$((FAILED+1))
  else
    warn "kubectl not found (optional). Install if you plan to use k8s deployment."
    OPTIONAL_MISSING=$((OPTIONAL_MISSING+1))
  fi
fi

# helm (optional unless STRICT=1)
if have_cmd helm; then
  H_VER="$(helm version --short 2>/dev/null || true)"
  ok "helm found: ${H_VER}"
else
  if [[ "$STRICT" == "1" ]]; then
    fail "helm not found."
    FAILED=$((FAILED+1))
  else
    warn "helm not found (optional). Install if you plan to use Helm charts."
    OPTIONAL_MISSING=$((OPTIONAL_MISSING+1))
  fi
fi

# terraform (optional unless STRICT=1)
if have_cmd terraform; then
  T_VER_RAW="$(terraform version 2>/dev/null | head -n1 || true)"
  ok "terraform found: ${T_VER_RAW}"
  # Try to extract semver and nudge if too old
  T_SEMVER="$(terraform version 2>/dev/null | sed -nE 's/^Terraform v([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -n1 || true)"
  if [[ -n "$T_SEMVER" ]] && ! version_ge "$T_SEMVER" "1.3.0"; then
    warn "terraform version ${T_SEMVER} is older than recommended (>= 1.3.0)."
  fi
else
  if [[ "$STRICT" == "1" ]]; then
    fail "terraform not found."
    FAILED=$((FAILED+1))
  else
    warn "terraform not found (optional). Install if you plan to use IaC for cloud deploys."
    OPTIONAL_MISSING=$((OPTIONAL_MISSING+1))
  fi
fi

# Summary
headline "Summary"
if [[ "$FAILED" -eq 0 ]]; then
  ok "Required tools present."
else
  fail "Missing required tools: ${FAILED} issue(s)."
fi

if [[ "$STRICT" == "0" && "$OPTIONAL_MISSING" -gt 0 ]]; then
  warn "Optional tools missing: ${OPTIONAL_MISSING}. (Run with --strict to require all.)"
fi

# Exit non-zero if any REQUIRED checks failed.
exit "$FAILED"
