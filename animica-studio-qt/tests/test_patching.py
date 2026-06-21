"""Tests for the safe patch applier (animica_studio.agent.patching)."""
from __future__ import annotations

import pytest

from animica_studio.agent.patching import (
    PatchError,
    apply_patch,
    apply_search_replace,
    apply_unified_diff,
)


def test_search_replace_basic():
    assert apply_search_replace("hello world", "world", "there") == "hello there"


def test_search_replace_only_first():
    assert apply_search_replace("a a a", "a", "b") == "b a a"


def test_search_replace_missing_raises():
    with pytest.raises(PatchError):
        apply_search_replace("abc", "xyz", "q")


def test_search_replace_whitespace_tolerant():
    original = "def f():\n    return 1\n"
    # Search text has different trailing whitespace but same content.
    out = apply_search_replace(original, "def f():\n    return 1", "def f():\n    return 2")
    assert "return 2" in out


def test_unified_diff_simple_change():
    original = "line1\nline2\nline3\n"
    diff = (
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2-changed\n"
        " line3\n"
    )
    out = apply_unified_diff(original, diff)
    assert out == "line1\nline2-changed\nline3\n"


def test_unified_diff_insertion():
    original = "a\nb\nc\n"
    diff = (
        "@@ -2,1 +2,2 @@\n"
        " b\n"
        "+inserted\n"
    )
    out = apply_unified_diff(original, diff)
    assert out == "a\nb\ninserted\nc\n"


def test_unified_diff_deletion():
    original = "a\nb\nc\n"
    diff = (
        "@@ -1,3 +1,2 @@\n"
        " a\n"
        "-b\n"
        " c\n"
    )
    out = apply_unified_diff(original, diff)
    assert out == "a\nc\n"


def test_unified_diff_wrong_offset_still_locates():
    # Header says line 1 but the real anchor is later; the locator must find it.
    original = "x\ny\nz\ntarget\nafter\n"
    diff = (
        "@@ -1,2 +1,2 @@\n"
        " target\n"
        "-after\n"
        "+AFTER\n"
    )
    out = apply_unified_diff(original, diff)
    assert out == "x\ny\nz\ntarget\nAFTER\n"


def test_unified_diff_no_match_raises_not_corrupts():
    original = "alpha\nbeta\n"
    diff = (
        "@@ -1,1 +1,1 @@\n"
        "-this line does not exist anywhere\n"
        "+replacement\n"
    )
    with pytest.raises(PatchError):
        apply_unified_diff(original, diff)
    # The original must be untouched by a failed apply (caller keeps old text).
    assert original == "alpha\nbeta\n"


def test_unified_diff_multi_hunk():
    original = "1\n2\n3\n4\n5\n6\n"
    diff = (
        "@@ -1,1 +1,1 @@\n"
        "-1\n"
        "+one\n"
        "@@ -5,1 +5,1 @@\n"
        "-5\n"
        "+five\n"
    )
    out = apply_unified_diff(original, diff)
    assert out == "one\n2\n3\n4\nfive\n6\n"


def test_headerless_diff_locates_block():
    original = "keep\nold\ntail\n"
    diff = " keep\n-old\n+new\n tail\n"
    out = apply_unified_diff(original, diff)
    assert out == "keep\nnew\ntail\n"


def test_no_eof_newline_marker():
    original = "a\nb\n"
    diff = (
        "@@ -2,1 +2,1 @@\n"
        "-b\n"
        "+b-no-newline\n"
        "\\ No newline at end of file\n"
    )
    out = apply_unified_diff(original, diff)
    assert out == "a\nb-no-newline"


def test_apply_patch_dispatch():
    assert apply_patch("a b", search="b", replace="c") == "a c"
    assert apply_patch("a\n", diff="@@ -1 +1 @@\n-a\n+z\n") == "z\n"
    with pytest.raises(PatchError):
        apply_patch("x", search=None, replace=None)


def test_does_not_append_to_eof_on_loose_diff():
    # Regression: the old loose applier appended additions at EOF, corrupting files.
    original = "head\nmid\nfoot\n"
    diff = " head\n-mid\n+MID\n foot\n"
    out = apply_unified_diff(original, diff)
    assert out == "head\nMID\nfoot\n"
    # MID must replace mid in place, not be appended after foot.
    assert not out.endswith("foot\nMID\n")
