"""
da_client.py
------------

Optional Data Availability (DA) adapter used by studio-services to *pin* and
optionally *retrieve* artifact blobs (e.g., contract code/ABI/manifest) to/from
the DA subsystem. If DA is not enabled or the SDK is unavailable, this adapter
gracefully no-ops for best-effort behavior.

Integration notes
-----------------
- The DA REST API can be mounted into the node RPC app (see `da/adapters/rpc_mount.py`).
- This adapter relies on the Python SDK's DA client (`omni_sdk.da.client.DAClient`)
  for wire details and canonical response shapes.
- Services should treat DA pinning as optional: store the returned `commitment`
  when available, but do not fail critical flows if DA is disabled or unreachable
  unless explicitly requested via `strict=True`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

try:
    # SDK dependency; present in this monorepo and pinned in requirements.
    from omni_sdk.da.client import DAClient  # type: ignore
except Exception:  # pragma: no cover - allows running without SDK during bootstrap
    DAClient = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PinResult:
    """Minimal DA pin result surfaced to higher layers."""

    commitment: str  # 0x-prefixed NMT root / commitment
    namespace: int  # numeric namespace id
    size: int  # original blob size (bytes)
    receipt: Optional[dict] = None  # optional DA receipt payload


class DAAdapter:
    """
    Thin, optional wrapper around the SDK DA client.

    Usage:
        da = DAAdapter.from_settings(settings)
        result = da.pin_blob(data, namespace=24)  # returns PinResult or None
        blob = da.get_blob(result.commitment)     # returns bytes or None
    """

    def __init__(
        self,
        enabled: bool,
        rpc_url: str,
        chain_id: int,
        *,
        default_namespace: int = 24,
        timeout_s: float = 15.0,
    ) -> None:
        self._enabled = bool(enabled)
        self._rpc_url = rpc_url
        self._chain_id = int(chain_id)
        self._default_ns = int(default_namespace)
        self._timeout_s = float(timeout_s)

        if self._enabled and DAClient is None:
            logger.warning(
                "DA enabled but omni_sdk.da.client is unavailable; disabling DA adapter."
            )
            self._enabled = False

        self._client: Optional[DAClient] = None
        if self._enabled:
            try:
                self._client = DAClient(self._rpc_url, chain_id=self._chain_id, timeout_s=self._timeout_s)  # type: ignore[call-arg]
                logger.info(
                    "DA adapter initialized",
                    extra={"rpc_url": self._rpc_url, "chain_id": self._chain_id},
                )
            except Exception as e:  # pragma: no cover
                logger.exception(
                    "Failed to initialize DA client; DA adapter will be disabled."
                )
                self._enabled = False
                self._client = None

    # -------------------------- factory -------------------------------------

    @classmethod
    def from_settings(cls, settings: Any) -> "DAAdapter":
        """
        Build adapter from app settings. We intentionally read keys defensively
        to avoid tight coupling with the exact Settings model.

        Expected (optional) attributes on `settings`:
          - DA_ENABLED: bool
          - RPC_URL or DA_RPC_URL: str
          - CHAIN_ID: int
          - DA_DEFAULT_NAMESPACE: int (default 24)
          - DA_TIMEOUT_S: float (default 15.0)
        """
        enabled = bool(getattr(settings, "DA_ENABLED", False))
        rpc_url = str(getattr(settings, "DA_RPC_URL", getattr(settings, "RPC_URL", "")))
        chain_id = int(getattr(settings, "CHAIN_ID", 0))
        default_ns = int(getattr(settings, "DA_DEFAULT_NAMESPACE", 24))
        timeout_s = float(getattr(settings, "DA_TIMEOUT_S", 15.0))

        if enabled and not rpc_url:
            logger.warning(
                "DA enabled but no DA_RPC_URL/RPC_URL provided; disabling DA."
            )
            enabled = False

        return cls(
            enabled=enabled,
            rpc_url=rpc_url,
            chain_id=chain_id,
            default_namespace=default_ns,
            timeout_s=timeout_s,
        )

    # -------------------------- properties ----------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    # -------------------------- operations ----------------------------------

    def pin_blob(
        self,
        data: bytes,
        *,
        namespace: Optional[int] = None,
        mime: Optional[str] = None,
        strict: bool = False,
    ) -> Optional[PinResult]:
        """
        Post a blob to DA and return the commitment and basic metadata.

        Parameters
        ----------
        data : bytes
            The artifact payload to store (code, ABI, manifest, or bundle).
        namespace : int, optional
            DA namespace id. Defaults to settings.DA_DEFAULT_NAMESPACE (24).
        mime : str, optional
            MIME type hint (purely informational; DA treats data as opaque).
        strict : bool
            If True, raise on failure; otherwise return None on any error.

        Returns
        -------
        PinResult or None (when DA is disabled or a non-strict error occurs)
        """
        if not self.enabled:
            if strict:
                raise RuntimeError("DA is not enabled")
            logger.debug("DA pin skipped (disabled).")
            return None

        ns = int(namespace if namespace is not None else self._default_ns)
        try:
            assert self._client is not None  # for type checkers
            res = self._client.post_blob(ns=ns, data=data, mime=mime)  # type: ignore[attr-defined]
            # The SDK returns a dict-like object; normalize expected fields.
            commitment = str(res.get("commitment") if isinstance(res, dict) else res.commitment)  # type: ignore[index,attr-defined]
            size = int(res.get("size") if isinstance(res, dict) else getattr(res, "size", len(data)))  # type: ignore[index]
            receipt = res.get("receipt") if isinstance(res, dict) else getattr(res, "receipt", None)  # type: ignore[index]
            logger.info(
                "DA pin ok",
                extra={"namespace": ns, "size": size, "commitment": commitment},
            )
            return PinResult(
                commitment=commitment, namespace=ns, size=size, receipt=receipt
            )
        except Exception as e:
            logger.warning("DA pin failed", exc_info=True)
            if strict:
                raise
            return None

    def get_blob(self, commitment: str, *, strict: bool = False) -> Optional[bytes]:
        """
        Retrieve a blob from DA by commitment.

        Returns bytes or None (disabled/not found/soft-fail).
        """
        if not self.enabled:
            if strict:
                raise RuntimeError("DA is not enabled")
            return None

        try:
            assert self._client is not None
            data = self._client.get_blob(commitment)  # type: ignore[attr-defined]
            if isinstance(data, (bytes, bytearray, memoryview)):
                return bytes(data)
            # Some clients might return {"data":"0x.."} shape; handle defensively.
            if isinstance(data, dict) and "data" in data:
                val = data["data"]
                if isinstance(val, (bytes, bytearray, memoryview)):
                    return bytes(val)
                if isinstance(val, str):
                    s = val.strip()
                    if s.startswith(("0x", "0X")):
                        try:
                            return bytes.fromhex(s[2:])
                        except ValueError:
                            pass
                    return s.encode("utf-8")
            return None
        except Exception:
            logger.warning(
                "DA get_blob failed", exc_info=True, extra={"commitment": commitment}
            )
            if strict:
                raise
            return None

    def get_proof(self, commitment: str, *, strict: bool = False) -> Optional[dict]:
        """
        Fetch a DA availability proof object for the given commitment.

        Returns a dict (shape depends on DA client) or None on soft-fail.
        """
        if not self.enabled:
            if strict:
                raise RuntimeError("DA is not enabled")
            return None

        try:
            assert self._client is not None
            proof = self._client.get_proof(commitment)  # type: ignore[attr-defined]
            if isinstance(proof, dict):
                return proof
            # Best-effort normalize to dict for non-dict responses
            return {"proof": proof}
        except Exception:
            logger.warning(
                "DA get_proof failed", exc_info=True, extra={"commitment": commitment}
            )
            if strict:
                raise
            return None


__all__ = ["DAAdapter", "PinResult"]
