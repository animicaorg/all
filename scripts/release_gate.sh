#!/usr/bin/env bash
# Animica PyPI release gate — run BEFORE every `twine upload`.
# Hard-fails (exit 1) if the built wheel is not release-quality.
#
#   scripts/release_gate.sh [path/to/animica-X.Y.Z-py3-none-any.whl]
#
# With no argument it builds the wheel from python/ first. Gates:
#   1. wheel hygiene: no __pycache__/.pyc/.bak/.fix/tests/secrets
#   2. compileall over the unpacked wheel (no syntax errors)
#   3. import sweep of animica.cli.* + key packages (no broken/paste-in modules)
#   4. clean-venv install + `pip check` (no broken requirements)
#   5. CLI smoke: --version, --help, up --plan, wallet new --label smoke
#   5b. vendored-artifact parity: the security code in the wheel matches git
#   6. pyflakes undefined-name count (informational)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ANIMICA_PY:-/root/animica/.venv/bin/python}"
FAIL=0
note() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  [PASS] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; FAIL=1; }

WHEEL="${1:-}"
if [ -z "$WHEEL" ]; then
  note "build wheel"
  ( cd "$ROOT/python" && rm -rf dist build _build_vendor && "$PY" -m build --wheel >/dev/null ) \
    && WHEEL="$(ls -t "$ROOT"/python/dist/animica-*.whl | head -1)"
fi
[ -f "$WHEEL" ] || { echo "no wheel: $WHEEL"; exit 2; }
echo "wheel: $WHEEL"

TMP="$(mktemp -d)"; "$PY" -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$WHEEL" "$TMP"
# Clean up the extracted wheel AND the clean-venv (set later) on any exit — otherwise
# every gate run leaks a multi-GB tree into /tmp.
_GATE_TMPDIRS=("$TMP")
trap 'rm -rf "${_GATE_TMPDIRS[@]}"' EXIT

note "1. wheel hygiene"
for pat in '__pycache__' '*.pyc' '*.bak*' '*.fix_*' '*.orig' '*.rej' 'mykey.json' 'tx.json'; do
  n=$(find "$TMP" -name "$pat" | wc -l); [ "$n" -eq 0 ] && ok "no $pat" || bad "$n x $pat present"
done
n=$(find "$TMP" -path '*/tests/*' | wc -l); [ "$n" -eq 0 ] && ok "no tests/" || bad "$n test files present"

note "2. compileall (syntax)"
if "$PY" -m compileall -q "$TMP" >/dev/null 2>&1; then ok "compileall clean"; else bad "compileall reported syntax errors"; fi

note "3. import sweep (broken/paste-in modules)"
PYTHONPATH="$TMP" "$PY" - <<PYEOF && ok "all cli + key modules import" || bad "module(s) failed to import"
import importlib, pkgutil, sys
sys.path.insert(0, "$TMP")
fails=[]
import animica.cli as cli
for m in pkgutil.iter_modules(cli.__path__):
    try: importlib.import_module("animica.cli."+m.name)
    except Exception as e: fails.append(("animica.cli."+m.name, type(e).__name__, str(e)[:80]))
for p in ("animica.ena","animica.quantum","animica.studio","animica.media"):
    try:
        pk=importlib.import_module(p)
        for m in pkgutil.iter_modules(pk.__path__):
            try: importlib.import_module(p+"."+m.name)
            except Exception as e: fails.append((p+"."+m.name, type(e).__name__, str(e)[:80]))
    except Exception as e: fails.append((p, type(e).__name__, str(e)[:80]))
# 9.0.0: the settle CLI chains into the vendored consensus stack — import the chain
# explicitly so a wheel that broke the vendoring fails the gate, not the operator.
for p in ("consensus.iou_settlement", "consensus.rewards"):
    try: importlib.import_module(p)
    except Exception as e: fails.append((p, type(e).__name__, str(e)[:80]))
for n,t,e in fails: print("    BROKEN:", n, t, e)
sys.exit(1 if fails else 0)
PYEOF

note "4. clean-venv install + pip check"
_VENV_ROOT="$(mktemp -d)"; _GATE_TMPDIRS+=("$_VENV_ROOT"); VENV="$_VENV_ROOT/v"; "$PY" -m venv "$VENV" >/dev/null 2>&1
if "$VENV/bin/pip" install -q "$WHEEL" >/dev/null 2>&1; then
  ok "pip install (base, no AI extras)"
  if "$VENV/bin/pip" check >/dev/null 2>&1; then ok "pip check: no broken requirements"; else bad "pip check found broken requirements"; fi
  note "5. CLI smoke"
  "$VENV/bin/animica" --version >/dev/null 2>&1 && ok "animica --version" || bad "animica --version"
  "$VENV/bin/animica" --help    >/dev/null 2>&1 && ok "animica --help"    || bad "animica --help"
  "$VENV/bin/animica" up --plan >/dev/null 2>&1 && ok "animica up --plan" || bad "animica up --plan"
  "$VENV/bin/animica" wallet new --label smoke >/dev/null 2>&1 && ok "animica wallet new --label smoke" || bad "animica wallet new"
else
  bad "pip install failed"
fi

note "5b. vendored-artifact parity: security code must match git, not the disk"
# hatch_build.py vendors ../rpc/ from LOCAL DISK, so a published artifact can
# silently differ from the committed tree in either direction. That is exactly
# how the wallet-RPC path-confinement and fail-closed IP check shipped in the
# 9.0.0-9.0.4 tarballs (as an uncommitted working-tree edit) and then vanished
# from 9.0.5 onward while `git diff` between the tags stayed empty
# (animicaorg/all#1867). Assert the built artifact carries them.
_VW="$TMP/rpc/methods/wallet.py"
if [ -f "$_VW" ]; then
  if grep -q "Confine any client-supplied wallet_file" "$_VW"; then
    ok "artifact: wallet_file path confinement present"
  else
    bad "artifact: wallet_file PATH CONFINEMENT MISSING (see animicaorg/all#1867)"
  fi
  if grep -q "Fail CLOSED" "$_VW"; then
    ok "artifact: _is_local_ip fails closed"
  else
    bad "artifact: _is_local_ip FAILS OPEN (see animicaorg/all#1867)"
  fi
  # And the artifact must not have drifted from the committed source at all.
  if git -C "$ROOT" show HEAD:rpc/methods/wallet.py 2>/dev/null | diff -q - "$_VW" >/dev/null 2>&1; then
    ok "artifact: rpc/methods/wallet.py is byte-identical to HEAD"
  else
    bad "artifact: rpc/methods/wallet.py DIFFERS from committed HEAD (built from a dirty tree?)"
  fi
else
  bad "artifact: rpc/methods/wallet.py not vendored into the wheel"
fi

note "6. pyflakes undefined-name (informational)"
"$PY" -m pyflakes "$TMP" 2>/dev/null | grep -c "undefined name" | sed 's/^/  undefined-name reports (mostly annotation false-positives): /'

note "RESULT"
[ "$FAIL" -eq 0 ] && { echo "  RELEASE GATE PASSED"; exit 0; } || { echo "  RELEASE GATE FAILED"; exit 1; }
