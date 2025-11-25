#!/usr/bin/env bash
# Animica Wallet — AppImage entrypoint wrapper
# Sets sane env vars inside the AppImage/AppDir and launches the bundled binary.

set -Eeuo pipefail

log()  { printf "\033[1;34m[entry]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn ]\033[0m %s\n" "$*" >&2; }

# Resolve APPDIR (AppImage runtime exports APPDIR; otherwise infer from script path)
if [[ -z "${APPDIR:-}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
  # If this script is installed as AppRun in AppDir, APPDIR is its parent.
  # If placed under usr/bin/, AppDir is two levels up.
  if [[ -d "${SCRIPT_DIR}/../usr" ]]; then
    APPDIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  else
    APPDIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
  fi
fi

# Compute LD paths for bundled libs
export APPDIR_LIBRARY_PATH="${APPDIR}/usr/lib:${APPDIR}/lib"
if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
  export LD_LIBRARY_PATH="${APPDIR_LIBRARY_PATH}:${LD_LIBRARY_PATH}"
else
  export LD_LIBRARY_PATH="${APPDIR_LIBRARY_PATH}"
fi

# Ensure icon/desktop/theme lookup sees our bundled share/
if [[ -n "${XDG_DATA_DIRS:-}" ]]; then
  export XDG_DATA_DIRS="${APPDIR}/usr/share:${XDG_DATA_DIRS}"
else
  export XDG_DATA_DIRS="${APPDIR}/usr/share:/usr/local/share:/usr/share"
fi

# Locale fallbacks (avoid GTK/Flutter warnings)
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-${LANG}}"

# Prefer Wayland if available, otherwise X11
if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  export GDK_BACKEND="${GDK_BACKEND:-wayland,x11}"
else
  export GDK_BACKEND="${GDK_BACKEND:-x11}"
fi

# Use portals for file pickers where available
export GTK_USE_PORTAL="${GTK_USE_PORTAL:-1}"

# Theme fallback (user can override)
export GTK_THEME="${GTK_THEME:-Adwaita:light}"

# Reduce potential sandbox surprises (no custom GL drivers shipped)
export LIBGL_DRIVERS_PATH="${LIBGL_DRIVERS_PATH:-/usr/lib/x86_64-linux-gnu/dri:/usr/lib/aarch64-linux-gnu/dri}"

# Locate the actual wallet binary inside the bundle
BIN_DIR="${APPDIR}/usr/lib/AnimicaWallet"
CANDIDATE="${BIN_DIR}/AnimicaWallet"
if [[ ! -x "$CANDIDATE" ]]; then
  # Fallback: first executable in BIN_DIR
  CANDIDATE="$(find "$BIN_DIR" -maxdepth 1 -type f -executable -printf '%p\n' | head -n1 || true)"
fi
[[ -n "$CANDIDATE" && -x "$CANDIDATE" ]] || {
  warn "Unable to locate executable under ${BIN_DIR}"
  exit 127
}

# Optional: print version on request without full GUI init
if [[ "${1:-}" == "--version" ]]; then
  exec "$CANDIDATE" --version
fi
if [[ "${1:-}" == "--help" ]]; then
  exec "$CANDIDATE" --help
fi

# Finally, launch the app (preserve arguments)
exec "$CANDIDATE" "$@"
