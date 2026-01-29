"""
p2p.adapters
=============

Thin integration layer that lets the P2P node validate and interpret
objects (headers/blocks/txs/shares) using the canonical code from:

- :mod:`core`       — decoding, basic integrity checks, DB access
- :mod:`consensus`  — cheap header validation & Θ/policy views for sync
- :mod:`proofs`     — fast pre-parse of proof envelopes for share gossip

These modules are intentionally light so the P2P stack can import them
without creating heavy dependency cycles. Each adapter exposes small,
pure functions and (optionally) stateless helpers.

Public surface
--------------

- :mod:`p2p.adapters.core_chain`     — decode/validate headers/blocks/txs
- :mod:`p2p.adapters.consensus_view` — schedule/Θ/policy roots for sync
- :mod:`p2p.adapters.proofs_view`    — pre-parse proof envelopes

Usage
-----

    from p2p.adapters import core_chain, consensus_view, proofs_view

    hdr = core_chain.decode_header(raw_bytes)
    consensus_view.quick_header_sanity(hdr, params=current_params)
    kind, view = proofs_view.peek_envelope(envelope_bytes)

All adapters are import-time cheap and safe to use in hot paths.
"""

from . import consensus_view, core_chain, proofs_view

__all__ = ["core_chain", "consensus_view", "proofs_view"]


def available_adapters() -> dict[str, object]:
    """
    Introspection helper used by diagnostics/tests.

    Returns
    -------
    dict[str, object]
        Mapping of adapter name → imported module.
    """
    return {
        "core_chain": core_chain,
        "consensus_view": consensus_view,
        "proofs_view": proofs_view,
    }
