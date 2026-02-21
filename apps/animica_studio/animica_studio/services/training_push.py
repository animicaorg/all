"""Training bundle validation, packaging, upload, and chain commit orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import time
from typing import Any

from animica_studio.services.rpc_client import RpcClient
from animica_studio.util.paths import app_data_dir

ALLOWED_SUFFIXES = {".jsonl", ".parquet", ".tar", ".zst", ".txt", ".json", ".safetensors", ".ckpt", ".bin", ".csv", ".yaml", ".yml"}


@dataclass
class BundleFile:
    path: str
    size: int
    sha3_256: str


class TrainingPushService:
    def __init__(self, rpc_url: str) -> None:
        self._rpc_url = rpc_url
        self._state_dir = app_data_dir() / "training_push"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._state_dir / "state.json"

    def build_bundle(self, inputs: list[Path], *, bundle_type: str, metadata: dict[str, Any], max_size_mb: int = 1024) -> dict[str, Any]:
        files = self._collect_files(inputs)
        total_size = sum(p.stat().st_size for p in files)
        if total_size > max_size_mb * 1024 * 1024:
            raise ValueError(f"Bundle exceeds size limit {max_size_mb}MB")
        entries: list[BundleFile] = []
        for f in files:
            if f.suffix.lower() not in ALLOWED_SUFFIXES and not f.suffix.lower().startswith(".jp") and f.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError(f"Unsupported file type: {f.name}")
            raw = f.read_bytes()
            entries.append(BundleFile(path=f.name, size=len(raw), sha3_256=hashlib.sha3_256(raw).hexdigest()))
        merkle_root = self._merkle_root([e.sha3_256 for e in entries])
        manifest = {
            "bundle_type": bundle_type,
            "metadata": metadata,
            "created_at": int(time.time()),
            "files": [e.__dict__ for e in entries],
            "bundle_root": merkle_root,
            "total_size": total_size,
        }
        bundle_path = self._write_deterministic_tar(files, manifest)
        return {"manifest": manifest, "bundle_path": bundle_path, "bundle_root": merkle_root}

    def upload_bundle(self, bundle: dict[str, Any], *, resume_key: str) -> dict[str, Any]:
        state = self._load_state()
        existing = state.get(resume_key, {})
        if existing.get("uploaded"):
            return existing
        data = Path(bundle["bundle_path"]).read_bytes()
        chunks = self._chunk_bytes(data, 512 * 1024)
        commitments: list[str] = []
        client = RpcClient(self._rpc_url)
        try:
            for idx, chunk in enumerate(chunks):
                if idx < len(existing.get("commitments", [])):
                    commitments.append(existing["commitments"][idx])
                    continue
                payload = {"data": chunk.hex()}
                commitment = self._try_da_put(client, payload)
                commitments.append(commitment)
                state[resume_key] = {"uploaded": False, "commitments": commitments, "bundle_root": bundle["bundle_root"]}
                self._save_state(state)
            uri = f"da://{bundle['bundle_root']}"
            state[resume_key] = {"uploaded": True, "commitments": commitments, "bundle_root": bundle["bundle_root"], "uri": uri}
            self._save_state(state)
            return state[resume_key]
        finally:
            client.close()

    def submit_bundle_tx(self, upload_result: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        client = RpcClient(self._rpc_url)
        try:
            methods = client._known_methods()  # noqa: SLF001
            register = self._pick(methods, "aicf.registerTrainingBundle", "ena.registerTrainingBundle", "da.register", "tx.submitTrainingRef")
            payload = {
                "bundle_root": manifest["bundle_root"],
                "manifest_commitment": hashlib.sha3_256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                "uri": upload_result.get("uri"),
                "metadata_hash": hashlib.sha3_256(json.dumps(manifest.get("metadata", {}), sort_keys=True).encode()).hexdigest(),
                "timestamp": int(time.time()),
            }
            if register:
                tx_blob = client.call(register, [payload])
            else:
                tx_blob = "0x"
            tx_hash = client.call("tx_sendRawTransaction", [tx_blob])
            return {"tx_hash": tx_hash, "register_method": register or "none", "payload": payload}
        finally:
            client.close()

    def poll_tx_status(self, tx_hash: str) -> dict[str, Any]:
        client = RpcClient(self._rpc_url)
        try:
            for method in ("tx_getTransactionReceipt", "tx_getTransactionStatus"):
                try:
                    result = client.call(method, [tx_hash])
                    return {"ok": True, "method": method, "result": result}
                except Exception:
                    continue
            return {"ok": False, "error": "No tx status method found"}
        finally:
            client.close()

    def _try_da_put(self, client: RpcClient, payload: dict[str, Any]) -> str:
        for method in ("da_putBlob", "da.putBlob"):
            try:
                out = client.call(method, [payload])
                return str(out)
            except Exception:
                continue
        return "local://export-only"

    def _collect_files(self, inputs: list[Path]) -> list[Path]:
        out: list[Path] = []
        for p in inputs:
            if p.is_dir():
                out.extend([x for x in p.rglob("*") if x.is_file()])
            elif p.is_file():
                out.append(p)
        return sorted(set(out), key=lambda p: str(p))

    def _merkle_root(self, hashes: list[str]) -> str:
        if not hashes:
            return hashlib.sha3_256(b"").hexdigest()
        level = [bytes.fromhex(h) for h in hashes]
        while len(level) > 1:
            nxt = []
            for i in range(0, len(level), 2):
                left = level[i]
                right = level[i + 1] if i + 1 < len(level) else left
                nxt.append(hashlib.sha3_256(left + right).digest())
            level = nxt
        return level[0].hex()

    def _write_deterministic_tar(self, files: list[Path], manifest: dict[str, Any]) -> str:
        tmp = Path(tempfile.mkdtemp(prefix="animica-training-"))
        out = tmp / "bundle.tar"
        with tarfile.open(out, "w") as tf:
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            mi = tarfile.TarInfo("manifest.json")
            mi.size = len(manifest_bytes)
            mi.mtime = 0
            mi.mode = 0o644
            tf.addfile(mi, fileobj=_BytesReader(manifest_bytes))
            for f in sorted(files, key=lambda p: p.name):
                data = f.read_bytes()
                ti = tarfile.TarInfo(f.name)
                ti.size = len(data)
                ti.mtime = 0
                ti.mode = 0o644
                tf.addfile(ti, fileobj=_BytesReader(data))
        return str(out)

    def _chunk_bytes(self, data: bytes, chunk: int) -> list[bytes]:
        return [data[i : i + chunk] for i in range(0, len(data), chunk)]

    def _load_state(self) -> dict[str, Any]:
        if not self._state_file.exists():
            return {}
        return json.loads(self._state_file.read_text(encoding="utf-8"))

    def _save_state(self, data: dict[str, Any]) -> None:
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._state_file)

    def _pick(self, methods: set[str], *candidates: str) -> str | None:
        for c in candidates:
            if c in methods:
                return c
        return None


class _BytesReader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._payload) - self._pos
        part = self._payload[self._pos : self._pos + n]
        self._pos += len(part)
        return part
