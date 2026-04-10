"""
randomness.qrng.providers
=========================

Pluggable QRNG provider implementations for the simple `EntropySource` protocol
defined in `randomness.qrng.__init__`.

These providers are **non-consensus** utilities. Any entropy sourced here must
not directly influence consensus-critical behavior. If mixed into beacons or
protocols, this should occur strictly through non-consensus paths or via
on-chain rules that treat QRNG input as optional/advisory.

Providers included
------------------
- FileQRNG     : Read bytes from a file-like source (e.g., /dev/urandom or a device FIFO).
- DeviceQRNG   : Thin wrapper around FileQRNG with device-centric defaults.
- HTTPQRNG     : Fetch raw bytes from an HTTP(S) endpoint that returns exactly `n` bytes.

All implementations only use the Python standard library and implement:

    def random_bytes(self, n: int) -> bytes

Security notes
--------------
- The HTTP provider performs **no** content authentication; prefer mutually
  authenticated TLS or signed payloads upstream if used in serious contexts.
- Treat remote/network sources as untrusted; validate and rate-limit as needed
  _outside_ of consensus.
"""

from __future__ import annotations

import io
import os
import ssl
import threading
import urllib.parse
import urllib.request
from typing import Optional

from . import EntropySource, QRNGNotAvailable

# -----------------------------------------------------------------------------#
# Utilities
# -----------------------------------------------------------------------------#


def _read_exact_from_file(
    f: io.BufferedReader, n: int, *, chunk_size: int = 1 << 16
) -> bytes:
    """
    Read exactly n bytes from an open binary file object, raising EOFError
    if not enough bytes are available.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    out = bytearray()
    remaining = n
    while remaining:
        read_len = min(remaining, chunk_size)
        chunk = f.read(read_len)
        if not chunk:
            raise EOFError(f"unexpected EOF: needed {remaining} more bytes")
        out.extend(chunk)
        remaining -= len(chunk)
    return bytes(out)


# -----------------------------------------------------------------------------#
# File-backed provider
# -----------------------------------------------------------------------------#


class FileQRNG(EntropySource):
    """
    Read entropy bytes from a file path (e.g., a character device or FIFO).

    Args:
        path: File path to read from.
        reopen_each_call: If True (default), open/close the file per call to
            `random_bytes` for simplicity and resilience to rotations.
            If False, keeps a shared handle; guarded by a lock for thread safety.
        block_size: Internal read chunk size.
    """

    def __init__(
        self, path: str, *, reopen_each_call: bool = True, block_size: int = 1 << 16
    ):
        if not path or not isinstance(path, str):
            raise ValueError("path must be a non-empty string")
        self._path = path
        self._reopen = reopen_each_call
        self._block = block_size
        self._lock = threading.Lock()
        self._fh: Optional[io.BufferedReader] = (
            None  # kept only if reopen_each_call=False
        )

    def _ensure_open(self) -> io.BufferedReader:
        if self._fh is not None:
            return self._fh
        # Open in binary read mode; buffering handled by io
        fh = open(self._path, "rb", buffering=0)  # unbuffered; we'll buffer ourselves
        # Wrap in a buffered reader for efficient small reads
        buf = io.BufferedReader(fh, buffer_size=self._block)
        self._fh = buf
        return buf

    def _close_if_needed(self) -> None:
        if self._reopen:
            try:
                if self._fh is not None:
                    self._fh.close()
            finally:
                self._fh = None

    def random_bytes(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be non-negative")
        if n == 0:
            return b""
        if self._reopen:
            # Open-close path per call
            with open(self._path, "rb", buffering=0) as fh:
                buf = io.BufferedReader(fh, buffer_size=self._block)
                return _read_exact_from_file(buf, n, chunk_size=self._block)
        # Shared-handle path
        with self._lock:
            fh = self._ensure_open()
            try:
                return _read_exact_from_file(fh, n, chunk_size=self._block)
            except Exception:
                # On any failure, drop the handle to allow recovery next call
                try:
                    fh.close()
                finally:
                    self._fh = None
                raise


# -----------------------------------------------------------------------------#
# Device provider (thin wrapper)
# -----------------------------------------------------------------------------#


class DeviceQRNG(FileQRNG):
    """
    Device-centric QRNG provider. Defaults are suitable for character devices,
    but it is just a thin wrapper around FileQRNG.

    Example paths:
        - Linux: /dev/hwrng, /dev/ttyACM0 (device exposing binary stream)
        - BSD   : /dev/random (beware: system CSPRNG, not QRNG)
        - Custom: Vendor-specific device nodes or FIFOs

    NOTE: This class does *not* set O_NONBLOCK explicitly. If you need that,
    open the device yourself and pass a FIFO/pipe to FileQRNG, or ensure the
    device semantics are blocking with guaranteed output.
    """

    def __init__(
        self,
        device_path: str,
        *,
        reopen_each_call: bool = True,
        block_size: int = 1 << 15,
    ):
        super().__init__(
            device_path, reopen_each_call=reopen_each_call, block_size=block_size
        )


# -----------------------------------------------------------------------------#
# HTTP(S) provider
# -----------------------------------------------------------------------------#


class HTTPQRNG(EntropySource):
    """
    Fetch entropy bytes from an HTTP(S) endpoint.

    The endpoint is expected to return **exactly** `n` raw bytes for each request.
    This class will perform multiple requests if `n` exceeds `max_per_request`.

    Args:
        base_url: Endpoint base URL.
        timeout: Socket timeout in seconds per request.
        param_name: Query parameter key used to request a specific byte count (default: "n").
        headers: Optional dict of HTTP headers (e.g., {"Authorization": "Bearer ..."}).
        ssl_context: Optional custom SSLContext for TLS settings.
        max_per_request: Upper bound on `n` per HTTP call; large requests are chunked.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        param_name: str = "n",
        headers: Optional[dict[str, str]] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        max_per_request: int = 1 << 20,  # 1 MiB per request
    ) -> None:
        if not base_url or not isinstance(base_url, str):
            raise ValueError("base_url must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_per_request <= 0:
            raise ValueError("max_per_request must be positive")

        self._base = base_url
        self._timeout = timeout
        self._param = param_name
        self._headers = dict(headers or {})
        self._ctx = ssl_context
        self._max = max_per_request

    def _one(self, need: int) -> bytes:
        # Build URL with `n` query param
        parts = urllib.parse.urlsplit(self._base)
        qs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        qs.append((self._param, str(need)))
        new_qs = urllib.parse.urlencode(qs)
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_qs, parts.fragment)
        )

        req = urllib.request.Request(url, headers=self._headers, method="GET")
        # Open with optional SSL context
        if self._ctx is None:
            resp = urllib.request.urlopen(
                req, timeout=self._timeout
            )  # nosec - caller chooses URL
        else:
            resp = urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx)  # type: ignore[call-arg] # nosec

        with resp:
            # Read exactly `need` bytes (servers may not deliver at once)
            out = bytearray()
            remaining = need
            while remaining:
                chunk = resp.read(remaining)
                if not chunk:
                    break
                out.extend(chunk)
                remaining -= len(chunk)
            b = bytes(out)
            if len(b) != need:
                raise QRNGNotAvailable(
                    f"HTTPQRNG short read: expected {need} bytes, got {len(b)} (url={self._base})"
                )
            return b

    def random_bytes(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be non-negative")
        if n == 0:
            return b""
        out = bytearray()
        remaining = n
        while remaining:
            take = min(remaining, self._max)
            out.extend(self._one(take))
            remaining -= take
        return bytes(out)


__all__ = [
    "FileQRNG",
    "DeviceQRNG",
    "HTTPQRNG",
]
