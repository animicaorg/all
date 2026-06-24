"""Decode a raw signed Ethereum transaction for the EVM<->native relayer.

Handles legacy (EIP-155) and EIP-1559 (type-2) transactions — the two shapes
MetaMask / ethers produce — recovering the secp256k1 sender via ecrecover and
extracting {chainId, nonce, to, value, data, gas, r, s, v}. Also enforces
secp256k1 signature hygiene (EIP-2 low-s, r/s in range, canonical v/yParity) so
malleated signatures cannot be used as distinct txs.

This module does NOT trust any client-supplied "from": the only authority is the
key recovered from the signature.
"""

from __future__ import annotations

from typing import Any, Optional

from . import formatters as F

# secp256k1 group order.
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = SECP256K1_N // 2


class RelayerReject(ValueError):
    """A structurally-decodable EVM tx that fails a policy/hygiene/EIP-155 check.

    Distinct from a plain ValueError (genuinely undecodable bytes): the caller
    surfaces this as an explicit RPC_TRANSACTION_REJECTED instead of falling
    through to the native passthrough + generic -32004 guidance.
    """


def evm_tx_hash(raw: bytes) -> str:
    """The Ethereum tx hash = keccak256(raw signed tx)."""
    return "0x" + F.keccak256(raw).hex()


def _to_int(b: Any) -> int:
    if isinstance(b, int):
        return b
    if isinstance(b, (bytes, bytearray)):
        return int.from_bytes(bytes(b), "big") if b else 0
    s = str(b)
    return int(s, 16) if s.startswith(("0x", "0X")) else int(s or 0)


def _addr(b: Any) -> Optional[str]:
    if not b:
        return None
    raw = bytes(b) if isinstance(b, (bytes, bytearray)) else bytes.fromhex(str(b)[2:] if str(b).startswith("0x") else str(b))
    if len(raw) != 20:
        return None
    return "0x" + raw.hex()


def decode_evm_tx(raw_hex: Any) -> dict:
    """Decode a raw signed Ethereum tx → dict. Raises ValueError if undecodable.

    Keys: sender (0x, from ecrecover), type, chainId (None ⇒ pre-EIP-155),
    nonce, gasLimit, to (0x or None), value (wei/nANM int), data (0x), r, s,
    v_or_yParity, and the fee fields for the tx type.
    """
    import rlp
    from eth_account import Account

    if isinstance(raw_hex, (bytes, bytearray)):
        rb = bytes(raw_hex)
    else:
        s = str(raw_hex)
        rb = bytes.fromhex(s[2:] if s.startswith(("0x", "0X")) else s)
    if not rb:
        raise ValueError("empty raw transaction")

    # Authority: the sender comes ONLY from the signature, never the body.
    try:
        sender = Account.recover_transaction(rb)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot recover sender: {exc}")
    if int(sender, 16) == 0:
        raise ValueError("recovered zero sender")

    out: dict = {"sender": sender.lower(), "raw": rb}

    if rb[0] in (1, 2):  # typed (EIP-2930 / EIP-1559)
        ttype = rb[0]
        dec = rlp.decode(rb[1:])
        if ttype == 2:
            # [chainId,nonce,maxPrio,maxFee,gas,to,value,data,accessList,yParity,r,s]
            out.update(
                type=2,
                chainId=_to_int(dec[0]), nonce=_to_int(dec[1]),
                maxPriorityFeePerGas=_to_int(dec[2]), maxFeePerGas=_to_int(dec[3]),
                gasLimit=_to_int(dec[4]), to=_addr(dec[5]), value=_to_int(dec[6]),
                data="0x" + (bytes(dec[7]).hex() if dec[7] else ""),
                v_or_yParity=_to_int(dec[9]), r=_to_int(dec[10]), s=_to_int(dec[11]),
            )
        else:  # type 1 (EIP-2930) — same fields minus the priority/max-fee split
            # [chainId,nonce,gasPrice,gas,to,value,data,accessList,yParity,r,s]
            out.update(
                type=1,
                chainId=_to_int(dec[0]), nonce=_to_int(dec[1]),
                gasPrice=_to_int(dec[2]), gasLimit=_to_int(dec[3]),
                to=_addr(dec[4]), value=_to_int(dec[5]),
                data="0x" + (bytes(dec[6]).hex() if dec[6] else ""),
                v_or_yParity=_to_int(dec[8]), r=_to_int(dec[9]), s=_to_int(dec[10]),
            )
    else:  # legacy [nonce,gasPrice,gas,to,value,data,v,r,s]
        dec = rlp.decode(rb)
        v = _to_int(dec[6])
        # EIP-155: v = chainId*2 + 35/36. v in {27,28} ⇒ pre-EIP-155 (no chainId).
        chain_id = (v - 35) // 2 if v >= 35 else None
        out.update(
            type=0, chainId=chain_id, nonce=_to_int(dec[0]), gasPrice=_to_int(dec[1]),
            gasLimit=_to_int(dec[2]), to=_addr(dec[3]), value=_to_int(dec[4]),
            data="0x" + (bytes(dec[5]).hex() if dec[5] else ""),
            v_or_yParity=v, r=_to_int(dec[7]), s=_to_int(dec[8]),
        )

    enforce_secp256k1_hygiene(out)
    return out


def enforce_secp256k1_hygiene(parsed: dict) -> None:
    """Reject malleable / non-canonical signatures (EIP-2). Raises RelayerReject."""
    r = int(parsed.get("r", 0))
    s = int(parsed.get("s", 0))
    if not (0 < r < SECP256K1_N):
        raise RelayerReject("signature r out of range")
    if not (0 < s < SECP256K1_N):
        raise RelayerReject("signature s out of range")
    if s > SECP256K1_HALF_N:
        raise RelayerReject("non-canonical signature (high-s; EIP-2 requires low-s)")
    if parsed.get("type") in (1, 2):
        if int(parsed.get("v_or_yParity", -1)) not in (0, 1):
            raise RelayerReject("invalid yParity")
    else:
        v = int(parsed.get("v_or_yParity", 0))
        if v in (27, 28):
            raise RelayerReject("pre-EIP-155 transaction (no chainId) rejected")
        if v < 35:
            raise RelayerReject("invalid legacy v")
