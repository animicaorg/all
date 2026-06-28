"""
randomness.beacon_api
=====================

Public Verifiable Quantum Randomness Beacon — an HTTP service + page that turns
the in-chain quantum randomness lane (``randomness.qrng``) into a drand-style
public good: a continuous, hash-chained stream of beacon rounds that anyone can
consume and **verify in their own browser** (no trust in the server).

- ``driver.BeaconDriver`` produces one round every ``interval`` seconds from the
  best available entropy source (pseudo-quantum now; auto-flips to a real
  attested QRNG the moment one connects), chaining each round to the previous.
- ``server.create_app`` exposes a small REST API and serves the ``/beacon`` page
  + an in-browser verifier (``static/verify.js``) that recomputes everything.
"""

from .driver import BeaconDriver

__all__ = ["BeaconDriver"]
