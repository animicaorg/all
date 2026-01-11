"""Request approval queue for external wallet RPC calls."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal


@dataclass
class ApprovalRequest:
    """A pending approval request from an external application."""
    request_id: str
    method: str
    params: dict[str, Any]
    requester_info: dict[str, Any]  # PID, process name, etc.
    created_at: float
    status: Literal["pending", "approved", "denied", "expired"]
    response: Any | None = None
    error: str | None = None


class ApprovalQueue:
    """Thread-safe queue for managing approval requests."""
    
    def __init__(self, persistence_path: Path | None = None, max_pending: int = 100):
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = Lock()
        self._persistence_path = persistence_path
        self._max_pending = max_pending
        self._load_state()
    
    def create_request(
        self,
        method: str,
        params: dict[str, Any],
        requester_info: dict[str, Any],
    ) -> str:
        """Create a new approval request and return its ID."""
        request_id = str(uuid.uuid4())
        request = ApprovalRequest(
            request_id=request_id,
            method=method,
            params=params,
            requester_info=requester_info,
            created_at=time.time(),
            status="pending",
        )
        
        with self._lock:
            # Enforce max pending limit
            pending_count = sum(1 for r in self._requests.values() if r.status == "pending")
            if pending_count >= self._max_pending:
                raise RuntimeError(f"Too many pending requests (max: {self._max_pending})")
            
            self._requests[request_id] = request
            self._persist()
        
        return request_id
    
    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get a request by ID."""
        with self._lock:
            return self._requests.get(request_id)
    
    def list_pending(self, max_age_seconds: float = 300) -> list[ApprovalRequest]:
        """List all pending requests that are not expired."""
        now = time.time()
        with self._lock:
            # Auto-expire old requests
            for req in self._requests.values():
                if req.status == "pending" and (now - req.created_at) > max_age_seconds:
                    req.status = "expired"
            
            self._persist()
            return [r for r in self._requests.values() if r.status == "pending"]
    
    def approve(self, request_id: str, response: Any) -> None:
        """Approve a request with the given response."""
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                raise ValueError(f"Request {request_id} not found")
            if request.status != "pending":
                raise ValueError(f"Request {request_id} is not pending (status: {request.status})")
            
            request.status = "approved"
            request.response = response
            self._persist()
    
    def deny(self, request_id: str, reason: str) -> None:
        """Deny a request with the given reason."""
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                raise ValueError(f"Request {request_id} not found")
            if request.status != "pending":
                raise ValueError(f"Request {request_id} is not pending (status: {request.status})")
            
            request.status = "denied"
            request.error = reason
            self._persist()
    
    def cleanup_old(self, max_age_seconds: float = 3600) -> int:
        """Remove old completed/expired requests and return count removed."""
        now = time.time()
        removed = 0
        
        with self._lock:
            to_remove = [
                req_id
                for req_id, req in self._requests.items()
                if req.status in ("approved", "denied", "expired")
                and (now - req.created_at) > max_age_seconds
            ]
            
            for req_id in to_remove:
                del self._requests[req_id]
                removed += 1
            
            if removed > 0:
                self._persist()
        
        return removed
    
    def _persist(self) -> None:
        """Persist queue state to disk (called with lock held)."""
        if not self._persistence_path:
            return
        
        data = {
            "requests": {
                req_id: {
                    "request_id": req.request_id,
                    "method": req.method,
                    "params": req.params,
                    "requester_info": req.requester_info,
                    "created_at": req.created_at,
                    "status": req.status,
                    "response": req.response,
                    "error": req.error,
                }
                for req_id, req in self._requests.items()
            }
        }
        
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    def _load_state(self) -> None:
        """Load queue state from disk."""
        if not self._persistence_path or not self._persistence_path.exists():
            return

        try:
            data = json.loads(self._persistence_path.read_text(encoding="utf-8"))
            requests_data = data.get("requests", {})

            with self._lock:
                self._requests = {
                    req_id: ApprovalRequest(**req_data)
                    for req_id, req_data in requests_data.items()
                }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            # Log but don't crash - start with empty queue if we can't load
            import logging

            logging.getLogger(__name__).warning("Failed to load approval queue state: %s", e)
