from __future__ import annotations

"""
Binary envelope for Animica P2P messages.

This module defines a compact frame header and helpers for packing/unpacking
frames with optional compression and authenticated encryption (AEAD).

Transport note
--------------
Transports (tcp/quic/ws) are responsible for length-prefixing each frame on
the wire. This module only turns a (msg_id, payload) into a single framed
byte string and back. See p2p/transport/tcp.py for stream segmentation.

Header layout (big-endian)
--------------------------
magic:     2 bytes   b"AM"
version:   1 byte    0x01
flags:     1 byte    bitfield (see FrameFlags)
msg_id:    2 bytes   numeric MsgID
seq:       8 bytes   monotonically increasing per-connection sequence
nonce:     8 bytes   AEAD nonce (often == seq); 0 if not encrypted
plain_len: 4 bytes   logical payload length BEFORE compression/encryption
wire_len:  4 bytes   payload bytes that follow the header (after enc/comp)
checksum:  8 bytes   first 8 bytes of sha3-256 over the *logical* payload

Total header size = 38 bytes.

AEAD usage
----------
- If ENCRYPTED flag is set, payload bytes are ciphertext and MUST be opened
  with the given AEAD using the header bytes (all 38 bytes) as AAD and the
  provided 64-bit nonce.
- If not encrypted, payload bytes are plain or compressed plaintext.

Compression
-----------
- If COMPRESSED flag is set, the decrypted payload must be zlib-decompressed
  to the logical payload. If not set, payload is already logical plaintext.
"""

import enum
import struct
import zlib
from dataclasses import dataclass
from hashlib import sha3_256
from typing import Optional, Protocol, Tuple, Union

from .message_ids import MsgID

MAGIC = b"AM"
VERSION = 1
HEADER_FMT = (
    "!2sBBHQQII8s"  # magic,ver,flags,msg_id,seq,nonce,plain_len,wire_len,checksum8
)
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class FrameFlags(enum.IntFlag):
    NONE = 0
    ENCRYPTED = 1 << 0
    COMPRESSED = 1 << 1
    MORE = 1 << 2  # reserved for chunk continuation


class AeadLike(Protocol):
    """
    Minimal AEAD interface expected by frames. The project provides wrappers in
    p2p.crypto.aead implementing this shape.

    Nonce is a 64-bit counter; the AEAD implementation is expected to map it
    to the cipher's full nonce space (e.g., prepend zeros/constant prefix).
    """

    def encrypt(self, nonce: int, aad: bytes, plaintext: bytes) -> bytes: ...
    def decrypt(self, nonce: int, aad: bytes, ciphertext: bytes) -> bytes: ...


@dataclass(frozen=True)
class Frame:
    msg_id: int
    seq: int
    flags: FrameFlags
    payload: bytes  # logical payload (after decrypt/decompress)

    def __repr__(self) -> str:
        return f"Frame(msg_id=0x{int(self.msg_id):04x}, seq={self.seq}, flags={self.flags}, payload_len={len(self.payload)})"


def _checksum8(data: bytes) -> bytes:
    return sha3_256(data).digest()[:8]


def _compress_if_beneficial(data: bytes, threshold: int) -> Tuple[bytes, bool]:
    if threshold <= 0 or len(data) < threshold:
        return data, False
    comp = zlib.compress(data, level=6)
    # Only keep if it actually helps
    if len(comp) + 1 < len(data):
        return comp, True
    return data, False


def pack_frame(
    msg_id: Union[int, MsgID],
    seq: int,
    payload: bytes,
    *,
    aead: Optional[AeadLike] = None,
    nonce: Optional[int] = None,
    compress_threshold: int = 1024,
) -> bytes:
    """
    Build a single wire frame.

    :param msg_id: numeric MsgID or enum
    :param seq: per-connection sequence (start at 0 and increment)
    :param payload: logical (plaintext) payload bytes
    :param aead: optional AEAD to encrypt with; sets ENCRYPTED flag
    :param nonce: optional 64-bit nonce; defaults to seq if not provided
    :param compress_threshold: zlib-compress if payload >= threshold (and beneficial)
    :return: header(38) + body bytes
    """
    if isinstance(msg_id, enum.IntEnum):
        mid = int(msg_id)
    else:
        mid = int(msg_id)

    # compress (logical) if it helps
    body, did_comp = _compress_if_beneficial(payload, compress_threshold)
    flags = FrameFlags.NONE
    if did_comp:
        flags |= FrameFlags.COMPRESSED

    # compute checksum over LOGICAL payload
    csum = _checksum8(payload)

    # choose nonce
    use_nonce = int(seq if nonce is None else nonce)

    # placeholder for encryption
    if aead is not None:
        flags |= FrameFlags.ENCRYPTED

    # we'll construct header AFTER we know wire_len (depends on encryption)
    # First, build a provisional header with wire_len=0; but AAD must be the final header,
    # so we will build the real header after we have the ciphertext length.
    plain_len = len(payload)

    # If we encrypt, ciphertext length may change; so do a two-pass:
    # 1) prepare body_enc = body or aead.encrypt(header_with_wire_len=?, body)
    # To avoid a circular dependency, we first assume body_enc_len = len(body),
    # build header with that wire_len, then actually encrypt and rebuild header
    # with the real length (some AEADs append tags leading to longer ciphertext).

    def build_header(wire_len: int) -> bytes:
        return struct.pack(
            HEADER_FMT,
            MAGIC,
            VERSION,
            int(flags),
            mid,
            int(seq),
            use_nonce if (flags & FrameFlags.ENCRYPTED) else 0,
            plain_len,
            wire_len,
            csum,
        )

    # First pass (un-encrypted size)
    provisional_header = build_header(len(body))

    if aead is not None:
        ciphertext = aead.encrypt(use_nonce, provisional_header, body)
        header = build_header(len(ciphertext))
        # Re-encrypt using the final header (AAD must match exactly)
        ciphertext = aead.encrypt(use_nonce, header, body)
        return header + ciphertext
    else:
        header = build_header(len(body))
        return header + body


def unpack_frame(
    framed: bytes,
    *,
    aead: Optional[AeadLike] = None,
) -> Frame:
    """
    Parse a single wire frame (no outer length prefix). Returns a Frame with
    logical (plaintext) payload after decrypt/decompress.

    :param framed: header+payload bytes
    :param aead: optional AEAD for opening encrypted frames
    """
    if len(framed) < HEADER_SIZE:
        raise ValueError(f"truncated frame: {len(framed)} < {HEADER_SIZE}")

    (
        magic,
        version,
        flags_b,
        msg_id,
        seq,
        nonce,
        plain_len,
        wire_len,
        csum,
    ) = struct.unpack(HEADER_FMT, framed[:HEADER_SIZE])

    if magic != MAGIC:
        raise ValueError("bad magic")
    if version != VERSION:
        raise ValueError(f"unsupported frame version: {version}")
    flags = FrameFlags(flags_b)

    body_wire = framed[HEADER_SIZE:]
    if len(body_wire) != wire_len:
        raise ValueError(
            f"wire_len mismatch: header={wire_len} actual={len(body_wire)}"
        )

    header_bytes = framed[:HEADER_SIZE]

    # Decrypt if needed
    if flags & FrameFlags.ENCRYPTED:
        if aead is None:
            raise ValueError("frame is encrypted but no AEAD provided")
        body = aead.decrypt(nonce, header_bytes, body_wire)
    else:
        body = body_wire

    # Decompress if needed
    if flags & FrameFlags.COMPRESSED:
        try:
            body = zlib.decompress(body)
        except zlib.error as e:
            raise ValueError(f"zlib decompress failed: {e}") from e

    # Verify length & checksum
    if len(body) != plain_len:
        raise ValueError(f"plain_len mismatch: header={plain_len} actual={len(body)}")
    if _checksum8(body) != csum:
        raise ValueError("checksum mismatch")

    return Frame(msg_id=msg_id, seq=seq, flags=flags, payload=body)


# Streaming helpers -----------------------------------------------------------


class Framer:
    """
    Convenience helper that maintains a send-sequence counter and an optional AEAD.
    Transports can use this to pack/unpack frames.
    """

    def __init__(
        self, *, aead: Optional[AeadLike] = None, compress_threshold: int = 1024
    ) -> None:
        self._aead = aead
        self._seq = 0
        self._compress_threshold = int(compress_threshold)

    @property
    def next_seq(self) -> int:
        return self._seq

    def pack(self, msg_id: Union[int, MsgID], payload: bytes) -> bytes:
        b = pack_frame(
            msg_id=msg_id,
            seq=self._seq,
            payload=payload,
            aead=self._aead,
            nonce=self._seq,  # simple schedule: nonce == seq
            compress_threshold=self._compress_threshold,
        )
        self._seq += 1
        return b

    def unpack(self, framed: bytes) -> Frame:
        return unpack_frame(framed, aead=self._aead)


__all__ = [
    "MAGIC",
    "VERSION",
    "HEADER_FMT",
    "HEADER_SIZE",
    "FrameFlags",
    "AeadLike",
    "Frame",
    "pack_frame",
    "unpack_frame",
    "Framer",
]
