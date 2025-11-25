#!/usr/bin/env bash
#
# notarization_check.sh — spctl/stapler helpers for macOS artifacts (.app, .dmg, .pkg)
#
# Usage:
#   ./notarization_check.sh -p /path/to/App.app
#   ./notarization_check.sh -p /path/to/Wallet.dmg --expect-team-id AB12C3D4E5 --expect-bundle-id io.animica.Wallet
#   ./notarization_check.sh -p /path/to/Wallet.pkg --type pkg --quiet
#
# Exit codes:
#   0  OK
#   2  codesign/spctl failed
#   3  stapler validation failed (not stapled)
#   4  expectation mismatch (team id / bundle id)
#   5  unsupported file or parse error
#   6  DMG mount error
set -euo pipefail

# -------- args --------
PATH_ARG=""
TYPE_ARG="auto"     # app|dmg|pkg|auto
EXPECT_TEAM_ID=""
EXPECT_BUNDLE_ID=""
QUIET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--path) PATH_ARG="$2"; shift 2 ;;
    -t|--type) TYPE_ARG="$2"; shift 2 ;;
    --expect-team-id) EXPECT_TEAM_ID="$2"; shift 2 ;;
    --expect-bundle-id) EXPECT_BUNDLE_ID="$2"; shift 2 ;;
    -q|--quiet) QUIET=1; shift ;;
    -h|--help)
      sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "[notarize] Unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { [[ "$QUIET" == "1" ]] || printf "[notarize] %s\n" "$*" >&2; }
die() { printf "[notarize][ERROR] %s\n" "$*" >&2; exit "${2:-1}"; }
have() { command -v "$1" >/dev/null 2>&1; }

[[ -n "$PATH_ARG" ]] || die "missing --path" 5
[[ -e "$PATH_ARG" ]] || die "path not found: $PATH_ARG" 5

ABS_PATH="$(python3 - <<PY
import os,sys
print(os.path.abspath(sys.argv[1]))
PY
"$PATH_ARG")"

# -------- detect kind --------
detect_kind() {
  local p="$1"
  if [[ -d "$p" && "$p" == *.app ]]; then
    echo "app"; return
  fi
  if [[ -f "$p" && "$p" == *.pkg ]]; then
    echo "pkg"; return
  fi
  if [[ -f "$p" && "$p" == *.dmg ]]; then
    echo "dmg"; return
  fi
  # Fallback: file magic
  if have file; then
    local f; f="$(file -b "$p" || true)"
    [[ "$f" =~ "xar archive" ]] && { echo "pkg"; return; }
    [[ "$f" =~ "zlib compressed data" ]] && [[ "$p" =~ \.dmg$ ]] && { echo "dmg"; return; }
  fi
  echo "unknown"
}

if [[ "$TYPE_ARG" == "auto" ]]; then
  TYPE_ARG="$(detect_kind "$ABS_PATH")"
fi
[[ "$TYPE_ARG" != "unknown" ]] || die "could not determine artifact type; pass --type app|dmg|pkg" 5

# -------- tools presence --------
have spctl || die "spctl not available (macOS required)" 5
# stapler may be missing on older Xcodes; treat as warning later
STAPLER_OK=0; have stapler && STAPLER_OK=1

# -------- helpers --------
spctl_assess() {
  local kind="$1" path="$2"
  # kind: exec|open|install
  local out rc
  set +e
  out="$(spctl --assess -vv --type "$kind" "$path" 2>&1)"; rc=$?
  set -e
  echo "$rc" $'\n'"$out"
}

codesign_verify() {
  local path="$1"
  codesign --verify --deep --strict --verbose=2 "$path" 2>&1
}

codesign_details() {
  local path="$1"
  # Capture identifier, team id, authorities
  codesign -dv --verbose=4 "$path" 2>&1
}

bundle_info() {
  local path="$1"
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$path/Contents/Info.plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$path/Contents/Info.plist" 2>/dev/null || true
}

pkg_signature() {
  local path="$1"
  pkgutil --check-signature "$path" 2>&1
}

stapler_validate() {
  local path="$1"
  if [[ "$STAPLER_OK" == "1" ]]; then
    stapler validate "$path" 2>&1
  else
    echo "stapler not found; skipping stapled ticket validation"
  fi
}

mount_dmg() {
  local dmg="$1" mnt="$2"
  hdiutil attach "$dmg" -mountpoint "$mnt" -nobrowse -readonly -noverify 2>&1
}
unmount_dmg() {
  local mnt="$1"
  set +e
  hdiutil detach "$mnt" -force >/dev/null 2>&1 || hdiutil detach "$mnt" >/dev/null 2>&1
  set -e
}

parse_team_id() {
  # From codesign -dv output
  awk -F= '/^TeamIdentifier=/ {print $2; found=1} END{if(!found) print ""}'
}
parse_identifier() {
  awk -F= '/^Identifier=/ {print $2; found=1} END{if(!found) print ""}'
}

# -------- main per-type checks --------
OK=1
NOTARIZED=0
STAPLED=0
TEAM_ID=""
BUNDLE_ID=""
BUNDLE_VER=""
SRC_DESC=""
DETAILS=""
EXPECT_FAIL_REASON=""

case "$TYPE_ARG" in
  app)
    log "Checking app bundle: $ABS_PATH"
    DETAILS="$(codesign_details "$ABS_PATH")" || { echo "$DETAILS" >&2; die "codesign details failed" 2; }
    TEAM_ID="$(printf "%s\n" "$DETAILS" | parse_team_id)"
    ID_FROM_CS="$(printf "%s\n" "$DETAILS" | parse_identifier)"
    # Prefer Info.plist for bundle id
    readarray -t BI < <(bundle_info "$ABS_PATH")
    BUNDLE_ID="${BI[0]:-$ID_FROM_CS}"
    BUNDLE_VER="${BI[1]:-}"

    log "codesign --verify (deep/strict)…"
    CS_VERIFY_OUT="$(codesign_verify "$ABS_PATH")" || { echo "$CS_VERIFY_OUT" >&2; die "codesign verify failed" 2; }

    log "spctl assess (exec)…"
    read -r RC OUT < <(spctl_assess exec "$ABS_PATH")
    echo "$OUT" | sed 's/^/[spctl] /' >&2
    [[ "$RC" -eq 0 ]] || die "spctl assess failed" 2
    SRC_DESC="$(echo "$OUT" | awk -F'source=' 'NF>1{print $2;exit}' | tr -d '"')"
    [[ "$SRC_DESC" == *Notarized* ]] && NOTARIZED=1

    STAP_OUT="$(stapler_validate "$ABS_PATH")" || true
    echo "$STAP_OUT" | sed 's/^/[stapler] /' >&2
    if [[ "$STAPLER_OK" == "1" ]]; then
      [[ "$STAP_OUT" == *"The validate action worked!"* ]] && STAPLED=1 || STAPLED=0
    else
      STAPLED=0
    fi
    ;;

  pkg)
    log "Checking installer package: $ABS_PATH"
    PKG_SIG_OUT="$(pkg_signature "$ABS_PATH")" || true
    echo "$PKG_SIG_OUT" | sed 's/^/[pkgutil] /' >&2
    TEAM_ID="$(echo "$PKG_SIG_OUT" | awk -F'[()]' '/Team Identifier/ {print $2; exit}')"

    log "spctl assess (install)…"
    read -r RC OUT < <(spctl_assess install "$ABS_PATH")
    echo "$OUT" | sed 's/^/[spctl] /' >&2
    [[ "$RC" -eq 0 ]] || die "spctl assess failed" 2
    SRC_DESC="$(echo "$OUT" | awk -F'source=' 'NF>1{print $2;exit}' | tr -d '"')"
    [[ "$SRC_DESC" == *Notarized* ]] && NOTARIZED=1

    STAP_OUT="$(stapler_validate "$ABS_PATH")" || true
    echo "$STAP_OUT" | sed 's/^/[stapler] /' >&2
    if [[ "$STAPLER_OK" == "1" ]]; then
      [[ "$STAP_OUT" == *"The validate action worked!"* ]] && STAPLED=1 || STAPLED=0
    else
      STAPLED=0
    fi
    ;;

  dmg)
    log "Checking disk image: $ABS_PATH"
    log "spctl assess (open)…"
    read -r RC OUT < <(spctl_assess open "$ABS_PATH")
    echo "$OUT" | sed 's/^/[spctl] /' >&2
    [[ "$RC" -eq 0 ]] || die "spctl assess failed" 2
    SRC_DESC="$(echo "$OUT" | awk -F'source=' 'NF>1{print $2;exit}' | tr -d '"')"
    [[ "$SRC_DESC" == *Notarized* ]] && NOTARIZED=1

    STAP_OUT="$(stapler_validate "$ABS_PATH")" || true
    echo "$STAP_OUT" | sed 's/^/[stapler] /' >&2
    if [[ "$STAPLER_OK" == "1" ]]; then
      [[ "$STAP_OUT" == *"The validate action worked!"* ]] && STAPLED=1 || STAPLED=0
    else
      STAPLED=0
    fi

    TMP_MNT="$(mktemp -d "/tmp/notarize_mnt.XXXXXX")"
    trap 'unmount_dmg "$TMP_MNT"; rmdir "$TMP_MNT" 2>/dev/null || true' EXIT
    MNT_OUT="$(mount_dmg "$ABS_PATH" "$TMP_MNT")" || { echo "$MNT_OUT" >&2; die "failed to mount DMG" 6; }
    log "Mounted at $TMP_MNT"

    readarray -t APPS < <(find "$TMP_MNT" -maxdepth 2 -type d -name "*.app" -print || true)
    if [[ "${#APPS[@]}" -gt 0 ]]; then
      # Check first app (and collect ID/version/team)
      INNER="${APPS[0]}"
      log "Found inner app: $INNER"
      DETAILS="$(codesign_details "$INNER")" || { echo "$DETAILS" >&2; die "codesign details (inner) failed" 2; }
      TEAM_ID="$(printf "%s\n" "$DETAILS" | parse_team_id)"
      readarray -t BI < <(bundle_info "$INNER")
      BUNDLE_ID="${BI[0]:-$(printf "%s\n" "$DETAILS" | parse_identifier)}"
      BUNDLE_VER="${BI[1]:-}"

      CS_VERIFY_OUT="$(codesign_verify "$INNER")" || { echo "$CS_VERIFY_OUT" >&2; die "codesign verify (inner) failed" 2; }
      log "Inner app codesign OK"
    else
      log "No .app found inside DMG (skipping inner checks)."
    fi

    unmount_dmg "$TMP_MNT"
    trap - EXIT
    rmdir "$TMP_MNT" 2>/dev/null || true
    ;;

  *)
    die "unsupported type: $TYPE_ARG" 5 ;;
esac

# -------- expectations --------
if [[ -n "$EXPECT_TEAM_ID" ]]; then
  if [[ "$TEAM_ID" != "$EXPECT_TEAM_ID" ]]; then
    EXPECT_FAIL_REASON="team_id_mismatch"
    log "Expected Team ID '$EXPECT_TEAM_ID' but got '$TEAM_ID'"
    RET=4
  fi
fi
if [[ -n "$EXPECT_BUNDLE_ID" && -n "$BUNDLE_ID" ]]; then
  if [[ "$BUNDLE_ID" != "$EXPECT_BUNDLE_ID" ]]; then
    EXPECT_FAIL_REASON="${EXPECT_FAIL_REASON:+$EXPECT_FAIL_REASON,}bundle_id_mismatch"
    log "Expected Bundle ID '$EXPECT_BUNDLE_ID' but got '$BUNDLE_ID'"
    RET=4
  fi
fi

# Consider OK if spctl accepted AND (if stapler available) stapler validated
if [[ "$NOTARIZED" -eq 1 && ( "$STAPLER_OK" -eq 0 || "$STAPLED" -eq 1 ) ]]; then
  OK=1
else
  OK=0
fi

# -------- summary JSON --------
python3 - <<PY
import json, os, sys
summary = {
  "path": os.environ.get("ABS_PATH"),
  "type": os.environ.get("TYPE_ARG"),
  "notarized": ${NOTARIZED},
  "stapled": ${STAPLED},
  "staplerAvailable": ${STAPLER_OK},
  "source": "${SRC_DESC}",
  "teamId": "${TEAM_ID}",
  "bundleId": "${BUNDLE_ID}",
  "bundleVersion": "${BUNDLE_VER}",
  "expectations": {
    "expectTeamId": "${EXPECT_TEAM_ID}",
    "expectBundleId": "${EXPECT_BUNDLE_ID}",
    "failed": "${EXPECT_FAIL_REASON}"
  },
  "ok": ${OK}
}
print(json.dumps(summary, indent=2))
PY

# -------- exit code logic --------
if [[ -n "${EXPECT_FAIL_REASON:-}" ]]; then
  exit 4
fi
if [[ "$OK" -ne 1 ]]; then
  # Distinguish stapler failure vs spctl/notarization
  if [[ "$NOTARIZED" -ne 1 ]]; then
    exit 2
  else
    exit 3
  fi
fi
exit 0
