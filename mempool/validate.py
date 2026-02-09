"""
mempool.validate
================

Stateless admission checks for incoming transactions:

  • Size limits (raw-bytes upper bound)
  • Chain ID equality
  • Gas limits sanity (non-zero, <= block cap)
  • Optional payload constraints (basic kind-aware checks)
  • Post-quantum signature *precheck* (fast path)

This module is intentionally *stateless*: it does not look at account
balances, nonces, or mempool state. It is safe to run before any heavier
processing or pool insertion.

Callers:
  - rpc.pending_pool (pre-admission)
  - mempool ingress paths (p2p relay)

Design notes
------------
• We verify PQ signatures against the canonical "sign-bytes" for the Tx.
  The Tx type in core/types/tx.py exposes either `sign_bytes()` or a
  `signing_message()` method; we gracefully try both.

• We avoid re-encoding CBOR unless needed; the `raw` parameter is the
  original canonical CBOR from the wire and is used for size checks.

• We keep imports light and guarded to avoid import cycles in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

# -----------------------------
# Optional/guarded imports
# -----------------------------

try:
    from core.types.tx import Tx  # type: ignore
except Exception:  # pragma: no cover

    class Tx:  # type: ignore
        """Minimal stub for isolated tests."""

        kind: str
        chain_id: int
        gas_limit: int
        to: Optional[bytes]
        data: bytes
        # Signature tuple; names vary across drafts, so we keep both:
        alg_id: int
        pubkey: bytes
        signature: bytes

        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

        def sign_bytes(self) -> bytes:
            return b"TEST_SIGN_BYTES"


try:
    from core.types.params import ChainParams  # type: ignore
except Exception:  # pragma: no cover

    @dataclass
    class ChainParams:  # type: ignore
        chain_id: int = 1
        block_gas_limit: int = 30_000_000
        tx_max_bytes: int = 1_048_576  # 1 MiB default


# PQ verify (uniform API)
try:
    from pq.py.verify import verify as pq_verify  # type: ignore
except Exception:  # pragma: no cover

    def pq_verify(message: bytes, signature: bytes, public_key: bytes, alg_id: int, domain: bytes = b"") -> bool:  # type: ignore
        # Educational fallback: *never* use in production.
        return False  # cause tests to explicitly inject a real verifier


# -----------------------------
# Errors / results
# -----------------------------


class StatelessValidationError(Exception):
    """Raised when a stateless validation check fails."""

    code: str

    def __init__(self, code: str, msg: str) -> None:
        super().__init__(msg)
        self.code = code


@dataclass(frozen=True)
class StatelessConfig:
    """
    Parameters required for stateless validation.

    If ChainParams is provided, missing fields fall back to defaults or
    are derived from it.
    """

    chain_id: int
    max_tx_bytes: int = 1_048_576  # 1 MiB hard cap unless overridden
    max_gas_limit: int = 30_000_000  # sane default matching many L1s
    enforce_sig_precheck: bool = True  # toggle fast PQ verify


# -----------------------------
# Public API
# -----------------------------


def _is_coinbase_tx(tx: "Tx") -> bool:
    """
    Check if a transaction is a coinbase transaction (protocol-generated mining reward).
    
    Coinbase transactions have:
    - kind = COINBASE (TxKind.COINBASE = 3)
    - sender = ZERO_ADDRESS (32 zero bytes)
    - No signature (empty sigs tuple)
    
    Returns:
        bool: True if this is a coinbase transaction
    """
    try:
        # Check kind (COINBASE = 3)
        kind = None
        if hasattr(tx, "unsigned"):
            kind = getattr(tx.unsigned, "kind", None)
        elif hasattr(tx, "kind"):
            kind = getattr(tx, "kind", None)
        
        # TxKind.COINBASE = 3
        if kind is not None:
            kind_int = int(kind) if hasattr(kind, "__int__") else kind
            if kind_int == 3:  # COINBASE
                return True
        
        return False
    except Exception:
        return False


def validate_stateless(
    tx: "Tx",
    raw: bytes,
    *,
    params: Optional[ChainParams] = None,
    cfg: Optional[StatelessConfig] = None,
) -> None:
    """
    Run stateless checks. Raises StatelessValidationError on failure.

    Args:
        tx:   Decoded Tx object (core.types.tx.Tx)
        raw:  Canonical CBOR bytes as received (for exact size checks)
        params: Optional ChainParams (derives chainId, gas limits)
        cfg:     Optional override for limits/toggles

    Precedence: explicit cfg > params-derived > built-ins.
    """
    effective_cfg = _derive_cfg(params, cfg)

    # Check if this is a coinbase transaction (protocol-generated, no signature needed)
    is_coinbase = _is_coinbase_tx(tx)

    _check_size(raw, effective_cfg)
    _check_chain_id(tx, effective_cfg)
    
    # Coinbase transactions can have zero gas_limit
    if not is_coinbase:
        _check_gas_limits(tx, effective_cfg)
    
    _check_payload_shape(tx)

    # Skip signature verification for coinbase transactions (they have no signature)
    if effective_cfg.enforce_sig_precheck and not is_coinbase:
        _precheck_pq_signature(tx)


# -----------------------------
# Individual checks
# -----------------------------


def _check_size(raw: bytes, cfg: StatelessConfig) -> None:
    n = len(raw)
    if n == 0:
        raise StatelessValidationError("EmptyBytes", "Transaction payload is empty.")
    if n > cfg.max_tx_bytes:
        raise StatelessValidationError(
            "Oversize",
            f"Transaction size {n} exceeds limit {cfg.max_tx_bytes} bytes.",
        )


def _check_chain_id(tx: "Tx", cfg: StatelessConfig) -> None:
    try:
        # Try to get chain_id from various locations
        tx_chain_id = getattr(tx, "chain_id", None)
        if tx_chain_id is None and hasattr(tx, "unsigned"):
            tx_chain_id = getattr(tx.unsigned, "chain_id", None)
        if tx_chain_id is None:
            raise AttributeError("chain_id not found")
        tx_chain_id = int(tx_chain_id)
    except Exception:
        raise StatelessValidationError("MissingField", "Transaction missing chain_id.")
    if tx_chain_id != int(cfg.chain_id):
        raise StatelessValidationError(
            "ChainIdMismatch",
            f"Transaction chainId {tx_chain_id} does not match node chainId {cfg.chain_id}.",
        )


def _check_gas_limits(tx: "Tx", cfg: StatelessConfig) -> None:
    # Try to get gas_limit from various locations
    gas = getattr(tx, "gas_limit", None)
    if gas is None and hasattr(tx, "unsigned"):
        gas = getattr(tx.unsigned, "gas_limit", None)
    if gas is None:
        gas = 0
    gas = int(gas)
    
    if gas <= 0:
        raise StatelessValidationError("BadGasLimit", "gas_limit must be > 0.")
    if gas > int(cfg.max_gas_limit):
        raise StatelessValidationError(
            "GasLimitExceeded",
            f"gas_limit {gas} exceeds block cap {cfg.max_gas_limit}.",
        )


def _check_payload_shape(tx: "Tx") -> None:
    """
    Basic kind-aware payload sanity checks. These are deliberately loose to
    remain compatible with VM evolution. Tight checks belong in execution.
    """
    # Unified kind mapping to avoid duplication
    KIND_MAP = {0: "transfer", 1: "deploy", 2: "call", 3: "coinbase"}
    
    kind = (getattr(tx, "kind", None) or "").lower()
    
    # Handle integer kind values (e.g., TxKind enum)
    if isinstance(kind, int):
        kind = KIND_MAP.get(kind, str(kind))
    
    # Basic sanity: data field should exist and be bounded
    try:
        data = getattr(tx, "data", None)
        if data is None and hasattr(tx, "unsigned"):
            data = getattr(tx.unsigned, "data", None)
        
        if data is not None:
            # Ensure data is bytes-like
            if not isinstance(data, (bytes, bytearray)):
                raise StatelessValidationError(
                    "MalformedPayload",
                    "Transaction data must be bytes."
                )
            # Prevent extremely large data fields (defense in depth)
            if len(data) > 10_485_760:  # 10 MiB absolute maximum
                raise StatelessValidationError(
                    "DataTooLarge",
                    f"Transaction data too large: {len(data)} bytes (max 10 MiB)"
                )
    except StatelessValidationError:
        raise
    except Exception:
        # Don't fail on edge cases in payload introspection
        pass
    
    # Re-extract kind if integer after data check
    if isinstance(kind, int):
        kind = KIND_MAP.get(kind, "")
    elif hasattr(kind, "name"):
        # Handle enum with .name attribute
        kind = kind.name.lower()
    
    # Extract from unsigned wrapper if present
    if hasattr(tx, "unsigned"):
        unsigned = tx.unsigned
        kind_val = getattr(unsigned, "kind", None)
        if isinstance(kind_val, int):
            kind = KIND_MAP.get(kind_val, "")
        elif hasattr(kind_val, "name"):
            kind = kind_val.name.lower()
        data = getattr(unsigned, "data", b"") or getattr(getattr(unsigned, "payload", None), "data", b"")
        to_field = getattr(getattr(unsigned, "payload", None), "to", None)
    else:
        data = getattr(tx, "data", b"")
        to_field = getattr(tx, "to", None)

    # Coinbase transactions are always valid (protocol-generated)
    if kind == "coinbase":
        return

    if kind in ("transfer", "xfer"):
        # For transfers, data should usually be empty or very small (e.g., memo).
        if data and len(data) > 1024:  # 1 KiB memo guardrail
            raise StatelessValidationError(
                "TransferDataTooLarge", f"transfer data too large: {len(data)} bytes."
            )
        # Transfers require a destination
        if to_field in (None, b"", ""):
            raise StatelessValidationError(
                "MissingTo", "transfer requires a 'to' address."
            )

    elif kind in ("deploy", "create"):
        # Deployments should include code bytes in data; 'to' MUST be empty.
        if to_field not in (None, b"", ""):
            raise StatelessValidationError(
                "CreateHasTo", "deploy/create must not set 'to'."
            )
        if not data or len(data) < 8:
            raise StatelessValidationError(
                "MissingCode", "deploy/create requires non-empty code bytes."
            )

    elif kind in ("call", "invoke"):
        # Calls require a destination; data may be empty (fallback) or ABI-encoded payload.
        if to_field in (None, b"", ""):
            raise StatelessValidationError(
                "MissingTo", "call/invoke requires a 'to' address."
            )

    else:
        # Unknown kinds are permitted to future-proof, but must still be reasonable.
        if len(data) > 512 * 1024:  # 512 KiB absolute guardrail for unknown kinds
            raise StatelessValidationError(
                "DataTooLarge",
                f"payload too large for unknown kind: {len(data)} bytes.",
            )


def _precheck_pq_signature(tx: "Tx") -> None:
    """
    Verify the PQ signature (fast path) against the transaction's canonical
    sign-bytes. This is purely cryptographic soundness: it does not derive
    the sender/account or check address matching.

    Raises StatelessValidationError if verification fails or fields are missing.
    
    Note: Uses safe conversion helpers (_extract_sig_tuple) so isinstance checks
    are no longer needed - values are already validated and converted.
    """
    alg_id, pubkey, signature = _extract_sig_tuple(tx)

    # Validate alg_id range (already converted to int by _extract_sig_tuple)
    if alg_id < 0:
        raise StatelessValidationError(
            "InvalidAlgId", f"Invalid algorithm ID: {alg_id}"
        )
    if alg_id > 100000:  # Sanity check for unreasonably large alg_id
        raise StatelessValidationError(
            "InvalidAlgId", f"Algorithm ID too large: {alg_id}"
        )
    
    # Validate pubkey size (already converted to bytes by _extract_sig_tuple)
    if len(pubkey) > 10000:  # Sanity check (largest PQ keys are ~2KB)
        raise StatelessValidationError(
            "InvalidPubkey", f"Public key too large: {len(pubkey)} bytes"
        )
    
    # Validate signature size (already converted to bytes by _extract_sig_tuple)
    if len(signature) > 50000:  # Sanity check (largest PQ sigs are ~16KB)
        raise StatelessValidationError(
            "InvalidSignature", f"Signature too large: {len(signature)} bytes"
        )

    # Build message bytes using whichever API the Tx exposes.
    msg = _sign_bytes_for_tx(tx)
    if not isinstance(msg, (bytes, bytearray)) or len(msg) == 0:
        raise StatelessValidationError(
            "SignBytesError", "Could not build sign-bytes for transaction."
        )

    ok = False
    try:
        # Domain separation: the canonical sign-bytes already embed the domain.
        # pq_verify supports an optional 'domain' argument; we omit it here.
        ok = bool(pq_verify(bytes(msg), bytes(signature), bytes(pubkey), int(alg_id)))
    except Exception as e:  # pragma: no cover
        raise StatelessValidationError("SigVerifyError", f"Verifier raised: {e!r}")

    if not ok:
        raise StatelessValidationError(
            "BadSignature", "Post-quantum signature verification failed."
        )


# -----------------------------
# Helpers
# -----------------------------


def _derive_cfg(
    params: Optional[ChainParams], cfg: Optional[StatelessConfig]
) -> StatelessConfig:
    if cfg is not None:
        return cfg
    if params is not None:
        # Derive from ChainParams with sensible fallbacks.
        chain_id = getattr(params, "chain_id", 0) or 0
        max_gas = getattr(params, "block_gas_limit", 30_000_000) or 30_000_000
        tx_max = getattr(params, "tx_max_bytes", 1_048_576) or 1_048_576
        return StatelessConfig(
            chain_id=int(chain_id), max_tx_bytes=int(tx_max), max_gas_limit=int(max_gas)
        )
    # Absolute fallbacks (tests/dev)
    return StatelessConfig(chain_id=1)


def _safe_to_int(value: Any, field_name: str = "value") -> int:
    """
    Safely convert a value to int, with clear error messages.
    
    Args:
        value: The value to convert
        field_name: Name of the field for error reporting
        
    Returns:
        int: The converted value
        
    Raises:
        StatelessValidationError: If conversion fails
    """
    if value is None:
        raise StatelessValidationError(
            "MissingField", f"Missing {field_name}"
        )
    
    # If already an int, return it
    if isinstance(value, int):
        return value
    
    # Try converting string representations
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise StatelessValidationError(
                "InvalidFormat", f"{field_name} must be a valid integer, got: {value!r}"
            )
    
    # Try converting bytes (could be CBOR-encoded)
    if isinstance(value, (bytes, bytearray)):
        # Reject unreasonably long byte sequences for algorithm IDs
        # Algorithm IDs should fit in a 32-bit int (4 bytes max)
        if len(value) > 4:
            raise StatelessValidationError(
                "InvalidFormat", 
                f"{field_name} byte sequence too long for int conversion: {len(value)} bytes"
            )
        try:
            # Try interpreting as string first
            return int(value.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            # Try big-endian int
            try:
                return int.from_bytes(bytes(value), 'big')
            except Exception:
                raise StatelessValidationError(
                    "InvalidFormat", f"{field_name} bytes cannot be converted to int"
                )
    
    # Try generic int() conversion for numeric types
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise StatelessValidationError(
            "InvalidFormat", 
            f"{field_name} must be int-compatible, got {type(value).__name__}: {e}"
        )


def _safe_to_bytes(value: Any, field_name: str = "value") -> bytes:
    """
    Safely convert a value to bytes, with clear error messages.
    
    Args:
        value: The value to convert
        field_name: Name of the field for error reporting
        
    Returns:
        bytes: The converted value
        
    Raises:
        StatelessValidationError: If conversion fails or value is empty
    """
    if value is None or value == b"" or value == "":
        raise StatelessValidationError(
            "MissingField", f"Missing or empty {field_name}"
        )
    
    # If already bytes, return it
    if isinstance(value, bytes):
        return value
    
    # Convert bytearray or memoryview
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    
    # Try converting hex strings
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise StatelessValidationError(
                "MissingField", f"Empty {field_name}"
            )
        # Handle hex strings with or without 0x prefix
        if value.startswith(("0x", "0X")):
            try:
                return bytes.fromhex(value[2:])
            except ValueError as e:
                raise StatelessValidationError(
                    "InvalidFormat", f"{field_name} invalid hex string: {e}"
                )
        # Try hex without prefix (but be more explicit about it)
        # Check if the string looks like hex (only [0-9a-fA-F])
        if all(c in '0123456789abcdefABCDEF' for c in value):
            try:
                return bytes.fromhex(value)
            except ValueError:
                pass  # Fall through to UTF-8
        # Not hex, treat as UTF-8 text
        try:
            return value.encode('utf-8')
        except UnicodeEncodeError as e:
            raise StatelessValidationError(
                "InvalidFormat", f"{field_name} cannot be encoded: {e}"
            )
    
    # Try converting list/tuple of integers
    if isinstance(value, (list, tuple)):
        try:
            return bytes(value)
        except (TypeError, ValueError) as e:
            raise StatelessValidationError(
                "InvalidFormat", 
                f"{field_name} list/tuple cannot be converted to bytes: {e}"
            )
    
    # For any other type, try generic bytes() conversion
    try:
        return bytes(value)
    except (TypeError, ValueError) as e:
        raise StatelessValidationError(
            "InvalidFormat", 
            f"{field_name} must be bytes-compatible, got {type(value).__name__}: {e}"
        )


def _extract_sig_tuple(tx: "Tx") -> Tuple[int, bytes, bytes]:
    """
    Extract (alg_id, pubkey, signature) from a Tx object that may use slightly
    different attribute names across drafts.
    
    Uses safe conversion helpers to provide clear error messages and handle
    various input types gracefully.
    """
    # Common names
    alg_id = getattr(tx, "alg_id", None)
    if alg_id is None:
        alg_id = getattr(tx, "sig_alg_id", None)
    
    # Use safe conversion with clear error message
    alg_id_int = _safe_to_int(alg_id, "alg_id")

    pubkey = (
        getattr(tx, "pubkey", None)
        or getattr(tx, "sender_pubkey", None)
        or getattr(tx, "pk", None)
    )
    # Use safe conversion with clear error message
    pubkey_bytes = _safe_to_bytes(pubkey, "pubkey")

    signature = (
        getattr(tx, "signature", None)
        or getattr(tx, "sig", None)
        or getattr(tx, "pq_signature", None)
    )
    # Use safe conversion with clear error message
    signature_bytes = _safe_to_bytes(signature, "signature")

    return alg_id_int, pubkey_bytes, signature_bytes


def _sign_bytes_for_tx(tx: "Tx") -> bytes:
    """
    Obtain canonical sign-bytes from the Tx object, trying common surfaces.
    """
    # Preferred: method on the Tx
    for meth in ("sign_bytes", "signing_message", "signing_bytes", "build_sign_bytes"):
        f = getattr(tx, meth, None)
        if callable(f):
            try:
                b = f()
                if isinstance(b, (bytes, bytearray)) and b:
                    return bytes(b)
            except Exception:
                pass

    # Fallback: canonical encoder function
    try:
        from core.encoding.canonical import tx_sign_bytes  # type: ignore

        b = tx_sign_bytes(tx)  # type: ignore
        if isinstance(b, (bytes, bytearray)) and b:
            return bytes(b)
    except Exception:
        pass

    # Last resort: encode via CBOR deterministically if available
    try:
        from core.encoding.cbor import dumps_canonical_tx  # type: ignore

        return dumps_canonical_tx(tx)  # type: ignore
    except Exception:
        return b""
