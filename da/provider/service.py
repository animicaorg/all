"""
Animica DA • Provider Service

HTTP service for storage providers to serve blobs to the network.
Provides blob retrieval endpoints with range request support, rate limiting,
and optional authentication.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

try:
    from fastapi import FastAPI, Header, HTTPException, Request, Response
    from fastapi.responses import FileResponse, StreamingResponse
except ImportError:
    FastAPI = None  # type: ignore
    HTTPException = Exception  # type: ignore


# -------------------------------- Rate Limiter --------------------------------


class SimpleRateLimiter:
    """
    Simple in-memory rate limiter using sliding window.
    """

    def __init__(self, requests_per_second: int = 100) -> None:
        self.rps = requests_per_second
        self.requests: Dict[str, list[float]] = defaultdict(list)

    def check_rate_limit(self, client_id: str) -> bool:
        """
        Check if client has exceeded rate limit.
        Returns True if allowed, False if rate limited.
        """
        now = time.time()
        # Clean old requests (older than 1 second)
        self.requests[client_id] = [
            t for t in self.requests[client_id] if now - t < 1.0
        ]

        if len(self.requests[client_id]) >= self.rps:
            return False

        self.requests[client_id].append(now)
        return True


# -------------------------------- Service -------------------------------------


class ProviderService:
    """
    FastAPI-based service for serving blobs.
    """

    def __init__(
        self,
        storage_path: Path,
        rate_limit_rps: int = 100,
        auth_token: Optional[str] = None,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.rate_limiter = SimpleRateLimiter(requests_per_second=rate_limit_rps)
        self.auth_token = auth_token

        if FastAPI is None:
            raise ImportError("FastAPI is required for provider service")

        self.app = FastAPI(title="Animica DA Provider Service")
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup FastAPI routes."""

        @self.app.get("/blob/{commitment}")
        async def get_blob(
            commitment: str,
            request: Request,
            range_header: Optional[str] = Header(None, alias="Range"),
            authorization: Optional[str] = Header(None),
        ) -> Response:
            """Retrieve blob by commitment with optional range support."""
            # Check authentication if configured
            if self.auth_token:
                if not authorization or not authorization.startswith("Bearer "):
                    raise HTTPException(status_code=401, detail="Unauthorized")
                token = authorization.replace("Bearer ", "")
                if token != self.auth_token:
                    raise HTTPException(status_code=401, detail="Invalid token")

            # Rate limiting
            client_ip = request.client.host if request.client else "unknown"
            if not self.rate_limiter.check_rate_limit(client_ip):
                raise HTTPException(status_code=429, detail="Too many requests")

            # Normalize commitment
            commit_hex = commitment.lower().strip()
            if commit_hex.startswith("0x"):
                commit_hex = commit_hex[2:]
            if len(commit_hex) != 64:
                raise HTTPException(status_code=400, detail="Invalid commitment")

            # Find blob file (organized by prefix for efficiency)
            prefix = commit_hex[:4]
            blob_path = self.storage_path / prefix / f"{commit_hex}.blob"

            if not blob_path.exists():
                raise HTTPException(status_code=404, detail="Blob not found")

            # Handle range requests
            if range_header:
                return await self._serve_range(blob_path, range_header)
            else:
                return FileResponse(
                    blob_path, media_type="application/octet-stream"
                )

        @self.app.head("/blob/{commitment}")
        async def check_blob(
            commitment: str,
            request: Request,
            authorization: Optional[str] = Header(None),
        ) -> Response:
            """Check if blob exists."""
            # Check authentication if configured
            if self.auth_token:
                if not authorization or not authorization.startswith("Bearer "):
                    raise HTTPException(status_code=401, detail="Unauthorized")
                token = authorization.replace("Bearer ", "")
                if token != self.auth_token:
                    raise HTTPException(status_code=401, detail="Invalid token")

            # Rate limiting
            client_ip = request.client.host if request.client else "unknown"
            if not self.rate_limiter.check_rate_limit(client_ip):
                raise HTTPException(status_code=429, detail="Too many requests")

            # Normalize commitment
            commit_hex = commitment.lower().strip()
            if commit_hex.startswith("0x"):
                commit_hex = commit_hex[2:]
            if len(commit_hex) != 64:
                raise HTTPException(status_code=400, detail="Invalid commitment")

            # Find blob file
            prefix = commit_hex[:4]
            blob_path = self.storage_path / prefix / f"{commit_hex}.blob"

            if not blob_path.exists():
                raise HTTPException(status_code=404, detail="Blob not found")

            # Return headers without body
            size = blob_path.stat().st_size
            return Response(
                headers={
                    "Content-Length": str(size),
                    "Content-Type": "application/octet-stream",
                }
            )

        @self.app.get("/health")
        async def health() -> Dict[str, str]:
            """Health check endpoint."""
            return {"status": "ok"}

    async def _serve_range(
        self, blob_path: Path, range_header: str
    ) -> StreamingResponse:
        """Serve partial content based on Range header."""
        # Parse Range header (format: "bytes=start-end")
        if not range_header.startswith("bytes="):
            raise HTTPException(status_code=400, detail="Invalid Range header")

        range_spec = range_header[6:]  # Remove "bytes="
        parts = range_spec.split("-")
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="Invalid Range format")

        file_size = blob_path.stat().st_size

        try:
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Range values")

        if start < 0 or end >= file_size or start > end:
            raise HTTPException(status_code=416, detail="Range not satisfiable")

        content_length = end - start + 1

        def file_iterator():
            with open(blob_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                chunk_size = 64 * 1024  # 64KB chunks
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        return StreamingResponse(
            file_iterator(),
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length),
                "Content-Type": "application/octet-stream",
            },
        )

    def store_blob(self, commitment: bytes, data: bytes) -> Path:
        """
        Store a blob locally using commitment-based organization.
        Returns the path where the blob was stored.
        """
        commit_hex = commitment.hex()
        prefix = commit_hex[:4]
        prefix_dir = self.storage_path / prefix
        prefix_dir.mkdir(parents=True, exist_ok=True)

        blob_path = prefix_dir / f"{commit_hex}.blob"
        blob_path.write_bytes(data)
        return blob_path

    def get_blob(self, commitment: bytes) -> Optional[bytes]:
        """Retrieve blob data by commitment."""
        commit_hex = commitment.hex()
        prefix = commit_hex[:4]
        blob_path = self.storage_path / prefix / f"{commit_hex}.blob"

        if not blob_path.exists():
            return None
        return blob_path.read_bytes()

    def has_blob(self, commitment: bytes) -> bool:
        """Check if blob exists."""
        commit_hex = commitment.hex()
        prefix = commit_hex[:4]
        blob_path = self.storage_path / prefix / f"{commit_hex}.blob"
        return blob_path.exists()


__all__ = ["ProviderService", "SimpleRateLimiter"]
