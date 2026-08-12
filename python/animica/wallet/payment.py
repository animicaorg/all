"""
Payment-txn signing helpers for ai/agent_runtime.

Exports `sign_payment_tx`, a thin reuse of the canonical PQ signing path
(`animica.cli.tx._build_tx_body` + `animica.tx.signing.pq_sign_tx` +
`animica.cli.tx._build_raw_tx`) so the chat client can construct a real
signed payment txn without re-implementing the wire format.

Wallet file layout: ~/.animica/wallets.json with a "wallets" array; each
entry has at minimum `address`, `alg_id`, `public_key_hex`,
`secret_key_hex`. Selection of the entry to sign with is by exact-match
on `from_address` — the caller passes the address from `WalletInfo`.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping, Optional

import cbor2

from animica.wallet.at_rest import (
    WalletEncryptionError,
    decrypt_secret_hex,
    is_encrypted_secret,
    resolve_passphrase,
)

# These helpers live in animica.cli.tx; importing them keeps the wire
# format identical to `animica tx send`, which is the only "canonical
# signer" in the codebase. Re-implementing would risk drift.
from animica.cli.tx import (
    DEFAULT_DOMAIN,
    DEFAULT_PREHASH,
    DEFAULT_TX_TTL_BLOCKS,
    _address_to_32_bytes,
    _build_raw_tx,
    _build_tx_body,
    _hex_to_bytes,
)
from animica.tx.signing import ChainContext, pq_sign_tx

# ANM → base units (nANM). Wallet show output says "1 ANM = 1,000,000,000 base
# units", which matches what the chain expects in `value` on tx bodies.
_ANM_TO_NANM = 10**9
# Default fee floor for a simple payment. Set conservatively; the AICF
# server doesn't currently verify payments, but using a realistic fee
# means the same txn would survive if/when payment verification lands.
_DEFAULT_GAS_LIMIT = 21000
# maxFee in the v2 tx body is PER GAS UNIT, not total — total cost is
# gasLimit * maxFee. The CLI's _get_default_max_fee returns 1 when the
# node doesn't expose a gas-price oracle, so 21000 * 1 = 21000 nANM
# = 0.000021 ANM in fees for a simple transfer. Matching that here.
_DEFAULT_MAX_FEE_PER_GAS_NANM = 1


class PaymentSigningError(Exception):
    """Raised when wallet selection or PQ signing fails."""


def _load_wallets_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise PaymentSigningError(f"wallet store not found at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaymentSigningError(f"failed to read wallet store {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PaymentSigningError(f"unexpected wallet store shape at {path}")
    wallets = raw.get("wallets")
    if not isinstance(wallets, list):
        raise PaymentSigningError(f"malformed wallet store at {path} (no 'wallets')")
    return wallets


def _find_wallet_entry(
    wallets: list[dict[str, Any]],
    from_address: Optional[str],
) -> dict[str, Any]:
    if from_address:
        for entry in wallets:
            if str(entry.get("address") or entry.get("addr") or "") == from_address:
                return entry
        raise PaymentSigningError(
            f"wallet store has no entry for from_address={from_address}"
        )
    # No address given — fall back to default-flagged entry, else first.
    for entry in wallets:
        if entry.get("default") is True:
            return entry
    if not wallets:
        raise PaymentSigningError("wallet store contains no entries")
    return wallets[0]


def _amount_to_nanm(amount_animica: float) -> int:
    if amount_animica < 0:
        raise PaymentSigningError("amount must be non-negative")
    # Use round() to avoid float drift at the last digit.
    return int(round(float(amount_animica) * _ANM_TO_NANM))


def _build_chain_context(
    *,
    chain_id: int,
    chain_identity: Optional[Mapping[str, Any]],
    domain: str,
    prehash: str,
) -> ChainContext:
    genesis = b""
    network = ""
    fork_id = 0
    if isinstance(chain_identity, Mapping):
        raw_genesis = (
            chain_identity.get("genesisHash")
            or chain_identity.get("genesis_hash")
            or chain_identity.get("genesis")
            or ""
        )
        if isinstance(raw_genesis, str) and raw_genesis.strip():
            try:
                genesis = _hex_to_bytes(raw_genesis)
            except Exception:
                genesis = b""
        network = str(
            chain_identity.get("network") or chain_identity.get("name") or ""
        )
        fork_raw = chain_identity.get("forkId") or chain_identity.get("fork_id") or 0
        try:
            fork_id = int(fork_raw)
        except (TypeError, ValueError):
            fork_id = 0
    return ChainContext(
        chain_id=int(chain_id),
        genesis_hash=genesis,
        network=network or "unknown",
        fork_id=fork_id,
        domain=domain,
        prehash=prehash,
    )


def sign_payment_tx(
    *,
    wallet_path: str,
    recipient: str,
    amount: float,
    nonce: int,
    chain_id: int,
    from_address: Optional[str] = None,
    chain_identity: Optional[Mapping[str, Any]] = None,
    current_height: Optional[int] = None,
    valid_after: Optional[int] = None,
    valid_until: Optional[int] = None,
    gas_limit: int = _DEFAULT_GAS_LIMIT,
    max_fee_nanm: int = _DEFAULT_MAX_FEE_PER_GAS_NANM,
    domain: str = DEFAULT_DOMAIN,
    prehash: str = DEFAULT_PREHASH,
    passphrase: Optional[str] = None,
) -> str:
    """Sign a simple value-transfer payment txn from the wallet's keys.

    Returns the canonical CBOR-encoded signed transaction as a hex string,
    suitable for embedding in an AICF payment envelope (or for direct
    submission to ``tx.submitRaw`` once the AICF protocol verifies
    payments on-chain).

    The wire format matches what ``animica tx send`` produces; the same
    private helpers are reused so there is no canonical-signer drift.

    Args:
        wallet_path: Path to ``wallets.json``.
        recipient: Destination address (bech32 ``anim1...`` or 0x hex).
        amount: Value in ANM (decimal). Converted to base units via *1e9.
        nonce: Transaction nonce (caller fetches this via ``state.getNonce``).
        chain_id: Target chain id.
        from_address: Selects the wallet entry; if omitted, the first
            entry flagged ``default``, else the first entry, is used.
        chain_identity: Optional dict with genesis_hash / network /
            fork_id for the ChainContext used in PQ domain separation.
            Without it, the signature still verifies on a node whose
            identity matches (chain_id, empty-genesis); but for strict
            verification, pass the result of ``chain.getIdentity`` RPC.
        valid_after / valid_until: Validity window in block heights. The
            tx body requires both; defaults are 0 and
            ``DEFAULT_TX_TTL_BLOCKS`` if not supplied.
        gas_limit / max_fee_nanm / domain / prehash: see ``tx.send``.

    Returns:
        Hex string of the canonical-CBOR-encoded ``{sig, body}`` envelope.

    Raises:
        PaymentSigningError: wallet lookup or signing setup failed.
    """
    path = Path(os.path.expanduser(wallet_path))
    wallets = _load_wallets_array(path)
    entry = _find_wallet_entry(wallets, from_address)
    sender = str(entry.get("address") or entry.get("addr") or "").strip()
    if not sender:
        raise PaymentSigningError("wallet entry has no address")
    pk_hex = str(entry.get("public_key_hex") or entry.get("publicKeyHex") or "")
    sk_hex = str(entry.get("secret_key_hex") or entry.get("secretKeyHex") or "")
    if not sk_hex:
        # At-rest encrypted store (ANM-C07): decrypt the secret in memory using
        # the caller-supplied passphrase, else ANIMICA_WALLET_PASSPHRASE(_FILE).
        enc = entry.get("secret_key_enc")
        if is_encrypted_secret(enc):
            secret = resolve_passphrase(passphrase)
            if not secret:
                raise PaymentSigningError(
                    "wallet secret is encrypted at rest; provide a passphrase "
                    "(pass passphrase=... or set ANIMICA_WALLET_PASSPHRASE) to sign"
                )
            try:
                sk_hex = decrypt_secret_hex(enc, secret, address=sender)
            except WalletEncryptionError as exc:
                raise PaymentSigningError(f"failed to decrypt wallet secret: {exc}") from exc
    if not pk_hex or not sk_hex:
        raise PaymentSigningError(
            "wallet entry missing public_key_hex / secret_key_hex; "
            "cannot sign without the PQ keypair"
        )
    try:
        # ANM-M07: default a missing alg_id to ml_dsa_65 (0x1003, real FIPS 204),
        # never the forgeable dilithium3 stub (0x1001).
        used_alg_id = int(entry.get("alg_id") or entry.get("algId") or 0x1003)
    except (TypeError, ValueError):
        used_alg_id = 0x1003

    pk = _hex_to_bytes(pk_hex)
    sk = _hex_to_bytes(sk_hex)

    value_base = _amount_to_nanm(amount)
    # validity window. validAfter defaults to the current head height
    # (so the mempool accepts it immediately); validUntil = validAfter + TTL.
    # If the caller doesn't know the head, default to 0 — the tx will only
    # be accepted on a chain whose current height is < TTL, which is fine
    # for genesis-era and test chains.
    if valid_after is not None:
        va = int(valid_after)
    elif current_height is not None:
        va = int(current_height)
    else:
        va = 0
    vu = int(valid_until) if valid_until is not None else (va + DEFAULT_TX_TTL_BLOCKS)
    salt = secrets.token_bytes(16)

    body = _build_tx_body(
        chain_id=int(chain_id),
        from_addr=sender,
        to_addr=recipient,
        nonce=int(nonce),
        value_base_units=value_base,
        gas_limit=int(gas_limit),
        max_fee=int(max_fee_nanm),
        data=b"",
        valid_after=va,
        valid_until=vu,
        salt=salt,
    )

    chain_ctx = _build_chain_context(
        chain_id=int(chain_id),
        chain_identity=chain_identity,
        domain=domain,
        prehash=prehash,
    )

    pq = pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)
    raw = _build_raw_tx(
        body=body,
        alg_id=pq.alg_id,
        pk=pk,
        sig=pq.sig,
        domain=domain,
        prehash=prehash,
        chain_id=int(chain_id),
    )
    return "0x" + raw.hex()


__all__ = ["sign_payment_tx", "PaymentSigningError"]
