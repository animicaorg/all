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


def test_reservation_quote_and_memo():
    q = names.reservation_quote("mysite", 2)   # 6 chars -> 25 ANM/yr
    assert q["name"] == "mysite" and q["years"] == 2
    assert q["feeAnm"] == 25 * 2 and q["feeNanm"] == 25 * 2 * config.NANM_PER_ANM
    assert q["foundation"] == config.FOUNDATION_ADDRESS
    assert names.reserve_memo("mysite", 2) == "anmreserve:mysite:2"
    assert names.memo_to_data_hex("anmreserve:mysite:2") == "0x" + b"anmreserve:mysite:2".hex()


def test_reserve_pays_foundation_with_bound_memo():
    calls = {}

    class _Wallet:
        def primary_address(self): return "anim1payer"
        def send(self, to, amount, *, from_address=None, data_hex=None):
            calls["to"] = to; calls["amount"] = amount
            calls["from"] = from_address; calls["data"] = data_hex
            return {"tx_hash": "0xdeadbeef"}

    class _Reg:
        def reserve(self, name, *, years, address, payment_txid, kind="app"):
            calls["reserve"] = (name, years, address, payment_txid, kind)
            return {"registered": True, "name": name}

    out = names.reserve(_Wallet(), _Reg(), "mysite", years=2)
    # paid the Foundation the exact fee, with the name-bound memo
    assert calls["to"] == config.FOUNDATION_ADDRESS
    assert calls["amount"] == 50 * config.NANM_PER_ANM
    assert calls["data"] == "0x" + b"anmreserve:mysite:2".hex()
    assert calls["reserve"] == ("mysite", 2, "anim1payer", "0xdeadbeef", "app")
    assert out["txid"] == "0xdeadbeef" and out["feeAnm"] == 50


def test_reserve_surfaces_payment_failure_clearly():
    class _Wallet:
        def primary_address(self): return "anim1payer"
        def send(self, *a, **k): raise RuntimeError("insufficient funds")

    with pytest.raises(names.ReserveError) as ei:
        names.reserve(_Wallet(), object(), "mysite", years=1)
    assert "Foundation" in str(ei.value)


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
