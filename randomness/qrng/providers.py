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


# -----------------------------------------------------------------------------#
# Hardware QRNG providers (Quantum Useful Work lane)
# -----------------------------------------------------------------------------#

import dataclasses

from . import health as _health


class EntropyHealthError(RuntimeError):
    """Raised when a batch of QRNG bytes fails the SP 800-90B health battery."""

    def __init__(self, report: "_health.HealthReport") -> None:
        super().__init__("; ".join(report.reasons) or "entropy health check failed")
        self.report = report


@dataclasses.dataclass(frozen=True)
class SourceInfo:
    """Self-description of an entropy source for the Quantum Useful Work lane."""

    name: str
    vendor: str
    model: str
    is_hardware: bool
    is_quantum: bool  # genuine quantum-physical source (not a CSPRNG/conditioned mix)
    device_path: Optional[str] = None
    attested: bool = False  # whether a hardware attestation can back this source
    notes: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


class QuantumEntropySource(EntropySource):
    """EntropySource that also describes itself via ``info()`` and reports availability."""

    def info(self) -> SourceInfo:  # pragma: no cover - overridden
        raise NotImplementedError

    def available(self) -> bool:
        try:
            self.random_bytes(1)
            return True
        except Exception:
            return False


# Common Linux device nodes exposed by ID Quantique drivers / kernel HWRNG.
_QUANTIS_DEVICE_CANDIDATES = (
    "/dev/qrandom0",
    "/dev/quantis0",
    "/dev/qrng0",
    "/dev/idq0",
)


class QuantisQRNG(QuantumEntropySource):
    """
    ID Quantique **Quantis QRNG** (PCIe 40/240 Mbps or USB) provider.

    Read strategy, in order:
      1. The IDQ ``quantis`` Python binding, if installed and a card is present
         (operators with the Quantis SDK get native, library-backed reads).
      2. A character device the IDQ Linux driver exposes (``device_path`` or one
         of the common candidates). This is the dependency-free path and works
         with the kernel-HWRNG integration many deployments use.

    is_quantum=True: the Quantis output is sourced from quantum shot noise. A
    hardware attestation (device cert + HSM/TPM signature) can back it — see
    ``randomness.qrng.attest`` / ``randomness.qrng.hsm_tpm``.
    """

    def __init__(
        self,
        *,
        device_path: Optional[str] = None,
        model: str = "Quantis QRNG (PCIe/USB)",
        attested: bool = False,
        prefer_library: bool = True,
        block_size: int = 1 << 16,
    ) -> None:
        self._model = model
        self._attested = bool(attested)
        self._lib = None
        self._file: Optional[FileQRNG] = None
        self._device_path: Optional[str] = None

        if prefer_library:
            self._lib = self._try_open_library()

        if self._lib is None:
            path = device_path or self._discover_device()
            if path is not None:
                self._device_path = path
                self._file = FileQRNG(path, block_size=block_size)

    @staticmethod
    def _discover_device() -> Optional[str]:
        for cand in _QUANTIS_DEVICE_CANDIDATES:
            if os.path.exists(cand) and os.access(cand, os.R_OK):
                return cand
        return None

    @staticmethod
    def _try_open_library():
        """Best-effort load of the optional IDQ Quantis Python binding."""
        for modname in ("quantis", "Quantis", "idq_quantis"):
            try:
                mod = __import__(modname)
            except Exception:
                continue
            return mod
        return None

    def info(self) -> SourceInfo:
        return SourceInfo(
            name="quantis",
            vendor="ID Quantique",
            model=self._model,
            is_hardware=True,
            is_quantum=True,
            device_path=self._device_path,
            attested=self._attested,
            notes="optional IDQ SDK present" if self._lib is not None else "device-node read",
        )

    def random_bytes(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be non-negative")
        if n == 0:
            return b""
        if self._file is not None:
            return self._file.random_bytes(n)
        raise QRNGNotAvailable(
            "Quantis QRNG not available: no readable device node "
            f"({', '.join(_QUANTIS_DEVICE_CANDIDATES)}) and no usable SDK. "
            "Pass device_path=... or install the ID Quantique Quantis driver/SDK."
        )


class HwRngQRNG(QuantumEntropySource):
    """
    Linux kernel hardware-RNG interface (``/dev/hwrng``). Many QRNG cards, TPMs,
    and HSMs expose entropy here via the kernel ``hwrng`` framework. is_quantum is
    reported False unless the operator asserts the backing device is quantum.
    """

    def __init__(self, *, device_path: str = "/dev/hwrng", is_quantum: bool = False,
                 model: str = "kernel hwrng", block_size: int = 1 << 16) -> None:
        self._path = device_path
        self._is_quantum = bool(is_quantum)
        self._model = model
        self._file = FileQRNG(device_path, block_size=block_size)

    def info(self) -> SourceInfo:
        return SourceInfo(
            name="hwrng", vendor="kernel", model=self._model, is_hardware=True,
            is_quantum=self._is_quantum, device_path=self._path, attested=False,
            notes="Linux /dev/hwrng",
        )

    def random_bytes(self, n: int) -> bytes:
        return self._file.random_bytes(n)


class SoftwareFallbackQRNG(QuantumEntropySource):
    """
    CSPRNG software fallback (``os.urandom``). NOT a quantum source and NOT
    attestable — provided so the Quantum Useful Work pipeline is fully runnable
    and testable without QRNG hardware. Contributions made with this source are
    flagged non-attested and earn no quantum-tier reward.
    """

    def info(self) -> SourceInfo:
        return SourceInfo(
            name="software-fallback", vendor="os", model="os.urandom CSPRNG",
            is_hardware=False, is_quantum=False, device_path=None, attested=False,
            notes="non-attested fallback for testing/degraded mode",
        )

    def random_bytes(self, n: int) -> bytes:
        if n < 0:
            raise ValueError("n must be non-negative")
        return os.urandom(n)


class HealthGatedSource(QuantumEntropySource):
    """
    Wrap any EntropySource and run the SP 800-90B health battery on every read.
    Raises ``EntropyHealthError`` (carrying the HealthReport) if a batch fails.
    The most recent report is available as ``last_report``.
    """

    def __init__(
        self,
        inner: EntropySource,
        *,
        min_entropy_per_byte: float = _health.DEFAULT_MIN_ENTROPY_PER_BYTE,
        raise_on_fail: bool = True,
    ) -> None:
        self._inner = inner
        self._min_h = float(min_entropy_per_byte)
        self._raise = bool(raise_on_fail)
        self.last_report: Optional[_health.HealthReport] = None

    def info(self) -> SourceInfo:
        if isinstance(self._inner, QuantumEntropySource):
            base = self._inner.info()
            return dataclasses.replace(base, notes=(base.notes + "; health-gated").strip("; "))
        return SourceInfo(name="health-gated", vendor="?", model="?", is_hardware=False,
                          is_quantum=False, notes="health-gated wrapper")

    def random_bytes(self, n: int) -> bytes:
        data = self._inner.random_bytes(n)
        if len(data) >= _health.MIN_SAMPLES:
            report = _health.evaluate(data, min_entropy_per_byte=self._min_h)
            self.last_report = report
            if not report.passed and self._raise:
                raise EntropyHealthError(report)
        return data


def detect_sources() -> list:
    """
    Return available entropy sources best-first:
    Quantis (if device/SDK present) -> /dev/hwrng -> software fallback.
    The software fallback is always present so the lane is never fully unavailable.
    """
    found: list = []
    q = QuantisQRNG()
    if q.available():
        found.append(q)
    if os.path.exists("/dev/hwrng") and os.access("/dev/hwrng", os.R_OK):
        hw = HwRngQRNG()
        if hw.available():
            found.append(hw)
    found.append(SoftwareFallbackQRNG())
    return found


def auto_select(*, prefer_hardware: bool = True, health_gated: bool = True,
                min_entropy_per_byte: float = _health.DEFAULT_MIN_ENTROPY_PER_BYTE
                ) -> QuantumEntropySource:
    """
    Pick the best available source. With ``prefer_hardware`` (default) a real
    quantum/hardware source wins over the software fallback. Wrapped in a
    HealthGatedSource by default so unhealthy batches are rejected.
    """
    sources = detect_sources()
    chosen = sources[0]
    if prefer_hardware:
        for s in sources:
            if s.info().is_hardware:
                chosen = s
                break
    if health_gated:
        return HealthGatedSource(chosen, min_entropy_per_byte=min_entropy_per_byte)
    return chosen


__all__ = [
    "FileQRNG",
    "DeviceQRNG",
    "HTTPQRNG",
    "QuantisQRNG",
    "HwRngQRNG",
    "SoftwareFallbackQRNG",
    "HealthGatedSource",
    "QuantumEntropySource",
    "SourceInfo",
    "EntropyHealthError",
    "detect_sources",
    "auto_select",
]
