from __future__ import annotations

from typing import Optional

try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


_current_da_root: bytes = b"\x00" * 32


def compute_da_root(payload: bytes) -> bytes:
    return sha3_256(payload)


def set_da_root(root: bytes) -> None:
    global _current_da_root
    if len(root) != 32:
        raise ValueError("DA root must be 32 bytes")
    _current_da_root = bytes(root)


def get_da_root() -> bytes:
    return bytes(_current_da_root)


def maybe_set_da_root(root_hex: Optional[str]) -> Optional[bytes]:
    if not root_hex:
        return None
    if root_hex.startswith("0x"):
        root = bytes.fromhex(root_hex[2:])
    else:
        root = bytes.fromhex(root_hex)
    set_da_root(root)
    return root


__all__ = ["compute_da_root", "get_da_root", "set_da_root", "maybe_set_da_root"]
