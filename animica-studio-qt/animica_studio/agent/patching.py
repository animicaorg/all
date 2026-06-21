"""Safe, correct text patching for the Animica Studio Agent.

The agent edits files in two ways: a **unified diff** (``patch_file`` with a
``diff`` argument) or a **search/replace** pair. The previous implementation
lived inline in :mod:`animica_studio.agent.tools` and was unsafe — it never
verified that a hunk's context/removed lines actually matched the source, and a
header-less diff was applied by appending every ``+`` line to the *end* of the
file. Both paths could silently corrupt the file the agent was trying to build.

This module replaces that with a real, line-oriented unified-diff applier that:

* parses ``@@ -a,b +c,d @@`` hunk headers,
* **locates** each hunk by matching its old block (context + removed lines)
  against the source — first at the stated offset, then via a bounded fuzzy
  search, then whitespace-insensitively — and refuses (raises
  :class:`PatchError`) when the block cannot be found instead of writing garbage,
* handles pure-insertion and pure-deletion hunks, ``\\ No newline at end of
  file`` markers, and header-less single-hunk diffs,
* preserves the file's original trailing-newline convention.

It has **no Qt or service dependency** and imports headless, so it is trivially
unit-testable (see ``tests/test_patching.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "PatchError",
    "apply_patch",
    "apply_unified_diff",
    "apply_search_replace",
]


class PatchError(ValueError):
    """Raised when a patch cannot be applied cleanly (never corrupt silently)."""


_HUNK_RE = re.compile(r"^@@+\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@")


@dataclass
class _Hunk:
    """One unified-diff hunk decomposed into old/new line lists."""

    old_start: int  # 1-based start line in the source (from the header)
    old_lines: list[str] = field(default_factory=list)  # context + removed
    new_lines: list[str] = field(default_factory=list)  # context + added
    # True when the hunk's last "new" line must not end with a newline.
    no_newline_at_eof: bool = False


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def apply_patch(
    original: str,
    *,
    diff: Optional[str] = None,
    search: Optional[str] = None,
    replace: Optional[str] = None,
) -> str:
    """Apply either a unified ``diff`` or a ``search``/``replace`` pair.

    Exactly one mode is used: ``diff`` takes precedence when non-empty,
    otherwise both ``search`` and ``replace`` must be provided. Raises
    :class:`PatchError` on any ambiguity or failure.
    """
    if diff:
        return apply_unified_diff(original, diff)
    if search is None or replace is None:
        raise PatchError(
            "patch requires either a 'diff' or both 'search' and 'replace'"
        )
    return apply_search_replace(original, search, replace)


def apply_search_replace(original: str, search: str, replace: str, count: int = 1) -> str:
    """Replace the first ``count`` occurrences of ``search`` with ``replace``.

    Raises :class:`PatchError` when ``search`` is empty or not present, so the
    model gets a clear error instead of a no-op that looks like success.
    """
    if search == "":
        raise PatchError("search text must not be empty")
    if search not in original:
        # Offer a whitespace-tolerant retry before giving up.
        loosened = _loose_find_replace(original, search, replace)
        if loosened is not None:
            return loosened
        raise PatchError("search text not found in file")
    return original.replace(search, replace, count)


def apply_unified_diff(original: str, diff_text: str) -> str:
    """Apply a unified diff to ``original`` and return the new text.

    Tolerant of surrounding prose/fences and missing line counts; strict about
    actually finding each hunk's anchor in the source.
    """
    trailing_newline = original.endswith("\n") or original == ""
    src = original.splitlines()

    hunks = _parse_hunks(diff_text)
    if not hunks:
        raise PatchError("no applicable hunks found in diff")

    # Apply hunks in source order, tracking a running offset so later hunks land
    # correctly after earlier ones changed the line count.
    out = list(src)
    applied_shift = 0
    search_from = 0
    for hunk in hunks:
        hint = max(0, hunk.old_start - 1 + applied_shift)
        loc = _locate(out, hunk.old_lines, hint, search_from)
        if loc is None:
            preview = (hunk.old_lines[0] if hunk.old_lines else "(insertion)")[:80]
            raise PatchError(
                f"could not locate hunk near line {hunk.old_start} "
                f"(anchor: {preview!r}); file may have changed since the diff was made"
            )
        end = loc + len(hunk.old_lines)
        out[loc:end] = hunk.new_lines
        applied_shift += len(hunk.new_lines) - len(hunk.old_lines)
        search_from = loc + len(hunk.new_lines)

    result = "\n".join(out)
    # Preserve the original trailing-newline convention unless the diff explicitly
    # marked "no newline at end of file" on its final hunk.
    last = hunks[-1]
    if result:
        if last.no_newline_at_eof:
            result = result.rstrip("\n")
        elif trailing_newline:
            result += "\n"
    return result


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _parse_hunks(diff_text: str) -> list[_Hunk]:
    lines = diff_text.splitlines()
    # Drop file headers and the surrounding fences/prose lines we don't need.
    cleaned: list[str] = []
    for ln in lines:
        if ln.startswith(("--- ", "+++ ", "diff ", "index ")):
            continue
        cleaned.append(ln)

    hunks: list[_Hunk] = []
    i = 0
    n = len(cleaned)
    saw_header = any(_HUNK_RE.match(ln) for ln in cleaned)

    if not saw_header:
        # Header-less diff: treat the contiguous +/-/space block as one hunk.
        body = [ln for ln in cleaned if ln[:1] in (" ", "+", "-", "\\")]
        if not body:
            return []
        hunk = _hunk_from_body(body, old_start=1)
        return [hunk] if (hunk.old_lines or hunk.new_lines) else []

    while i < n:
        m = _HUNK_RE.match(cleaned[i])
        if not m:
            i += 1
            continue
        old_start = int(m.group(1))
        i += 1
        body: list[str] = []
        while i < n and not _HUNK_RE.match(cleaned[i]):
            body.append(cleaned[i])
            i += 1
        hunk = _hunk_from_body(body, old_start=old_start)
        if hunk.old_lines or hunk.new_lines:
            hunks.append(hunk)
    return hunks


def _hunk_from_body(body: list[str], *, old_start: int) -> _Hunk:
    hunk = _Hunk(old_start=old_start)
    for raw in body:
        if raw == "":
            # A blank line inside a hunk is a context line for an empty source line.
            hunk.old_lines.append("")
            hunk.new_lines.append("")
            continue
        tag, text = raw[0], raw[1:]
        if tag == " ":
            hunk.old_lines.append(text)
            hunk.new_lines.append(text)
        elif tag == "-":
            hunk.old_lines.append(text)
        elif tag == "+":
            hunk.new_lines.append(text)
        elif tag == "\\":
            # "\ No newline at end of file"
            hunk.no_newline_at_eof = True
        else:
            # Unknown marker — be conservative and treat it as context.
            hunk.old_lines.append(raw)
            hunk.new_lines.append(raw)
    return hunk


# --------------------------------------------------------------------------- #
# Locating a hunk's old block in the source
# --------------------------------------------------------------------------- #
def _locate(
    src: list[str], old_lines: list[str], hint: int, search_from: int
) -> Optional[int]:
    """Return the index in ``src`` where ``old_lines`` matches, or ``None``.

    Pure-insertion hunks (no old lines) anchor at ``hint`` (clamped). Otherwise
    we try the hint first, then expand outward, then fall back to a global
    exact scan and finally a whitespace-insensitive scan.
    """
    if not old_lines:
        return min(max(hint, 0), len(src))

    length = len(old_lines)
    last_start = len(src) - length
    if last_start < 0:
        # Source shorter than the block — only a whitespace-insensitive global
        # scan could still match a prefix; give exact a shot anyway via scan.
        return _scan(src, old_lines, 0, loose=True)

    hint = min(max(hint, 0), last_start)

    # 1. Exact match at the hint.
    if _matches(src, old_lines, hint, loose=False):
        return hint
    # 2. Bounded outward search around the hint (handles small drift).
    for radius in range(1, max(64, last_start) + 1):
        for cand in (hint - radius, hint + radius):
            if 0 <= cand <= last_start and _matches(src, old_lines, cand, loose=False):
                return cand
        if hint - radius < 0 and hint + radius > last_start:
            break
    # 3. Global exact scan from ``search_from`` (then from 0).
    found = _scan(src, old_lines, search_from, loose=False)
    if found is not None:
        return found
    found = _scan(src, old_lines, 0, loose=False)
    if found is not None:
        return found
    # 4. Whitespace-insensitive global scan as a last resort.
    return _scan(src, old_lines, 0, loose=True)


def _scan(
    src: list[str], old_lines: list[str], start: int, *, loose: bool
) -> Optional[int]:
    length = len(old_lines)
    last_start = len(src) - length
    for cand in range(max(0, start), last_start + 1):
        if _matches(src, old_lines, cand, loose=loose):
            return cand
    return None


def _matches(src: list[str], old_lines: list[str], at: int, *, loose: bool) -> bool:
    if at < 0 or at + len(old_lines) > len(src):
        return False
    for k, want in enumerate(old_lines):
        got = src[at + k]
        if got == want:
            continue
        if loose and got.rstrip() == want.rstrip():
            continue
        return False
    return True


def _loose_find_replace(original: str, search: str, replace: str) -> Optional[str]:
    """Whitespace-tolerant search/replace fallback (line-trimmed block match)."""
    src = original.splitlines()
    needle = search.splitlines()
    if not needle:
        return None
    idx = _scan(src, needle, 0, loose=True)
    if idx is None:
        return None
    trailing = original.endswith("\n")
    out = src[:idx] + replace.splitlines() + src[idx + len(needle):]
    text = "\n".join(out)
    if trailing and text and not text.endswith("\n"):
        text += "\n"
    return text
