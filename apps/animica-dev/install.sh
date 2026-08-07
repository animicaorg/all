#!/bin/sh
# Animica CLI installer.
#
#   curl -fsSL https://animica.dev/install.sh | sh
#
# Installs the `animica` command — an agentic coding assistant that talks to the
# Animica miner network. No API key, no wallet and no GPU are needed for the free
# tier: `animica chat` works the moment this finishes.
#
# Written in POSIX sh, not bash, because the machines people run this on include
# Alpine containers where /bin/sh is busybox and `bash` is not installed at all.
#
# It is deliberately boring:
#   * It installs into a venv under ~/.animica/venv and symlinks one binary. It
#     never touches the system Python, and never needs sudo.
#   * It refuses rather than guessing when Python is too old or missing.
#   * It tells you the exact command to undo everything.
#   * Piping a script from the internet into a shell is a real trust decision, so
#     the source is short enough to read first: curl it without `| sh` and look.

set -eu

REPO_PKG="animica"
MIN_PY_MINOR=9                       # 3.9+, matching the wheel's requires-python
HOME_DIR="${ANIMICA_HOME:-$HOME/.animica}"
VENV="$HOME_DIR/venv"
BIN_DIR="${ANIMICA_BIN_DIR:-$HOME/.local/bin}"
PRICING_URL="https://animica.dev/pricing"

# ---- output helpers --------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$(printf '\033[1m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m')
    R=$(printf '\033[31m'); D=$(printf '\033[2m'); N=$(printf '\033[0m')
else
    B=''; G=''; Y=''; R=''; D=''; N=''
fi

say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s warn%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# ---- find a usable python --------------------------------------------------
find_python() {
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
        cmd=$(command -v "$candidate" 2>/dev/null) || continue
        # Ask the interpreter its own version rather than parsing the name: on
        # several distros `python3` is a wrapper and the name says nothing.
        if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, '"$MIN_PY_MINOR"') else 1)' 2>/dev/null; then
            printf '%s' "$cmd"
            return 0
        fi
    done
    return 1
}

main() {
    say ""
    say "${B}Animica CLI${N}"
    say "${D}an agentic coding assistant on the Animica network${N}"
    say ""

    PY=$(find_python) || die "no Python 3.$MIN_PY_MINOR+ found.
  Install Python first:
    Debian/Ubuntu  sudo apt install python3 python3-venv
    Fedora         sudo dnf install python3
    Alpine         sudo apk add python3
    macOS          brew install python3"
    PY_VER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    step "using $PY (Python $PY_VER)"

    # `python3 -m venv` is a separate package on Debian and a common failure, so
    # check for it explicitly rather than letting venv creation fail obscurely.
    if ! "$PY" -c 'import venv' 2>/dev/null; then
        die "this Python has no venv module.
  Debian/Ubuntu: sudo apt install python3-venv"
    fi

    step "creating $VENV"
    mkdir -p "$HOME_DIR"
    if [ -d "$VENV" ]; then
        say "${D}   reusing the existing environment${N}"
    else
        "$PY" -m venv "$VENV" || die "could not create a virtualenv at $VENV"
    fi

    step "installing $REPO_PKG (this pulls a few MB)"
    # --upgrade so re-running the installer is also how you update.
    "$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
    if ! "$VENV/bin/python" -m pip install --quiet --upgrade "$REPO_PKG"; then
        die "pip could not install $REPO_PKG.
  Try it directly to see why:
    $VENV/bin/python -m pip install $REPO_PKG"
    fi

    VERSION=$("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("animica"))' 2>/dev/null || printf 'unknown')
    step "installed animica $VERSION"

    # ---- link one binary onto PATH ----------------------------------------
    mkdir -p "$BIN_DIR"
    ln -sf "$VENV/bin/animica" "$BIN_DIR/animica"
    step "linked $BIN_DIR/animica"

    case ":$PATH:" in
        *":$BIN_DIR:"*) ON_PATH=1 ;;
        *) ON_PATH=0 ;;
    esac

    say ""
    say "${B}done.${N}"
    say ""
    if [ "$ON_PATH" = "0" ]; then
        warn "$BIN_DIR is not on your PATH."
        say "   add it, then reopen your shell:"
        say ""
        say "     ${B}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.profile${N}"
        say ""
        say "   or run it by full path right now:"
        say ""
        say "     ${B}$BIN_DIR/animica chat${N}"
    else
        say "   ${B}animica chat${N}              talk to it"
        say "   ${B}animica chat --agentic '…'${N}  let it read and edit your code"
        say "   ${B}animica chat${N} then ${B}/swarm …${N}  run several agents at once"
    fi
    say ""
    say "${D}free: unlimited chat, 10 agentic tasks/day, 2 parallel agents."
    say "A paid plan lifts all three — $PRICING_URL${N}"
    say ""
    say "${D}uninstall: rm -rf $HOME_DIR && rm -f $BIN_DIR/animica${N}"
    say ""
}

main "$@"
