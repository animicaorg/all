"""Stdlib-only JSON client for the Animica Python Cloud REST API (/api/cloud/v1).

Deliberately urllib-only: this module ships inside the ``animica`` pip package and must not
drag a requests/httpx dependency into every node/miner install. Auth is a Bearer marketplace
API key (``anm_mkt_...``); the platform's session-cookie path is a browser concern and not
supported here.

Response envelope contract (lib/api.ts): success bodies are the data itself (bigints already
stringified by jsonSafe — so every ``*Nanm`` field arrives as a **decimal string**; keep it a
string or int(), never float()). Errors are ``{"error": {"code", "message", "details?"}}`` and
are raised as typed exceptions (errors.py).

Endpoint map (server routes are built in apps/animica-marketplace/app/api/cloud/v1):
    GET    /api/cloud/v1/me
    GET    /api/cloud/v1/functions            POST /api/cloud/v1/functions
    GET    /api/cloud/v1/functions/{id}       PATCH/DELETE same
    GET    /api/cloud/v1/functions/{id}/versions       POST same        (source upload)
    POST   /api/cloud/v1/functions/{id}/deploy
    GET    /api/cloud/v1/functions/{id}/deployments
    GET    /api/cloud/v1/deployments/{id}
    POST   /api/cloud/v1/functions/{id}/invoke
    POST   /api/cloud/v1/fn/{owner}/{slug}    (public endpoint — schema.prisma CloudFunction.slug)
    GET    /api/cloud/v1/executions[?functionId=&limit=]
    GET    /api/cloud/v1/executions/{requestId}
    GET    /api/cloud/v1/executions/{requestId}/logs
    GET    /api/cloud/v1/earnings
    GET    /api/cloud/v1/apps                 POST /api/cloud/v1/apps
    GET    /api/cloud/v1/apps/{slug}
    GET    /api/cloud/v1/secrets              PUT /api/cloud/v1/secrets   DELETE /api/cloud/v1/secrets
    GET    /api/cloud/v1/schedules            POST /api/cloud/v1/schedules
    DELETE /api/cloud/v1/schedules/{id}
    POST   /api/cloud/v1/validate             (server-side run of sandbox/validate.py)
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .config import CloudConfig
from .errors import (
    ApiError,
    AuthError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
    ValidationFailed,
)

API_PREFIX = "/api/cloud/v1"


def _sdk_version() -> str:
    try:
        from importlib.metadata import version

        return version("animica")
    except Exception:  # noqa: BLE001 - editable/dev installs may have no dist metadata
        return "dev"


class CloudClient:
    """One authenticated connection profile to a Python Cloud deployment.

    Cheap to construct (no I/O); every method is one HTTP round trip. All money values in
    returned dicts are decimal **strings** of integer nANM — the platform's jsonSafe() output
    passed through verbatim.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> None:
        self.config = CloudConfig.resolve(api_key=api_key, base_url=base_url, timeout_s=timeout_s)
        self._ua = f"animica-cloud-python/{_sdk_version()}"

    # ------------------------------------------------------------------ core

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        query: Optional[Dict[str, Any]] = None,
        *,
        auth: bool = True,
        timeout_s: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        url = self.config.base_url + API_PREFIX + path
        if query:
            filtered = {k: str(v) for k, v in query.items() if v is not None}
            if filtered:
                url += "?" + urlparse.urlencode(filtered)
        headers = {"accept": "application/json", "user-agent": self._ua}
        data: Optional[bytes] = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if auth:
            headers["authorization"] = f"Bearer {self.config.require_key()}"
        elif self.config.api_key:
            # Public endpoints still accept a key (requiresAuth functions demand one) —
            # send it when we have it so the caller's plan/rate-limit identity applies.
            headers["authorization"] = f"Bearer {self.config.api_key}"
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key

        req = urlrequest.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlrequest.urlopen(req, timeout=timeout_s or self.config.timeout_s) as resp:
                raw = resp.read()
        except urlerror.HTTPError as exc:
            raise self._api_error(exc) from None
        except urlerror.URLError as exc:
            raise NetworkError(f"{method} {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise NetworkError(f"{method} {url}: timed out") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise NetworkError(f"{method} {url}: non-JSON response ({raw[:200]!r})") from exc

    @staticmethod
    def _api_error(exc: urlerror.HTTPError) -> ApiError:
        status = exc.code
        code, message, details = "http_error", f"HTTP {status}", None
        try:
            payload = json.loads(exc.read() or b"{}")
            envelope = payload.get("error") or {}
            code = envelope.get("code") or code
            message = envelope.get("message") or message
            details = envelope.get("details")
        except (ValueError, OSError):
            pass
        if status in (401, 403):
            return AuthError(status, code, message, details)
        if status == 404:
            return NotFoundError(status, code, message, details)
        if status == 429:
            retry_after: Optional[int] = None
            ra = exc.headers.get("retry-after") if exc.headers else None
            if ra and str(ra).isdigit():
                retry_after = int(ra)
            return RateLimitedError(status, code, message, details, retry_after=retry_after)
        return ApiError(status, code, message, details)

    # ------------------------------------------------------------------ identity

    def me(self) -> dict:
        """The authenticated account: id, address, plan, entitlements as the server reports."""
        return self._request("GET", "/me")

    # ------------------------------------------------------------------ functions

    def list_functions(self) -> List[dict]:
        res = self._request("GET", "/functions")
        return res if isinstance(res, list) else res.get("functions", [])

    def find_function(self, slug: str) -> Optional[dict]:
        """Resolve one of the caller's own functions by slug; None when absent."""
        for fn in self.list_functions():
            if fn.get("slug") == slug:
                return fn
        return None

    def create_function(
        self,
        slug: str,
        name: str,
        *,
        entrypoint: str = "main",
        timeout_ms: int = 30_000,
        memory_mb: int = 256,
        capabilities: Optional[List[str]] = None,
        description: str = "",
        per_call_nanm: int = 0,
        requires_auth: bool = False,
        app_id: Optional[str] = None,
    ) -> dict:
        return self._request(
            "POST",
            "/functions",
            {
                "slug": slug,
                "name": name,
                "entrypoint": entrypoint,
                "timeoutMs": int(timeout_ms),
                "memoryMb": int(memory_mb),
                "capabilities": list(capabilities or []),
                "description": description,
                "perCallNanm": str(int(per_call_nanm)),  # nANM travels as a string (BigInt)
                "requiresAuth": bool(requires_auth),
                "appId": app_id,
            },
        )

    def get_function(self, function_id: str) -> dict:
        return self._request("GET", f"/functions/{function_id}")

    def update_function(self, function_id: str, **fields: Any) -> dict:
        if "perCallNanm" in fields:
            fields["perCallNanm"] = str(int(fields["perCallNanm"]))
        return self._request("PATCH", f"/functions/{function_id}", fields)

    def delete_function(self, function_id: str) -> Any:
        return self._request("DELETE", f"/functions/{function_id}")

    # ------------------------------------------------------------------ versions + deploy

    def create_version(
        self,
        function_id: str,
        source: str,
        *,
        entrypoint: str = "main",
        packages: Optional[List[str]] = None,
    ) -> dict:
        return self._request(
            "POST",
            f"/functions/{function_id}/versions",
            {"source": source, "entrypoint": entrypoint, "packages": list(packages or [])},
        )

    def list_versions(self, function_id: str) -> List[dict]:
        res = self._request("GET", f"/functions/{function_id}/versions")
        return res if isinstance(res, list) else res.get("versions", [])

    def deploy(self, function_id: str, version_id: Optional[str] = None) -> dict:
        """Kick a deployment (validate -> anchor -> activate). Returns the CloudDeployment row.

        Truthful lifecycle: the source is ANCHORED on-chain (a consensus-carried DEPLOY tx
        binding owner + hashes) and EXECUTED off-chain by the Python Cloud — consensus never
        runs the Python itself.
        """
        body: dict = {}
        if version_id:
            body["versionId"] = version_id
        return self._request("POST", f"/functions/{function_id}/deploy", body)

    def list_deployments(self, function_id: str, limit: int = 10) -> List[dict]:
        res = self._request("GET", f"/functions/{function_id}/deployments", query={"limit": limit})
        return res if isinstance(res, list) else res.get("deployments", [])

    def get_deployment(self, deployment_id: str) -> dict:
        return self._request("GET", f"/deployments/{deployment_id}")

    # ------------------------------------------------------------------ invoke

    def invoke(
        self,
        function_id: str,
        payload: Any = None,
        *,
        max_spend_nanm: Optional[int] = None,
        timeout_s: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Run a function you can address by id. Returns the full invoke response:
        {requestId, status, result|error, logs, stdout, durationMs, receipt}.

        ``max_spend_nanm`` is the caller-side price ceiling — the platform refuses (never
        clips) an execution whose quote exceeds it. Invocations can legitimately run for the
        function's whole timeout budget, so the HTTP deadline defaults to the platform's
        execution ceiling plus dispatch headroom rather than the control-plane 30s.
        """
        body: dict = {"payload": payload}
        if max_spend_nanm is not None:
            body["maxSpendNanm"] = str(int(max_spend_nanm))
        return self._request(
            "POST",
            f"/functions/{function_id}/invoke",
            body,
            timeout_s=timeout_s or 330,
            idempotency_key=idempotency_key or f"inv-{uuid.uuid4().hex}",
        )

    def invoke_public(
        self,
        owner_slug: str,
        fn_slug: str,
        payload: Any = None,
        *,
        max_spend_nanm: Optional[int] = None,
        timeout_s: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Call any published function by its public address (owner/slug). Anonymous callers
        ride the free tier; sending a key (if configured) attributes + bills the caller."""
        body: dict = {"payload": payload}
        if max_spend_nanm is not None:
            body["maxSpendNanm"] = str(int(max_spend_nanm))
        return self._request(
            "POST",
            f"/fn/{owner_slug}/{fn_slug}",
            body,
            auth=False,
            timeout_s=timeout_s or 330,
            idempotency_key=idempotency_key or f"inv-{uuid.uuid4().hex}",
        )

    # ------------------------------------------------------------------ executions + logs

    def list_executions(
        self,
        function_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[dict]:
        res = self._request("GET", "/executions", query={"functionId": function_id, "limit": limit})
        return res if isinstance(res, list) else res.get("executions", [])

    def get_execution(self, request_id: str) -> dict:
        return self._request("GET", f"/executions/{request_id}")

    def get_logs(self, request_id: str) -> List[dict]:
        res = self._request("GET", f"/executions/{request_id}/logs")
        return res if isinstance(res, list) else res.get("logs", [])

    # ------------------------------------------------------------------ earnings

    def earnings(self) -> dict:
        """Developer earnings as the server computes them from settled executions/purchases."""
        return self._request("GET", "/earnings")

    # ------------------------------------------------------------------ apps

    def list_apps(self, mine: bool = True) -> List[dict]:
        res = self._request("GET", "/apps", query={"mine": "1" if mine else None})
        return res if isinstance(res, list) else res.get("apps", [])

    def get_app(self, slug: str) -> dict:
        return self._request("GET", f"/apps/{slug}")

    def create_app(
        self,
        slug: str,
        name: str,
        *,
        tagline: str = "",
        description: str = "",
        category: str = "UTILITIES",
    ) -> dict:
        return self._request(
            "POST",
            "/apps",
            {"slug": slug, "name": name, "tagline": tagline, "description": description, "category": category},
        )

    # ------------------------------------------------------------------ secrets

    def list_secrets(self, function_id: Optional[str] = None) -> List[dict]:
        res = self._request("GET", "/secrets", query={"functionId": function_id})
        return res if isinstance(res, list) else res.get("secrets", [])

    def put_secret(self, name: str, value: str, function_id: Optional[str] = None) -> dict:
        return self._request("PUT", "/secrets", {"name": name, "value": value, "functionId": function_id})

    def delete_secret(self, name: str, function_id: Optional[str] = None) -> Any:
        return self._request("DELETE", "/secrets", {"name": name, "functionId": function_id})

    # ------------------------------------------------------------------ schedules

    def list_schedules(self) -> List[dict]:
        res = self._request("GET", "/schedules")
        return res if isinstance(res, list) else res.get("schedules", [])

    def create_schedule(
        self,
        function_id: str,
        *,
        interval_minutes: Optional[int] = None,
        cron: Optional[str] = None,
        payload: Any = None,
    ) -> dict:
        return self._request(
            "POST",
            "/schedules",
            {
                "functionId": function_id,
                "kind": "cron" if cron else "interval",
                "intervalMinutes": interval_minutes,
                "cron": cron,
                "payloadJson": json.dumps(payload if payload is not None else {}),
            },
        )

    def delete_schedule(self, schedule_id: str) -> Any:
        return self._request("DELETE", f"/schedules/{schedule_id}")

    # ------------------------------------------------------------------ validation

    def validate_remote(self, source: str, entrypoint: str = "main") -> dict:
        """Server-side validation (the same sandbox/validate.py the deploy pipeline runs).
        Returns the report; raises ValidationFailed when the server refuses the source."""
        report = self._request("POST", "/validate", {"source": source, "entrypoint": entrypoint})
        if isinstance(report, dict) and report.get("ok") is False:
            raise ValidationFailed("validation failed", findings=report.get("findings") or [])
        return report
