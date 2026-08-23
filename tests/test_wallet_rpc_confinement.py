"""Wallet-RPC hardening that must never leave the tree again.

Both protections asserted here shipped in the animica 9.0.0-9.0.4 PyPI
tarballs while existing ONLY as an uncommitted edit in the build host's
working tree. ``hatch_build.py`` vendors ``../rpc/`` from local disk, so the
artifact captured whatever happened to be on that disk; when the edit went
away the protections silently vanished from 9.0.5 onward and every release
since, including 10.4.4, while ``git diff v9.0.4..v9.0.5 -- rpc/methods/
wallet.py`` stayed empty. Reported as animicaorg/all#1867.

The fix is not only the code — it is that the code is now in git with a test
that fails without it.
"""
from __future__ import annotations

import ipaddress  # noqa: F401  (imported by the module under test)
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rpc import errors as rpc_errors  # noqa: E402
from rpc.methods import wallet as wallet_rpc  # noqa: E402


@pytest.fixture()
def wallet_dir(tmp_path, monkeypatch):
    """Point the node's default wallet file inside an isolated directory."""
    d = tmp_path / "walletdir"
    d.mkdir()
    monkeypatch.setenv("ANIMICA_WALLETS_FILE", str(d / "wallets.json"))
    return d


# --------------------------------------------------------------------------
# 1. _wallet_path: a client-supplied wallet_file may never escape the dir
# --------------------------------------------------------------------------

def test_default_wallet_file_is_used_when_none_supplied(wallet_dir):
    assert wallet_rpc._wallet_path(None) == (wallet_dir / "wallets.json").resolve()


def test_a_bare_name_resolves_inside_the_wallet_directory(wallet_dir):
    got = wallet_rpc._wallet_path("second.json")
    assert got == (wallet_dir / "second.json").resolve()
    assert got.parent == wallet_dir.resolve()


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../etc/passwd",
        "../../../../home/victim/.animica/wallets.json",
        "/etc/passwd",
        "/home/victim/.animica/wallets.json",
        "..%2f..%2fetc%2fpasswd",
        "subdir/../../escape.json",
        "....//....//etc/passwd",
    ],
)
def test_traversal_and_absolute_paths_cannot_escape(wallet_dir, hostile):
    """The signer must never be pointed at another user's key store.

    Either the call is refused, or it is confined to the wallet directory.
    What must NOT happen is the caller's path being used verbatim.
    """
    try:
        got = wallet_rpc._wallet_path(hostile)
    except rpc_errors.InvalidParams:
        return
    assert got.parent == wallet_dir.resolve(), f"{hostile!r} escaped to {got}"
    assert str(got) != str(Path(hostile))


def test_a_symlink_out_of_the_wallet_dir_is_refused(wallet_dir, tmp_path):
    outside = tmp_path / "victim.json"
    outside.write_text("{}", encoding="utf-8")
    link = wallet_dir / "innocent.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("symlinks unavailable here")
    # .resolve() follows the link, so the parent check catches the escape.
    with pytest.raises(rpc_errors.InvalidParams):
        wallet_rpc._wallet_path("innocent.json")


def test_empty_and_dot_names_are_refused(wallet_dir):
    for bad in ("", ".", "..", "/", "///"):
        try:
            got = wallet_rpc._wallet_path(bad)
        except rpc_errors.InvalidParams:
            continue
        # "" is falsy and legitimately means "use the default".
        assert got == (wallet_dir / "wallets.json").resolve(), bad


# --------------------------------------------------------------------------
# 2. _is_local_ip: an undeterminable peer is NOT local
# --------------------------------------------------------------------------

@pytest.mark.parametrize("unknown", [None, "", "not-an-ip", "999.999.999.999"])
def test_unknown_peer_is_not_local(unknown):
    """A fund-moving auth check must never treat "unknown" as "trusted"."""
    assert wallet_rpc._is_local_ip(unknown) is False


def test_loopback_is_local():
    assert wallet_rpc._is_local_ip("127.0.0.1") is True
    assert wallet_rpc._is_local_ip("::1") is True


def test_public_address_is_not_local():
    assert wallet_rpc._is_local_ip("8.8.8.8") is False


def test_private_ranges_follow_the_env_switch(monkeypatch):
    monkeypatch.delenv("ANIMICA_WALLET_RPC_ALLOW_PRIVATE", raising=False)
    assert wallet_rpc._is_local_ip("10.0.0.5") is True
    monkeypatch.setenv("ANIMICA_WALLET_RPC_ALLOW_PRIVATE", "0")
    assert wallet_rpc._is_local_ip("10.0.0.5") is False
    # ...but the switch must not re-open the unknown-peer hole.
    assert wallet_rpc._is_local_ip(None) is False


# --------------------------------------------------------------------------
# 3. The source itself, because the artifact is vendored from disk
# --------------------------------------------------------------------------

def test_the_protections_are_present_in_the_source_file():
    """Guards against a vendored copy that silently lost them (#1867)."""
    src = Path(wallet_rpc.__file__).read_text(encoding="utf-8")
    assert "Confine any client-supplied wallet_file" in src
    assert "Fail CLOSED" in src
