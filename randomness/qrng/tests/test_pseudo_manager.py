"""Pseudo-quantum source + auto-flipping source manager."""

from __future__ import annotations

import os

from randomness.qrng import health
from randomness.qrng.pseudo import PseudoQuantumSource
from randomness.qrng.providers import QuantumEntropySource, SourceInfo
from randomness.qrng.manager import EntropySourceManager


class _FakeQuantis(QuantumEntropySource):
    """Stand-in for a real attested quantum device connecting at runtime."""
    def info(self) -> SourceInfo:
        return SourceInfo(name="quantis", vendor="ID Quantique", model="Quantis (test)",
                          is_hardware=True, is_quantum=True, attested=True, notes="test hw")
    def random_bytes(self, n: int) -> bytes:
        return os.urandom(n)


def test_pseudo_ideal_passes_health():
    src = PseudoQuantumSource()  # ideal theta=0 -> fair coin
    info = src.info()
    assert info.name == "pseudo-quantum" and not info.is_quantum and not info.attested
    rep = health.evaluate(src.random_bytes(8192))
    assert rep.passed


def test_pseudo_biased_is_detected():
    src = PseudoQuantumSource(bias_theta=1.2)  # decohered/biased
    data = src.random_bytes(8192)
    # biased measurement model -> min-entropy below the full-entropy line
    rep = health.evaluate(data, min_entropy_per_byte=7.9)
    assert not rep.passed


def test_manager_starts_pseudo():
    m = EntropySourceManager()
    st = m.status()
    assert st["mode"] == "pseudo" and st["pseudo"] is True
    assert st["real_available"] is False and st["attested"] is False
    assert st["active_source"]["name"] == "pseudo-quantum"
    # current source still yields healthy bytes
    assert len(m.current().random_bytes(64)) == 64


def test_manager_flips_to_real_on_connect_and_back():
    flips_seen = []
    m = EntropySourceManager()
    m.on_flip(lambda f: flips_seen.append(f))
    assert m.mode == "pseudo"
    # a real attested provider connects -> auto flip
    st = m.connect_provider(_FakeQuantis(), name="quantis")
    assert st["mode"] == "hardware" and st["real_available"] and st["attested"]
    assert st["active_source"]["name"] == "quantis"
    assert any(f["to_mode"] == "hardware" for f in m.status()["flips"])
    assert flips_seen and flips_seen[-1]["to_mode"] == "hardware"
    # disconnect -> flips back to pseudo (lane still works)
    st2 = m.disconnect_all()
    assert st2["mode"] == "pseudo" and not st2["real_available"]
    assert m.status()["flips"][-1]["to_mode"] == "pseudo"
