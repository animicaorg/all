"""
Shared transaction signing helpers.

This module centralizes the canonical steps for computing Animica
transaction sign-bytes, invoking a PQ signer with chain_id domain
separation, and packing the resulting signature into the wire-format
envelope expected by the node.

The helpers here are intentionally minimal and reusable by both the SDK
and the CLI so that all transaction signing flows share the exact same
logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Protocol, runtime_checkable

from omni_sdk.tx.encode import pack_signed, sign_bytes as _legacy_sign_bytes

try:
    from animica.tx.signing import DOMAIN_TX_SIGN_V1 as _DOMAIN_TX_SIGN_V1
    from animica.tx.signing import tx_signing_preimage as _tx_signing_preimage
except Exception:  # pragma: no cover - SDK standalone fallback
    _DOMAIN_TX_SIGN_V1 = "animica.tx.v1"
    _tx_signing_preimage = None


@runtime_checkable
class TxSigner(Protocol):
    """Protocol for signers capable of Animica transaction signing."""

    alg_id: int
    public_key: bytes

    def sign_tx(
        self, message: bytes, chain_id: int, fork_id: int | None = None
    ) -> bytes:
        """Sign CBOR-encoded transaction body bytes with chain_id/fork_id separation."""


@dataclass(frozen=True)
class SignedTx:
    """Result of signing a transaction."""

    sign_bytes: bytes
    signature: bytes
    raw_tx: bytes


@dataclass(frozen=True)
class SigningContext:
    """Resolved chain context needed for node-compatible tx submission signing."""

    chain_id: int
    fork_id: int | None = None
    genesis_hash: bytes = b""
    network: str = "unknown"


def _supports_fork_id(sign_tx: object) -> bool:
    try:
        sig = inspect.signature(sign_tx)
    except (TypeError, ValueError):
        # Builtins / dynamic callables: optimistically try fork_id first.
        return True

    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "fork_id" and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def _call_sign_tx_compat(
    sign_tx: object, message: bytes, chain_id: int, fork_id: int | None
) -> bytes:
    if not callable(sign_tx):  # pragma: no cover - protocol/runtime guard
        raise TypeError("signer.sign_tx is not callable")

    if _supports_fork_id(sign_tx):
        try:
            return sign_tx(message, chain_id, fork_id=fork_id)  # type: ignore[misc]
        except TypeError as exc:
            # Some wrappers obscure signatures; fall back only for fork_id arg mismatch.
            err = str(exc)
            if "fork_id" not in err:
                raise
            if "unexpected keyword" not in err and "positional arguments" not in err:
                raise
            return sign_tx(message, chain_id)  # type: ignore[misc]

    return sign_tx(message, chain_id)  # type: ignore[misc]


def _hex_to_bytes(value: str) -> bytes:
    raw = value.strip()
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if not raw:
        return b""
    if len(raw) % 2:
        raw = "0" + raw
    return bytes.fromhex(raw)


def resolve_signing_context(
    rpc: Any | None,
    *,
    chain_id: int,
    fork_id: int | None = None,
) -> SigningContext:
    """
    Resolve chain identity metadata used by node-side preimage verification.

    We intentionally tolerate missing RPC support and fall back to a minimal
    context so callers can still sign for older/offline environments.
    """

    resolved_chain_id = int(chain_id)
    resolved_fork_id = int(fork_id) if fork_id is not None else None
    genesis_hash = b""
    network = "unknown"

    if rpc is None or not hasattr(rpc, "request"):
        return SigningContext(
            chain_id=resolved_chain_id,
            fork_id=resolved_fork_id,
            genesis_hash=genesis_hash,
            network=network,
        )

    try:
        identity = rpc.request("chain.getChainIdentity", [])
    except Exception:
        identity = None

    if isinstance(identity, dict):
        chain_id_raw = identity.get("chainId", identity.get("chain_id"))
        if chain_id_raw is not None:
            identity_chain_id = int(chain_id_raw)
            if identity_chain_id != resolved_chain_id:
                raise ValueError(
                    f"chain_id mismatch: requested={resolved_chain_id} rpc={identity_chain_id}"
                )

        if resolved_fork_id is None:
            fork_id_raw = identity.get("forkId", identity.get("fork_id"))
            if fork_id_raw is not None:
                resolved_fork_id = int(fork_id_raw)

        for key in ("genesisHash", "genesis", "genesisBlockHash", "genesisHeaderHash"):
            value = identity.get(key)
            if isinstance(value, str):
                try:
                    genesis_hash = _hex_to_bytes(value)
                except Exception:
                    genesis_hash = b""
                if genesis_hash:
                    break
            elif isinstance(value, (bytes, bytearray, memoryview)):
                genesis_hash = bytes(value)
                if genesis_hash:
                    break

        network_raw = identity.get("network", identity.get("name"))
        if isinstance(network_raw, str) and network_raw.strip():
            network = network_raw.strip()

    return SigningContext(
        chain_id=resolved_chain_id,
        fork_id=resolved_fork_id,
        genesis_hash=genesis_hash,
        network=network,
    )


def build_submission_sign_bytes(tx: object, context: SigningContext) -> bytes:
    """
    Build node-compatible submission sign-bytes for a tx.

    Preferred path is `animica.tx.signing.tx_signing_preimage` so SDK signing
    bytes are identical to node verification bytes. When unavailable (standalone
    SDK installs), we fall back to legacy body-CBOR bytes.
    """

    if _tx_signing_preimage is not None:
        return _tx_signing_preimage(
            tx,
            chain_id=int(context.chain_id),
            genesis=bytes(context.genesis_hash),
            network=str(context.network),
            domain=_DOMAIN_TX_SIGN_V1,
            message_type="tx",
        )
    return _legacy_sign_bytes(tx)


def sign_transaction_for_submission(
    tx: object,
    signer: TxSigner,
    *,
    context: SigningContext,
) -> SignedTx:
    """
    Sign transaction bytes exactly as node verification expects for submission.
    """

    msg = build_submission_sign_bytes(tx, context)
    sig = _call_sign_tx_compat(
        signer.sign_tx, msg, int(context.chain_id), context.fork_id
    )
    raw = pack_signed(
        tx,
        signature=sig,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    return SignedTx(sign_bytes=msg, signature=sig, raw_tx=raw)


def sign_transaction_with_rpc_context(
    tx: object,
    signer: TxSigner,
    *,
    chain_id: int,
    rpc: Any | None,
    fork_id: int | None = None,
) -> SignedTx:
    context = resolve_signing_context(rpc, chain_id=chain_id, fork_id=fork_id)
    return sign_transaction_for_submission(tx, signer, context=context)


def sign_transaction(
    tx: object, signer: TxSigner, chain_id: int, fork_id: int | None = None
) -> SignedTx:
    """
    Sign a transaction using the provided signer and pack into a raw envelope.

    Parameters
    ----------
    tx : object
        Transaction object compatible with omni_sdk.tx.encode.canonical_body_dict.
    signer : TxSigner
        Signer implementing alg_id, public_key, and sign_tx(message, chain_id).
    chain_id : int
        Chain ID for domain separation.
    fork_id : Optional[int]
        Fork identifier for replay protection (genesis reset domain separation).

    Returns
    -------
    SignedTx
        Container with the sign-bytes, raw signature, and packed CBOR envelope.
    """

    msg = _legacy_sign_bytes(tx)
    sig = _call_sign_tx_compat(signer.sign_tx, msg, chain_id, fork_id)
    raw = pack_signed(
        tx,
        signature=sig,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    return SignedTx(sign_bytes=msg, signature=sig, raw_tx=raw)
