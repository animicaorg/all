#!/usr/bin/env python3
"""Resolve duplicate LC_LOAD_DYLIB load commands in a Mach-O binary.

macOS 13+ dyld aborts at launch with
  "duplicate linked dylib '.../OpenGL.framework/.../OpenGL'"
when a binary lists the same dylib path twice. Our cross-compiled (osxcross)
Qt wallet links OpenGL.framework twice.

IMPORTANT: we must NOT simply delete a load command — Mach-O binds each imported
symbol to a library by its *ordinal* (index in the load-command list). Removing
one shifts every later ordinal, so symbols silently rebind to the wrong library
(e.g. QByteArray::_empty resolving against IOKit -> "Symbol not found" abort).

Instead we RENAME the duplicate occurrences to an equivalent path to the SAME
file (frameworks expose a top-level symlink, e.g.
  /System/Library/Frameworks/OpenGL.framework/Versions/A/OpenGL
  -> /System/Library/Frameworks/OpenGL.framework/OpenGL
otherwise we insert "/./"). dyld's duplicate check is on the path string, so a
different-but-equivalent string clears it while the load-command count — and
therefore every symbol ordinal — stays identical.

Re-sign after editing (signature is invalidated):  codesign --force --deep --sign - App.app
Requires `lief`. No-op (exit 0) if lief missing or there are no duplicates.
"""
import re
import sys
from collections import Counter


def _equivalent_path(p: str) -> str:
    # Foo.framework/Versions/A/Foo  ->  Foo.framework/Foo  (top-level symlink)
    m = re.match(r"^(.*/([^/]+)\.framework)/Versions/[^/]+/\2$", p)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    # Fallback: insert "/./" before the last path component (same file).
    if "/" in p:
        head, tail = p.rsplit("/", 1)
        return f"{head}/./{tail}"
    return "./" + p


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fix_macho_duplicate_dylibs.py <macho-binary> [more...]", file=sys.stderr)
        return 2
    try:
        import lief
    except Exception:
        print("[fix-macho] lief not installed; skipping (pip install lief)", file=sys.stderr)
        return 0

    rc = 0
    for path in sys.argv[1:]:
        try:
            b = lief.parse(path)
            if b is None:
                print(f"[fix-macho] {path}: not a Mach-O, skipping", file=sys.stderr)
                continue
            names = [l.name for l in b.libraries]
            dups = {k: v for k, v in Counter(names).items() if v > 1}
            if not dups:
                print(f"[fix-macho] {path}: no duplicate dylibs")
                continue
            seen = set()
            renamed = 0
            for c in b.commands:
                if isinstance(c, lief.MachO.DylibCommand):
                    if c.name in seen and c.name in dups:
                        new = _equivalent_path(c.name)
                        # keep distinct if the equivalent collides with another seen entry
                        while new in seen:
                            new = _equivalent_path(new)
                        c.name = new
                        seen.add(new)
                        renamed += 1
                    else:
                        seen.add(c.name)
            b.write(path)
            print(f"[fix-macho] {path}: renamed {renamed} duplicate load command(s) (ordinals preserved): {list(dups)}")
        except Exception as e:  # noqa: BLE001
            print(f"[fix-macho] {path}: ERROR {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
