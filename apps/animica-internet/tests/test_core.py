"""
Hermetic unit tests for the Animica Internet app's pure-logic layer (no Qt, no network).
Covers: CID computation/verification, name validation + fee schedule, reservation memo binding
and the pay-the-Foundation orchestration (with a mocked wallet + registry).
"""

from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from animica_internet import config, names, resolver, serve  # noqa: E402


# ------------------------------------------------------------------ CID / content
def test_cid_matches_marketplace_formula():
    data = b"<!doctype html><h1>hi</h1>"
    assert resolver.compute_cid(data) == "anm1c" + hashlib.sha3_256(data).hexdigest()
    assert resolver.is_cid(resolver.compute_cid(data))
    assert not resolver.is_cid("anm1cZZZ")
    assert not resolver.is_cid("bafy...")


def test_fetch_content_rejects_tampered_bytes(monkeypatch):
    good = b"<h1>real</h1>"
    cid = resolver.compute_cid(good)

    class _Resp:
        def __init__(self, b): self._b = b
        def read(self, *_a): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    # server returns DIFFERENT bytes than the CID promises -> must raise, never return
    monkeypatch.setattr(resolver.urllib.request, "urlopen", lambda *a, **k: _Resp(b"<h1>evil</h1>"))
    with pytest.raises(resolver.ContentVerifyError):
        resolver.fetch_content(cid)

    # correct bytes verify
    monkeypatch.setattr(resolver.urllib.request, "urlopen", lambda *a, **k: _Resp(good))
    assert resolver.fetch_content(cid) == good


def test_normalize_name_strips_scheme_suffix_and_path():
    assert resolver.normalize_name("Foo.anm") == "foo"
    assert resolver.normalize_name("anm://bar/baz") == "bar"
    assert resolver.normalize_name("  QUX  ") == "qux"


# ------------------------------------------------------------------ names + fees
def test_name_validation():
    assert names.validate_name("hello") == "hello"
    assert names.validate_name("My-Site.anm") == "my-site"
    for bad in ("a", "-x", "x-", "a--b", "UP!", "anm", "admin", "x" * 64):
        with pytest.raises(names.ReserveError):
            names.validate_name(bad)


def test_fee_schedule_matches_ans():
    assert config.registration_fee_anm("abc", 1) == 500      # <=3
    assert config.registration_fee_anm("abcde", 1) == 100    # <=5
    assert config.registration_fee_anm("abcdefgh", 1) == 25  # <=8
    assert config.registration_fee_anm("abcdefghi", 1) == 5  # 9+
    assert config.registration_fee_anm("abcdefghi", 3) == 15  # per-year * years
    assert config.registration_fee_anm("abc", 99) == 500 * 10  # years clamp 1..10


def test_reservation_quote():
    q = names.reservation_quote("mysite", 2)   # 6 chars -> 25 ANM/yr
    assert q["name"] == "mysite" and q["years"] == 2
    assert q["feeAnm"] == 25 * 2 and q["feeNanm"] == 25 * 2 * config.NANM_PER_ANM
    assert q["foundation"] == config.FOUNDATION_ADDRESS


def test_reserve_logs_in_then_registers():
    calls = {}

    class _Wallet:
        def primary_address(self): return "anim1payer"

    class _Reg:
        def login(self, wallet, *, address=None): calls["login"] = True
        def register(self, name, *, years, kind="app"):
            calls["register"] = (name, years, kind)
            return {"domain": {"name": name, "fqdn": f"{name}.anm"}, "feeAnm": 50}

    out = names.reserve(_Wallet(), _Reg(), "mysite", years=2)
    assert calls.get("login") and calls["register"] == ("mysite", 2, "app")
    assert out["name"] == "mysite" and out["feeAnm"] == 50 and out["domain"]["fqdn"] == "mysite.anm"


def test_reserve_insufficient_balance_surfaces_deposit_address():
    class _Wallet:
        def primary_address(self): return "anim1payer"

    class _Reg:
        def login(self, wallet, *, address=None): pass
        def register(self, *a, **k): raise RuntimeError("402 insufficient_funds")
        def deposit_address(self, purpose="names"): return "anim1deposit"

    with pytest.raises(names.InsufficientBalance) as ei:
        names.reserve(_Wallet(), _Reg(), "mysite", years=1)
    assert ei.value.deposit_address == "anim1deposit"
    assert ei.value.fee_anm == 25          # mysite = 6 chars, 1yr


def test_fund_balance_sends_to_deposit_address():
    calls = {}

    class _Wallet:
        def primary_address(self): return "anim1payer"
        def send(self, to, amount, *, from_address=None, data_hex=None):
            calls["to"] = to; calls["amount"] = amount
            return {"tx_hash": "0xfeed"}

    class _Reg:
        def deposit_address(self, purpose="names"): return "anim1deposit"

    out = names.fund_balance(_Wallet(), _Reg(), 50)
    assert calls["to"] == "anim1deposit" and calls["amount"] == 50 * config.NANM_PER_ANM
    assert out["txid"] == "0xfeed" and out["depositAddress"] == "anim1deposit"


# ------------------------------------------------------------------ serve/publish limits
def test_publish_rejects_oversize(tmp_path):
    big = tmp_path / "index.html"
    big.write_bytes(b"x" * (config.MAX_CONTENT_BYTES + 1))
    with pytest.raises(serve.PublishError):
        serve.load_site_html(str(big))


def test_load_site_html_from_folder(tmp_path):
    (tmp_path / "index.html").write_text("<h1>ok</h1>")
    assert "ok" in serve.load_site_html(str(tmp_path))
    assert serve.local_cid("<h1>ok</h1>") == resolver.compute_cid(b"<h1>ok</h1>")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
