"""DaService — blob put/get/proof with client-side chunking strategy."""

from __future__ import annotations

import base64
import hashlib
import logging
import math
from typing import Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 256 * 1024  # 256 KiB


def _animica_bin(config: Config) -> str:
    return config.get_active_profile().cli.animica_bin


class DaService:
    """Data Availability operations: put/get/proof with optional chunking."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._runner = CliRunner()

    def _rpc_url(self, override: str | None = None) -> str:
        raw = override or self._config.get_active_profile().node.rpc_local_url
        url = raw.rstrip("/")
        if not url.endswith("/rpc"):
            url = url + "/rpc"
        return url

    def _client(self, override: str | None = None) -> RpcClient:
        return RpcClient(self._rpc_url(override), connect_timeout=4.0, read_timeout=30.0, max_retries=2)

    # ------------------------------------------------------------------
    # Put blob
    # ------------------------------------------------------------------

    def put_blob(
        self,
        data: bytes,
        namespace: str | None = None,
        rpc_url: str | None = None,
        *,
        progress_cb: Callable[[int, int], None] | None = None,
        cancel_token: CancelToken | None = None,
    ) -> dict:
        """Upload *data* as a DA blob.

        For blobs larger than :data:`_DEFAULT_CHUNK_SIZE`, the data is split
        into chunks and each chunk is submitted individually.  The method
        returns a ``commitment`` list (one per chunk) and a ``root`` hash.

        Parameters
        ----------
        data:
            Raw blob bytes.
        namespace:
            Optional namespace string for the blob.
        rpc_url:
            Override RPC URL.
        progress_cb:
            Called with ``(chunks_done, total_chunks)`` after each chunk.
        cancel_token:
            Allows aborting a chunked upload.
        """
        if len(data) <= _DEFAULT_CHUNK_SIZE:
            return self._put_single(data, namespace, rpc_url)

        # Chunked upload
        return self._put_chunked(
            data,
            namespace,
            rpc_url,
            progress_cb=progress_cb,
            cancel_token=cancel_token,
        )

    def _put_single(self, data: bytes, namespace: str | None, rpc_url: str | None) -> dict:
        client = self._client(rpc_url)
        try:
            b64 = base64.b64encode(data).decode()
            params: dict = {"data": b64}
            if namespace:
                params["namespace"] = namespace
            # Try multiple method names (node may use either)
            for method in ("da_putBlob", "da.putBlob"):
                try:
                    result = client.call(method, [params])
                    return {"ok": True, "commitment": result, "chunks": 1}
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": "da_putBlob not available on this node"}
        finally:
            client.close()

    def _put_chunked(
        self,
        data: bytes,
        namespace: str | None,
        rpc_url: str | None,
        *,
        progress_cb: Callable[[int, int], None] | None,
        cancel_token: CancelToken | None,
    ) -> dict:
        total_size = len(data)
        total_chunks = math.ceil(total_size / _DEFAULT_CHUNK_SIZE)
        commitments: list = []
        chunk_hashes: list[str] = []

        for i in range(total_chunks):
            if cancel_token and cancel_token.is_cancelled:
                return {"ok": False, "error": "Cancelled"}
            chunk = data[i * _DEFAULT_CHUNK_SIZE : (i + 1) * _DEFAULT_CHUNK_SIZE]
            chunk_hash = hashlib.sha256(chunk).hexdigest()
            chunk_hashes.append(chunk_hash)

            result = self._put_single(chunk, namespace, rpc_url)
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error"), "chunks_done": i, "total_chunks": total_chunks}
            commitments.append(result.get("commitment"))
            if progress_cb:
                progress_cb(i + 1, total_chunks)

        # Compute a root from all chunk commitments
        root = hashlib.sha256(b"".join(c.encode() if isinstance(c, str) else repr(c).encode() for c in commitments)).hexdigest()
        return {
            "ok": True,
            "commitments": commitments,
            "chunks": total_chunks,
            "root": root,
            "total_bytes": total_size,
        }

    # ------------------------------------------------------------------
    # Get blob
    # ------------------------------------------------------------------

    def get_blob(self, commitment: str, rpc_url: str | None = None) -> dict:
        """Download a blob by commitment hash."""
        client = self._client(rpc_url)
        try:
            for method in ("da_getBlob", "da.getBlob"):
                try:
                    result = client.call(method, [commitment])
                    if isinstance(result, dict) and result.get("data"):
                        raw = base64.b64decode(result["data"])
                        return {"ok": True, "data": raw, "raw": result}
                    return {"ok": True, "data": result}
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": "da_getBlob not available on this node"}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Get proof
    # ------------------------------------------------------------------

    def get_proof(self, commitment: str, rpc_url: str | None = None) -> dict:
        """Retrieve inclusion proof for a commitment."""
        client = self._client(rpc_url)
        try:
            for method in ("da_getProof", "da.getProof"):
                try:
                    result = client.call(method, [commitment])
                    return {"ok": True, "proof": result}
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": "da_getProof not available on this node"}
        finally:
            client.close()

    # ------------------------------------------------------------------
    # CLI-based large blob upload
    # ------------------------------------------------------------------

    def put_blob_cli(
        self,
        file_path: str,
        namespace: str | None = None,
        *,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
    ) -> ExecResult:
        """Upload a file via the ``animica da put`` CLI command."""
        bin_ = _animica_bin(self._config)
        cmd = [bin_, "da", "put", file_path]
        if namespace:
            cmd += ["--namespace", namespace]
        return self._runner.run(cmd, cancel_token=cancel_token, stream_cb=stream_cb, timeout_s=300.0)
