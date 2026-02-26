from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from animica_studio.services.rpc_client import RpcClient
from animica_studio.storage.config import Config

log = logging.getLogger(__name__)

CacheMode = Literal["local_first", "da_only", "disabled"]


@dataclass(slots=True)
class BootstrapInfo:
    key: str
    source: Literal["local", "da", "live", "memory"]
    fetched_at: int
    ttl_seconds: int
    payload: dict[str, Any]
    version_commitment: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


class BootstrapInfoManager:
    """Two-tier bootstrap manifest cache for Studio startup.

    Priority: local cache -> DA pointer/payload -> live probe.
    """

    def __init__(self, config: Config, *, ttl_seconds: int = 60 * 60 * 6) -> None:
        self._config = config
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._inflight = False
        self._inflight_cv = threading.Condition(self._lock)
        self._memory_cache: BootstrapInfo | None = None
        self._failure_count = 0
        self._next_retry_after = 0.0

    @property
    def cache_mode(self) -> CacheMode:
        ena = self._config.ena if isinstance(self._config.ena, dict) else {}
        raw = str(ena.get("bootstrap_cache_mode") or "local_first").strip().lower()
        if raw in {"local_first", "da_only", "disabled"}:
            return raw  # type: ignore[return-value]
        return "local_first"

    @property
    def cache_path(self) -> Path:
        return Path.home() / ".local" / "share" / "animica-studio" / "cache" / "bootstrap_info.json"

    def load(self, *, force_refresh: bool = False) -> BootstrapInfo:
        mode = self.cache_mode
        if mode == "disabled":
            return self._live_fetch(source="live")

        now = time.time()
        with self._lock:
            if not force_refresh and self._memory_cache is not None:
                self._memory_cache.source = "memory"
                return self._memory_cache
            if self._inflight:
                self._inflight_cv.wait(timeout=10.0)
                if self._memory_cache is not None:
                    self._memory_cache.source = "memory"
                    return self._memory_cache
            if now < self._next_retry_after and self._memory_cache is not None and not force_refresh:
                self._memory_cache.source = "memory"
                return self._memory_cache
            self._inflight = True

        try:
            info = self._load_impl(force_refresh=force_refresh, mode=mode)
            with self._lock:
                self._memory_cache = info
                self._failure_count = 0
                self._next_retry_after = 0.0
            return info
        except Exception:
            with self._lock:
                self._failure_count += 1
                delays = [30, 60, 120]
                self._next_retry_after = time.time() + delays[min(self._failure_count - 1, len(delays) - 1)]
            raise
        finally:
            with self._lock:
                self._inflight = False
                self._inflight_cv.notify_all()

    def publish_to_da(self) -> dict[str, Any]:
        info = self.load()
        payload_json = json.dumps(info.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload_sha = hashlib.sha256(payload_json).hexdigest()
        key = self._cache_key()
        with self._rpc_client(payload_timeout=True) as client:
            status = self._call_da_method(client, ["da.getStatus", "da_getStatus", "da.status", "da_status"], {})
            if not bool((status or {}).get("enabled")):
                raise RuntimeError("DA disabled on node; enable DA before publishing bootstrap info")
            if not bool((status or {}).get("allow_remote_put")):
                raise RuntimeError("Publishing requires allow_remote_put or local ingest mapping")

            current_pointer_commitment = self._read_da_pointer(client)
            if current_pointer_commitment:
                try:
                    current_pointer = self._fetch_blob_json(client, current_pointer_commitment)
                    if str(current_pointer.get("payload_sha256") or "") == payload_sha:
                        return {
                            "ok": True,
                            "skipped": True,
                            "reason": "payload_unchanged",
                            "payload_commitment": str(current_pointer.get("payload_commitment") or ""),
                            "pointer_commitment": current_pointer_commitment,
                            "payload_sha256": payload_sha,
                        }
                except Exception:
                    pass

            put_blob = self._call_da_method(
                client,
                ["da.putBlob", "da_putBlob"],
                {"namespace": 0, "data": "0x" + payload_json.hex()},
            )
            payload_commitment = str(put_blob if isinstance(put_blob, str) else put_blob.get("commitment") or put_blob.get("blob_id") or "")
            pointer = {
                "schema": 1,
                "name": "studio-bootstrap",
                "network": self._network_identity(client),
                "channel": self._model_channel(),
                "payload_commitment": payload_commitment,
                "payload_sha256": payload_sha,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "studio_min_version": "0.1.0",
                "cache_key": key,
            }
            pointer_hex = "0x" + json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode("utf-8").hex()
            pointer_commitment = self._call_da_method(
                client,
                ["da.putBlob", "da_putBlob"],
                {"namespace": 0, "data": pointer_hex},
            )
            pointer_commitment = str(pointer_commitment if isinstance(pointer_commitment, str) else pointer_commitment.get("commitment") or pointer_commitment.get("blob_id") or "")
            self._write_da_pointer(client, pointer_commitment=pointer_commitment)
            verified_pointer = self._fetch_blob_json(client, pointer_commitment)
            verified_payload = self._fetch_blob_json(client, payload_commitment)
            return {
                "ok": True,
                "payload_commitment": payload_commitment,
                "pointer_commitment": pointer_commitment,
                "payload_sha256": payload_sha,
                "verified": bool(verified_pointer and verified_payload),
            }

    def _load_impl(self, *, force_refresh: bool, mode: CacheMode) -> BootstrapInfo:
        local = None if mode == "da_only" else self._read_local_cache()
        if local and not force_refresh and self._is_fresh(local):
            changed = self._pointer_changed(local.version_commitment)
            if not changed:
                local.source = "local"
                return local

        da_info = self._load_from_da(local)
        if da_info is not None:
            if mode != "da_only":
                self._write_local_cache(da_info)
            da_info.source = "da"
            return da_info

        live = self._live_fetch(source="live")
        if mode != "da_only":
            self._write_local_cache(live)
        return live

    def _load_from_da(self, local: BootstrapInfo | None) -> BootstrapInfo | None:
        with self._rpc_client(payload_timeout=False) as client:
            pointer_commitment = self._read_da_pointer(client)
            if not pointer_commitment:
                return None
            if local is not None and local.version_commitment == pointer_commitment and self._is_fresh(local):
                return local
            with self._rpc_client(payload_timeout=True) as payload_client:
                pointer_blob = self._fetch_blob_json(payload_client, pointer_commitment)
                payload_commitment = str(pointer_blob.get("payload_commitment") or "")
                if not payload_commitment:
                    raise RuntimeError("Bootstrap pointer missing payload_commitment")
                payload = self._fetch_blob_json(payload_client, payload_commitment)
                return BootstrapInfo(
                    key=self._cache_key(),
                    source="da",
                    fetched_at=int(time.time()),
                    ttl_seconds=self._ttl_seconds,
                    payload=payload,
                    version_commitment=pointer_commitment,
                    diagnostics={"payload_commitment": payload_commitment},
                )

    def _live_fetch(self, *, source: Literal["live"]) -> BootstrapInfo:
        with self._rpc_client(payload_timeout=False) as client:
            discover = client.discover()
            methods = {m.get("name", "") if isinstance(m, dict) else str(m) for m in discover.get("methods", [])}
            da_status = self._safe_da_call(client, ["da.getStatus", "da_getStatus", "da.status", "da_status"], {})
            da_default_dir = self._safe_da_call(client, ["da.getDefaultDir", "da_getDefaultDir"], {})
            da_allowed_dirs = self._safe_da_call(client, ["da.getAllowedBaseDirs", "da_getAllowedBaseDirs"], {})
            source_meta = self._dataset_source_resolution()
            payload = {
                "schema": 1,
                "cache_key": self._cache_key(),
                "network": self._network_identity(client),
                "channel": self._model_channel(),
                "rpc_discover": {
                    "version": (discover.get("info") or {}).get("version"),
                    "capabilities": {
                        "da_getStatus": any(x in methods for x in ("da.getStatus", "da_getStatus", "da.status", "da_status")),
                        "da_getBlob": any(x in methods for x in ("da.getBlob", "da_getBlob")),
                        "da_putBlob": any(x in methods for x in ("da.putBlob", "da_putBlob")),
                        "rpc_discover": any(x in methods for x in ("rpc.discover", "rpc_discover")),
                    },
                },
                "da_bootstrap": {
                    "status": da_status,
                    "default_dir": da_default_dir,
                    "allowed_base_dirs": da_allowed_dirs,
                },
                "ena_channel_pointer": self._resolve_ena_pointer(client),
                "dataset_source_resolution": source_meta,
                "studio_defaults": {
                    "default_namespace": 0,
                    "safe_hyperparams": {"preset": "quick", "batch_size": 4},
                },
            }
            return BootstrapInfo(
                key=self._cache_key(),
                source=source,
                fetched_at=int(time.time()),
                ttl_seconds=self._ttl_seconds,
                payload=payload,
            )

    def _resolve_ena_pointer(self, client: RpcClient) -> dict[str, Any]:
        channel = self._model_channel()
        candidates = ["ena.getChannelPointer", "ena_getChannelPointer", "ena.getPointer", "ena_getPointer"]
        for method in candidates:
            try:
                resolved = client.resolve_method(method, candidates)
                out = client.call_with_schema(resolved, {"channel": channel})
                commitment = ""
                if isinstance(out, dict):
                    commitment = str(out.get("commitment") or out.get("pointer_commitment") or "")
                return {"exists": bool(commitment), "commitment": commitment}
            except Exception:
                continue
        return {"exists": False, "commitment": ""}

    def _read_local_cache(self) -> BootstrapInfo | None:
        p = self.cache_path
        if not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        return BootstrapInfo(
            key=str(raw.get("key") or ""),
            source="local",
            fetched_at=int(raw.get("fetched_at") or 0),
            ttl_seconds=int(raw.get("ttl_seconds") or self._ttl_seconds),
            payload=dict(raw.get("payload") or {}),
            version_commitment=str(raw.get("version_commitment") or ""),
            diagnostics={"cache_path": str(p)},
        )

    def _write_local_cache(self, info: BootstrapInfo) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": info.key,
            "version_commitment": info.version_commitment,
            "fetched_at": info.fetched_at,
            "ttl_seconds": info.ttl_seconds,
            "payload": info.payload,
        }
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _is_fresh(self, info: BootstrapInfo) -> bool:
        return (info.fetched_at + max(30, info.ttl_seconds)) > time.time()

    def _pointer_changed(self, local_commitment: str) -> bool:
        if not local_commitment:
            return False
        try:
            with self._rpc_client(payload_timeout=False) as client:
                latest = self._read_da_pointer(client)
                return bool(latest and latest != local_commitment)
        except Exception as exc:  # noqa: BLE001
            log.debug("bootstrap pointer check failed: %s", exc)
            return False

    def _fetch_blob_json(self, client: RpcClient, commitment: str) -> dict[str, Any]:
        out = self._call_da_method(client, ["da.getBlob", "da_getBlob"], {"commitment": commitment, "allow_remote_get": True})
        data = out.get("data") if isinstance(out, dict) else out
        if not isinstance(data, str):
            raise RuntimeError("Invalid DA blob response")
        raw = bytes.fromhex(data.removeprefix("0x"))
        return json.loads(raw.decode("utf-8"))

    def _read_da_pointer(self, client: RpcClient) -> str:
        channel = f"studio-bootstrap/{self._network_identity(client)}/{self._model_channel()}/latest"
        out = self._call_da_method(client, ["da.get", "da_get"], {"channel": channel, "allow_remote_get": True})
        if isinstance(out, dict):
            return str(out.get("commitment") or out.get("value") or "")
        return str(out or "")

    def _write_da_pointer(self, client: RpcClient, *, pointer_commitment: str) -> None:
        channel = f"studio-bootstrap/{self._network_identity(client)}/{self._model_channel()}/latest"
        self._call_da_method(client, ["da.put", "da_put"], {"channel": channel, "commitment": pointer_commitment})

    def _call_da_method(self, client: RpcClient, candidates: list[str], params: dict[str, Any]) -> Any:
        method = client.resolve_method(candidates[0], candidates)
        return client.call_with_schema(method, params)

    def _safe_da_call(self, client: RpcClient, candidates: list[str], params: dict[str, Any]) -> dict[str, Any]:
        try:
            out = self._call_da_method(client, candidates, params)
            return out if isinstance(out, dict) else {"value": out}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _network_identity(self, client: RpcClient) -> str:
        try:
            return str(client.get_chain_id())
        except Exception:
            try:
                disc = client.discover()
                return str((disc.get("info") or {}).get("version") or "unknown")
            except Exception:
                return "unknown"

    def _model_channel(self) -> str:
        full = self._config.ena.get("full_auto") if isinstance(self._config.ena, dict) else {}
        return str((full or {}).get("model_channel") or "ena-main").strip() or "ena-main"

    def _dataset_source_resolution(self) -> dict[str, Any]:
        ena = self._config.ena if isinstance(self._config.ena, dict) else {}
        ds = ena.get("dataset_sources") if isinstance(ena.get("dataset_sources"), dict) else {}
        providers = ds.get("providers") if isinstance(ds.get("providers"), dict) else {}
        out: dict[str, Any] = {}
        for name, value in providers.items():
            if not isinstance(value, dict):
                continue
            out[name] = {
                "base_url": str(value.get("base_url") or ""),
                "version": str(value.get("version") or ""),
                "mirrors": list(value.get("mirrors") or [])[:3],
            }
        return out

    def _cache_key(self) -> str:
        profile = self._config.get_active_profile()
        rpc_url = profile.rpc_url
        network = str(getattr(profile, "chain_id_expected", "unknown"))
        return f"{network}/{rpc_url}/{self._model_channel()}"

    def _rpc_client(self, *, payload_timeout: bool):
        if payload_timeout:
            return RpcClient(self._config.get_active_profile().rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
        return RpcClient(self._config.get_active_profile().rpc_url, connect_timeout=2.0, read_timeout=5.0, max_retries=1)

    def diagnostics(self) -> dict[str, Any]:
        current = self._memory_cache
        return {
            "cache_mode": self.cache_mode,
            "cache_path": str(self.cache_path),
            "next_retry_after": self._next_retry_after,
            "failure_count": self._failure_count,
            "current": asdict(current) if current else None,
        }
