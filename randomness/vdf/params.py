"""
randomness.vdf.params
=====================

Profiles and helpers for configuring the beacon's VDF (time-delay) parameters.

What lives here
---------------
- A lightweight :class:`VDFParams` dataclass capturing the modulus (if using an
  RSA group for Wesolowski), iteration count ``t``, and an indicative security
  level in bits.
- A few built-in profiles (``devnet``, ``testnet``) with **placeholder**
  moduli that are suitable for local/testing only. Production networks MUST
  supply a trapdoorless modulus generated via MPC (or switch to a class-group
  backend that doesn't require an RSA modulus).
- Environment-variable overrides for CI/dev convenience:
    * ``VDF_PROFILE``          → profile name (e.g., "devnet", "testnet")
    * ``VDF_ITERATIONS``       → integer iteration override
    * ``VDF_MODULUS_HEX``      → hex string (no "0x") to override modulus
    * ``VDF_SECURITY_BITS``    → integer security-bits override
    * ``VDF_BACKEND``          → "rsa" (default) or "classgroup"

Notes
-----
- ``security_bits`` is advisory metadata for operators and tests; verification
  correctness does not directly depend on this field.
- If ``backend == "classgroup"``, ``modulus_hex`` may be left empty. Backends
  should document their own parameterization.

WARNING (production)
--------------------
The default moduli below are **NOT** MPC-generated and should never be used on
a live network. They exist only so tests/benches can run out-of-the-box.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Literal, Optional


BackendKind = Literal["rsa", "classgroup"]


@dataclass(frozen=True)
class VDFParams:
    """Container for VDF configuration."""
    name: str
    iterations: int
    security_bits: int
    backend: BackendKind = "rsa"
    # Hex string (no "0x") for RSA groups. Optional for classgroup backends.
    modulus_hex: str = ""
    description: str = ""

    @property
    def modulus_n(self) -> int:
        """Return modulus as int (0 if unset)."""
        return int(self.modulus_hex, 16) if self.modulus_hex else 0

    @property
    def modulus_bitlen(self) -> int:
        n = self.modulus_n
        return n.bit_length() if n else 0

    def validate(self) -> None:
        """Basic sanity checks to catch obvious misconfigurations early."""
        if self.iterations <= 0:
            raise ValueError("VDFParams.iterations must be > 0")
        if self.security_bits < 64:
            raise ValueError("VDFParams.security_bits looks too low (<64)")
        if self.backend == "rsa":
            if not self.modulus_hex:
                raise ValueError("RSA backend requires modulus_hex")
            # Coarse checks; we cannot test trapdoorlessness here.
            n = self.modulus_n
            if n % 2 == 0:
                raise ValueError("RSA modulus must be odd")
            if self.modulus_bitlen < 1024:
                raise ValueError("RSA modulus must be at least 1024 bits for testing")
        # classgroup backend has no modulus checks here.


# ---------------------------------------------------------------------------
# Built-in placeholder moduli (TEST-ONLY!)
# ---------------------------------------------------------------------------

# 1024-bit odd composite-looking placeholder (hex length = 256 chars).
# DO NOT USE IN PRODUCTION.
_DEVNET_RSA_MODULUS_HEX = (
    "F7F1D3C5B7A98B7D6F5F4F3F2F1F0FEEEDCBA9876543210F0E1D2C3B4A59687"
    "CAFEBABEDEADBEAF112233445566778899AABBCCDDEEFF0011223344556677"
    "9B3D5F7A9CBEADF1023456789ABCDEF00123456789ABCDEFFEDCBA987654321"
    "B1C3D5E7F9ABCD01EFCDAB8967452301C0FFEEFACED00DDEAD0BEEF1234567F"
)

# 2048-bit odd composite-looking placeholder (hex length = 512 chars).
# DO NOT USE IN PRODUCTION.
_TESTNET_RSA_MODULUS_HEX = (
    "F3D5C7B9AB9D8F7E6D5C4B3A291817060504030201FFEEDDCCBBAA99887766"
    "112233445566778899AABBCCDDEEFF00CAFEBABEDEADBEAF0123456789ABCDE"
    "FFEDCBA9876543210F1E2D3C4B5A69788796A5B4C3D2E1F0011223344556677"
    "8899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF1122334455667799"
    "ABCDEF0123456789ABCDEF00112233445566778899AABBCCDDEEFF0A0B0C0D0E"
    "0F1F2E3D4C5B6A79888796A5B4C3D2E1F0123456789ABCDEFFEDCBA987654321"
    "13579BDF02468ACEFDB97531ECA86420DEADBEEFCAFEBABE0022446688AACCEE"
    "1122446688AACCEE99BBAADDCCEEFF1133557799BBDDFF001133557799BBDDFF"
)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

DEVNET = VDFParams(
    name="devnet",
    backend="rsa",
    iterations=2**16,           # keep snappy for local runs/CI
    security_bits=96,           # indicative only
    modulus_hex=_DEVNET_RSA_MODULUS_HEX,
    description="Fast local profile with a 1024-bit placeholder RSA modulus.",
)

TESTNET = VDFParams(
    name="testnet",
    backend="rsa",
    iterations=2**20,           # slower, but still testable
    security_bits=110,
    modulus_hex=_TESTNET_RSA_MODULUS_HEX,
    description="Testnet profile with a 2048-bit placeholder RSA modulus.",
)

DEFAULT_PROFILES: Dict[str, VDFParams] = {
    DEVNET.name: DEVNET,
    TESTNET.name: TESTNET,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def profile_names() -> list[str]:
    """Return available profile names."""
    return sorted(DEFAULT_PROFILES.keys())


def from_dict(d: dict) -> VDFParams:
    """
    Construct :class:`VDFParams` from a plain dict (e.g., parsed YAML/JSON).
    Unknown keys are ignored.
    """
    base = DEFAULT_PROFILES.get(d.get("name", ""), DEVNET)
    return replace(
        base,
        name=d.get("name", base.name),
        iterations=int(d.get("iterations", base.iterations)),
        security_bits=int(d.get("security_bits", base.security_bits)),
        backend=d.get("backend", base.backend),  # type: ignore[arg-type]
        modulus_hex=d.get("modulus_hex", base.modulus_hex),
        description=d.get("description", base.description),
    )


def _apply_env_overrides(p: VDFParams) -> VDFParams:
    import os

    name = os.getenv("VDF_PROFILE", p.name)
    it = int(os.getenv("VDF_ITERATIONS", p.iterations))
    sec = int(os.getenv("VDF_SECURITY_BITS", p.security_bits))
    be = os.getenv("VDF_BACKEND", p.backend)
    mod = os.getenv("VDF_MODULUS_HEX", p.modulus_hex)

    return VDFParams(
        name=name,
        iterations=it,
        security_bits=sec,
        backend=be if be in ("rsa", "classgroup") else p.backend,  # type: ignore[arg-type]
        modulus_hex=mod,
        description=p.description,
    )


def get_params(profile: Optional[str] = None) -> VDFParams:
    """
    Load parameters by profile name, then apply environment overrides.

    If ``profile`` is None, uses ``VDF_PROFILE`` if set, otherwise ``devnet``.
    """
    import os

    chosen = profile or os.getenv("VDF_PROFILE") or DEVNET.name
    base = DEFAULT_PROFILES.get(chosen, DEVNET)
    params = _apply_env_overrides(base)
    params.validate()
    return params


__all__ = [
    "BackendKind",
    "VDFParams",
    "DEVNET",
    "TESTNET",
    "DEFAULT_PROFILES",
    "profile_names",
    "from_dict",
    "get_params",
]
