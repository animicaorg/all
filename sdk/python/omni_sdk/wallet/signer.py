"""
omni_sdk.wallet.signer
======================

Post-quantum (PQ) signers for the Animica SDK.

This module provides a thin, well-typed facade over the `pq` package's
uniform signing APIs for Dilithium3 and SPHINCS+ (SHAKE-128s). It is designed
to be stable and ergonomic for the SDK, while deferring cryptographic details
to the `pq` library.

Key features
------------
- Deterministic, domain-separated signatures via `pq.py.sign` / `pq.py.verify`
- Support for Dilithium3 and SPHINCS+ (SHAKE-128s)
- Seeded key generation and explicit keypair import
- Address derivation helper (via `omni_sdk.address` or `pq.py.address`)
- Careful import strategy with helpful errors if `pq` is unavailable

Notes
-----
- Domain separation is optional at this layer to avoid double-domaining if
  upstream callers already include domain tags in their sign-bytes. Pass a
  `domain` value only if you know the target verification path expects it.
- This module is pure-Python and only depends on `pq` for crypto. If the
  optional `liboqs` backend is available, `pq` may use it internally.

"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

# Public algorithm names we support here (match pq registry naming)
AlgName = Literal["dilithium3", "sphincs_shake_128s"]

__all__ = [
    "AlgName",
    "SignerInfo",
    "PQSigner",
    "create_signer_from_seed",
    "create_signer_from_keypair",
]


# --- Lazy imports of pq components with clear error messages -----------------


def _import_pq() -> Tuple[Any, Any, Any, Any]:
    """
    Import the pq submodules we rely on and return them.

    Returns
    -------
    (registry, keygen, sign, verify)
    """
    try:
        # Package layout: pq/py/...
        from pq.py import keygen as pq_keygen
        from pq.py import registry as pq_registry
        from pq.py import sign as pq_sign
        from pq.py import verify as pq_verify
    except Exception as e:  # pragma: no cover - import-time environment specific
        raise RuntimeError(
            "The 'pq' package is required for PQ signing. "
            "Ensure the Animica 'pq' module is installed and importable."
        ) from e
    return pq_registry, pq_keygen, pq_sign, pq_verify


# --- Data structures ---------------------------------------------------------


@dataclass(frozen=True)
class SignerInfo:
    """
    Lightweight description of a signer.

    Attributes
    ----------
    alg_name : AlgName
        Canonical algorithm name (e.g., 'dilithium3', 'sphincs_shake_128s').
    alg_id : int
        Canonical numeric algorithm id from the pq registry.
    public_key : bytes
        Raw public key bytes for the algorithm.
    address : Optional[str]
        Bech32m address string (hrp 'anim' by default) if derivation succeeds.
    """

    alg_name: AlgName
    alg_id: int
    public_key: bytes
    address: Optional[str]


# --- Helpers -----------------------------------------------------------------


def _normalize_alg_name(name: str) -> AlgName:
    n = name.strip().lower()
    aliases = {
        "dilithium": "dilithium3",
        "dilithium-3": "dilithium3",
        "sphincs": "sphincs_shake_128s",
        "sphincs+": "sphincs_shake_128s",
        "sphincs_shake_128s": "sphincs_shake_128s",
        "sphincs+-shake-128s": "sphincs_shake_128s",
    }
    n = aliases.get(n, n)
    if n not in ("dilithium3", "sphincs_shake_128s"):
        raise ValueError(f"Unsupported algorithm name: {name!r}")
    return n  # type: ignore[return-value]


def _lookup_alg_id(alg_name: AlgName) -> int:
    pq_registry, _, _, _ = _import_pq()
    # Prefer a stable registry interface; try common shapes with graceful fallback.
    # 1) registry.ALG_ID: Dict[str, int]
    alg_id_map = getattr(pq_registry, "ALG_ID", None)
    if isinstance(alg_id_map, dict) and alg_name in alg_id_map:
        return int(alg_id_map[alg_name])
    # 2) registry.id_of(name: str) -> int
    if hasattr(pq_registry, "id_of"):
        return int(pq_registry.id_of(alg_name))  # type: ignore[attr-defined]
    # 3) registry.id_for_name(name: str) -> int
    if hasattr(pq_registry, "id_for_name"):
        return int(pq_registry.id_for_name(alg_name))  # type: ignore[attr-defined]
    # 4) registry.name_to_id: Dict[str, int]
    mapping = getattr(pq_registry, "name_to_id", None)
    if isinstance(mapping, dict) and alg_name in mapping:
        return int(mapping[alg_name])
    raise RuntimeError(f"Unable to resolve algorithm id for '{alg_name}' from pq registry.")


def _fork_id_from_env() -> Optional[int]:
    for key in ("ANIMICA_FORK_ID", "ANIMICA_CHAIN_FORK_ID"):
        val = os.getenv(key)
        if not val:
            continue
        try:
            v = val.strip().lower()
            if v.startswith("0x"):
                return int(v, 16)
            return int(v)
        except Exception:
            logging.warning("Invalid fork_id in %s=%s", key, val)
            return None
    return None


def _derive_address(alg_id: int, public_key: bytes, hrp: str = "anim") -> Optional[str]:
    """
    Try to derive a bech32m address from alg_id and public key.

    Prefer omni_sdk.address if available; fall back to pq.py.address.
    """
    # Path A: omni_sdk.address (preferred)
    try:
        from omni_sdk import address as sdk_address  # type: ignore

        # Try common helper shapes
        if hasattr(sdk_address, "from_pubkey"):
            return sdk_address.from_pubkey(public_key, alg_id=alg_id, hrp=hrp)  # type: ignore[attr-defined]
        if hasattr(sdk_address, "derive"):
            return sdk_address.derive(public_key, alg_id=alg_id, hrp=hrp)  # type: ignore[attr-defined]
        if hasattr(sdk_address, "encode"):
            return sdk_address.encode(public_key, alg_id, hrp)  # type: ignore[attr-defined]
    except Exception:
        pass  # fall through

    # Path B: pq.py.address
    try:
        from pq.py import address as pq_address  # type: ignore

        # Common helpers in crypto libs:
        for fn_name in ("from_pubkey", "encode", "pubkey_to_address"):
            fn = getattr(pq_address, fn_name, None)
            if callable(fn):
                try:
                    return fn(public_key, alg_id=alg_id, hrp=hrp)  # type: ignore[misc]
                except TypeError:
                    # Try positional (pk, alg_id, hrp)
                    return fn(public_key, alg_id, hrp)  # type: ignore[misc]
    except Exception:
        pass

    return None


def _call_uniform_sign(
    pq_sign: Any, *, alg_name: str, sk: bytes, msg: bytes, domain: Optional[bytes]
) -> bytes:
    """
    Call pq.py.sign signing with a variety of tolerated signatures for resilience across
    minor library changes.
    """
    # Try sign_detached first (preferred)
    if hasattr(pq_sign, "sign_detached"):
        try:
            # sign_detached(msg, alg, sk, domain=..., ...)
            result = pq_sign.sign_detached(
                msg,
                alg_name,
                sk,
                domain=domain or b"generic"
            )
            # Result is a Signature object or just bytes
            if isinstance(result, bytes):
                return result
            if hasattr(result, "sig"):
                return bytes(result.sig)
            if hasattr(result, "signature"):
                return bytes(result.signature)
            return bytes(result)
        except TypeError as e:
            # Try with positional domain
            try:
                result = pq_sign.sign_detached(msg, alg_name, sk)
                if isinstance(result, bytes):
                    return result
                if hasattr(result, "sig"):
                    return bytes(result.sig)
                return bytes(result)
            except TypeError:
                pass
    
    # Fallback to sign if available
    if hasattr(pq_sign, "sign"):
        try:
            return pq_sign.sign(alg=alg_name, sk=sk, msg=msg, domain=domain)  # type: ignore[attr-defined]
        except (TypeError, AttributeError):
            pass
    
    raise RuntimeError(
        "pq.sign API not recognized; please update the SDK or pq module."
    )


def _call_uniform_verify(
    pq_verify: Any,
    *,
    alg_name: str,
    pk: bytes,
    msg: bytes,
    sig: bytes,
    domain: Optional[bytes],
) -> bool:
    # Preferred keyword form
    try:
        ok = pq_verify.verify(alg=alg_name, pk=pk, msg=msg, sig=sig, domain=domain)  # type: ignore[attr-defined]
        return bool(ok)
    except TypeError:
        pass
    # Alternate keywords
    for kwargs in (
        dict(algorithm=alg_name, pk=pk, msg=msg, sig=sig, domain=domain),
        dict(alg_name=alg_name, pk=pk, msg=msg, sig=sig, domain=domain),
        dict(alg=alg_name, pk=pk, message=msg, signature=sig, domain=domain),
    ):
        try:
            return bool(pq_verify.verify(**kwargs))  # type: ignore[misc]
        except TypeError:
            continue
    # Positional fallbacks
    for args in (
        (pk, msg, sig, alg_name, domain),
        (alg_name, pk, msg, sig, domain),
        (pk, msg, sig, alg_name),
        (alg_name, pk, msg, sig),
    ):
        try:
            return bool(pq_verify.verify(*args))  # type: ignore[misc]
        except TypeError:
            continue
    raise RuntimeError(
        "pq.verify.verify API not recognized; please update the SDK or pq module."
    )


def _uniform_keygen(alg_name: AlgName, seed: Optional[bytes]) -> Tuple[bytes, bytes]:
    """
    Use pq.py.keygen to derive a keypair for a signature algorithm, optionally
    seeded (deterministic).
    """
    _, pq_keygen, _, _ = _import_pq()

    # Preferred forms to try:
    candidates = [
        # keypair_sig(alg=..., seed=...) -> (sk, pk)
        ("keypair_sig", dict(alg=alg_name, seed=seed)),
        # keypair(kind='sig', alg=..., seed=...)
        ("keypair", dict(kind="sig", alg=alg_name, seed=seed)),
        # keypair(alg=..., seed=...)
        ("keypair", dict(alg=alg_name, seed=seed)),
        # keypair_sig(name=..., seed=...)
        ("keypair_sig", dict(name=alg_name, seed=seed)),
    ]
    for func_name, kwargs in candidates:
        fn = getattr(pq_keygen, func_name, None)
        if callable(fn):
            try:
                sk, pk = fn(**kwargs)  # type: ignore[misc]
                if isinstance(sk, (bytes, bytearray)) and isinstance(
                    pk, (bytes, bytearray)
                ):
                    return bytes(sk), bytes(pk)
            except TypeError:
                continue

    # Positional fallbacks
    for func_name in ("keypair_sig", "keypair"):
        fn = getattr(pq_keygen, func_name, None)
        if callable(fn):
            for args in ((alg_name, seed), (alg_name,), (None, alg_name)):
                try:
                    sk, pk = fn(*[a for a in args if a is not None])  # type: ignore[misc]
                    if isinstance(sk, (bytes, bytearray)) and isinstance(
                        pk, (bytes, bytearray)
                    ):
                        return bytes(sk), bytes(pk)
                except TypeError:
                    continue

    raise RuntimeError(
        "pq.keygen API not recognized; please update the SDK or pq module."
    )


# --- Main signer -------------------------------------------------------------


class PQSigner:
    """
    A post-quantum signer for Dilithium3 or SPHINCS+ (SHAKE-128s).

    Create instances via:
        - PQSigner.from_seed(...)
        - PQSigner.from_keypair(...)
        - create_signer_from_seed(...) convenience
        - create_signer_from_keypair(...) convenience
    """

    def __init__(
        self, *, alg_name: AlgName, secret_key: bytes, public_key: bytes
    ) -> None:
        self._alg_name: AlgName = _normalize_alg_name(alg_name)
        self._sk: bytes = bytes(secret_key)
        self._pk: bytes = bytes(public_key)
        self._alg_id: int = _lookup_alg_id(self._alg_name)
        self._address: Optional[str] = _derive_address(self._alg_id, self._pk)

    # ---- Constructors ----

    @classmethod
    def from_seed(cls, alg_name: str, seed: Optional[bytes] = None) -> "PQSigner":
        """
        Derive a deterministic keypair for the given algorithm using an optional seed.

        Parameters
        ----------
        alg_name : str
            'dilithium3' or 'sphincs_shake_128s' (case/alias-insensitive).
        seed : Optional[bytes]
            If provided, used as a deterministic RNG seed; otherwise, pq will use OS RNG.

        Returns
        -------
        PQSigner
        """
        name = _normalize_alg_name(alg_name)
        sk, pk = _uniform_keygen(name, seed)
        return cls(alg_name=name, secret_key=sk, public_key=pk)

    @classmethod
    def from_keypair(
        cls, alg_name: str, secret_key: bytes, public_key: bytes
    ) -> "PQSigner":
        """
        Construct a signer from an existing keypair.
        """
        name = _normalize_alg_name(alg_name)
        return cls(alg_name=name, secret_key=secret_key, public_key=public_key)

    # ---- Properties ----

    @property
    def alg_name(self) -> AlgName:
        return self._alg_name

    @property
    def alg_id(self) -> int:
        return self._alg_id

    @property
    def public_key(self) -> bytes:
        return self._pk

    @property
    def secret_key(self) -> bytes:
        # Expose read-only bytes; callers are responsible for secure storage.
        return self._sk

    @property
    def address(self) -> Optional[str]:
        return self._address

    def info(self) -> SignerInfo:
        return SignerInfo(
            alg_name=self._alg_name,
            alg_id=self._alg_id,
            public_key=self._pk,
            address=self._address,
        )

    # ---- Operations ----

    def sign(self, message: bytes, *, domain: Optional[bytes] = None) -> bytes:
        """
        Sign a message with optional domain separation.

        Parameters
        ----------
        message : bytes
            The exact byte string to sign (e.g., canonical SignBytes for a tx).
        domain : Optional[bytes]
            Domain tag to prevent cross-protocol signature reuse. Leave as None
            if upstream callers already include domain separation in the message.

        Returns
        -------
        bytes
            Raw signature bytes for the algorithm.
        """
        _, _, pq_sign, _ = _import_pq()
        return _call_uniform_sign(
            pq_sign, alg_name=self._alg_name, sk=self._sk, msg=message, domain=domain
        )

    def sign_tx(self, message: bytes, chain_id: int, fork_id: int | None = None) -> bytes:
        """
        Sign a transaction message with proper domain separation for Animica transactions.
        
        This method uses the standard "tx" domain and includes chain_id + fork_id
        in the signature construction, matching the node's verification expectations.
        
        Parameters
        ----------
        message : bytes
            The transaction body (CBOR-encoded canonical body dict from omni_sdk.tx.encode.sign_bytes)
        chain_id : int
            Chain ID for domain separation
        fork_id : Optional[int]
            Fork identifier (domain separation between genesis resets). If None,
            attempt to read ANIMICA_FORK_ID from the environment.
        
        Returns
        -------
        bytes
            Raw signature bytes
        """
        _, _, pq_sign, _ = _import_pq()
        resolved_fork_id = fork_id if fork_id is not None else _fork_id_from_env()
        # Call sign_detached with domain="tx" and chain_id/fork_id to match node verification
        try:
            result = pq_sign.sign_detached(
                message,
                self._alg_name,
                self._sk,
                domain="tx",
                chain_id=chain_id,
                fork_id=resolved_fork_id,
            )
            # Extract raw signature bytes from result
            if isinstance(result, bytes):
                return result
            if hasattr(result, "sig"):
                return bytes(result.sig)
            if hasattr(result, "signature"):
                return bytes(result.signature)
            return bytes(result)
        except TypeError as e:
            logging.warning(
                "PQ backend does not support domain/chain_id parameters; "
                "using manual SignBytes construction with chain_id=%s fork_id=%s (%s)",
                chain_id,
                resolved_fork_id,
                e,
            )

            # Build canonical SignBytes ourselves to preserve domain separation
            try:
                prehash = pq_sign.build_sign_bytes(
                    message,
                    domain="tx",
                    chain_id=chain_id,
                    fork_id=resolved_fork_id,
                    alg_id=self._alg_id,
                )
            except Exception as build_err:
                raise RuntimeError(
                    "PQ signing failed: could not build SignBytes for legacy backend"
                ) from build_err

            backend_sign = getattr(pq_sign, "_backend_sign", None)
            if backend_sign is None:
                raise RuntimeError(
                    "PQ signing failed: legacy backend missing _backend_sign helper"
                )

            try:
                sig_bytes = backend_sign(self._alg_name, self._sk, prehash)
                return bytes(sig_bytes)
            except Exception as backend_err:
                raise RuntimeError(
                    "PQ signing failed using legacy backend signer"
                ) from backend_err

    def verify(
        self, message: bytes, signature: bytes, *, domain: Optional[bytes] = None
    ) -> bool:
        """
        Verify a signature over a message with optional domain separation.
        """
        _, _, _, pq_verify = _import_pq()
        try:
            ok = _call_uniform_verify(
                pq_verify,
                alg_name=self._alg_name,
                pk=self._pk,
                msg=message,
                sig=signature,
                domain=domain,
            )
            # Use constant-time compare if verify returns a recomputed sig (some libs do),
            # otherwise accept boolean True from the verifier.
            if isinstance(ok, (bytes, bytearray)):
                return hmac.compare_digest(bytes(ok), signature)
            return bool(ok)
        except Exception:
            return False


# --- Convenience creators ----------------------------------------------------


def create_signer_from_seed(alg_name: str, seed: Optional[bytes] = None) -> PQSigner:
    """
    Convenience factory that forwards to PQSigner.from_seed with name normalization.
    """
    return PQSigner.from_seed(alg_name, seed)


def create_signer_from_keypair(
    alg_name: str, secret_key: bytes, public_key: bytes
) -> PQSigner:
    """
    Convenience factory that forwards to PQSigner.from_keypair with name normalization.
    """
    return PQSigner.from_keypair(alg_name, secret_key, public_key)


# --- Minimal self-test (importable) ------------------------------------------

if __name__ == "__main__":  # pragma: no cover - lightweight sanity check
    # Try both algs if pq is present. This does not assert, just prints a brief status.
    for name in ("dilithium3", "sphincs_shake_128s"):
        try:
            s = PQSigner.from_seed(name, seed=b"\x01" * 32)
            msg = b"hello animica"
            sig = s.sign(msg, domain=None)
            ok = s.verify(msg, sig, domain=None)
            print(
                f"[{name}] addr={s.address!r} verify={ok} pk_len={len(s.public_key)} sig_len={len(sig)}"
            )
        except Exception as e:
            print(f"[{name}] FAILED: {e}")
