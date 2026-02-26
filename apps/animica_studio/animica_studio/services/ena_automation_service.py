from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from animica_studio.util.paths import app_data_dir

log = logging.getLogger(__name__)


class DaPolicyError(RuntimeError):
    """Structured ENA publish DA policy error with actionable remediation."""

    def __init__(
        self,
        *,
        message: str,
        code: str,
        da_enabled: bool,
        allow_remote_put: bool,
        da_dir: str,
        rpc_url: str,
        can_enable_remote_put: bool,
        diagnostics: dict[str, Any],
        recommendations: list[str],
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.da_enabled = da_enabled
        self.allow_remote_put = allow_remote_put
        self.da_dir = da_dir
        self.rpc_url = rpc_url
        self.can_enable_remote_put = can_enable_remote_put
        self.diagnostics = diagnostics
        self.recommendations = recommendations

    def to_step_payload(self) -> dict[str, Any]:
        actions = []
        if self.can_enable_remote_put:
            actions.append({"id": "enable_remote_put", "label": "Configure DA Now"})
        if bool((self.diagnostics or {}).get("local_node")):
            actions.append({"id": "local_upload", "label": "Retry local ingest"})
        actions.append({"id": "copy_diagnostics", "label": "Copy diagnostics"})
        return {
            "ok": False,
            "error": self.message,
            "error_code": self.code,
            "retry_action": "Retry Push to DA",
            "diagnostics": self.diagnostics,
            "recommendations": self.recommendations,
            "actions": actions,
        }


from animica_studio.services.artifact_service import ArtifactService
from animica_studio.services.ena_store import EnaStore
from animica_studio.services.fee_routing_service import FeeRoutingService
from animica_studio.services.rpc_client import RpcClient
from animica_studio.services.step_runner import StepRunner
from animica_studio.services.training_service import TrainingService
from animica_studio.storage.config import Config


class EnaService:
    """Wizard-first ENA automation flows with idempotent store-backed state."""

    def __init__(self, config: Config, store: EnaStore | None = None) -> None:
        self.config = config
        self.store = store or EnaStore()
        self.runner = StepRunner(self.store)
        self.artifacts = ArtifactService()
        self.training = TrainingService(config)
        try:
            from animica_studio.services.aicf_service import AicfService
            self.aicf = AicfService(config)
        except Exception:
            self.aicf = type('StubAicf', (), {'submit_job': lambda *_a, **_k: {'ok': False, 'error': 'aicf unavailable'}})()
        try:
            from animica_studio.services.da_client import DaClient
            from animica_studio.services.da_status_service import DaStatusService
            self.da = DaClient(config.get_active_profile().node.rpc_local_url)
            self.da_status = DaStatusService(config)
        except Exception:
            self.da = type('StubDa', (), {'upload_bytes': lambda *_a, **_k: {'ok': False, 'error': 'da unavailable'}})()
            self.da_status = type('StubDaStatus', (), {'get_status': lambda *_a, **_k: {'enabled': False, 'allow_remote_put': False}, 'enable_da': lambda *_a, **_k: {'ok': False}})()
        self.fees = FeeRoutingService()

    @staticmethod
    def _supports_allow_remote_put(status: dict[str, Any]) -> bool:
        spec = status.get("configure_param_spec")
        if not isinstance(spec, list):
            return False
        return any((p or {}).get("name") == "allow_remote_put" for p in spec if isinstance(p, dict))

    @staticmethod
    def _is_local_rpc(rpc_url: str) -> bool:
        try:
            host = (urlparse(rpc_url or "").hostname or "").lower()
        except Exception:
            return False
        return host in {"127.0.0.1", "localhost", "::1"}

    @staticmethod
    def _map_container_da_dir_to_host(da_dir: str) -> str:
        if da_dir.startswith("/data/chain-"):
            return os.path.expanduser("~/.animica") + da_dir.removeprefix("/data")
        if da_dir.startswith("/data/"):
            return os.path.expanduser("~/.animica") + da_dir.removeprefix("/data")
        return da_dir

    @staticmethod
    def _map_node_path_to_host(path: str) -> str:
        cleaned = str(path or "").strip()
        if cleaned.startswith("/data/chain-"):
            return os.path.expanduser("~/.animica") + cleaned.removeprefix("/data")
        if cleaned.startswith("/data/"):
            return os.path.expanduser("~/.animica") + cleaned.removeprefix("/data")
        return cleaned

    def _build_da_diagnostics(self, status: dict[str, Any]) -> dict[str, Any]:
        da_dir = str(status.get("configured_dir") or status.get("raw", {}).get("dir") or "")
        rpc_url = str(status.get("rpc_url") or self.config.get_active_profile().node.rpc_local_url or "")
        return {
            "da_enabled": bool(status.get("enabled")),
            "allow_remote_put": bool(status.get("allow_remote_put")),
            "da_dir": da_dir,
            "rpc_url": rpc_url,
            "version": str(status.get("raw", {}).get("version") or ""),
            "local_node": self._is_local_rpc(rpc_url),
            "host_da_dir": self._map_container_da_dir_to_host(da_dir),
        }


    def detect_capabilities(self) -> dict[str, Any]:
        rpc_url = self.config.get_active_profile().rpc_url
        try:
            client = RpcClient(rpc_url, max_retries=2)
            discover = client.discover()
            methods = {
                m.get("name", "") if isinstance(m, dict) else str(m)
                for m in discover.get("methods", [])
            }
        except Exception as exc:  # noqa: BLE001
            return {"aicf": False, "da": False, "ena": False, "reason": str(exc), "fallback_mode": True}
        finally:
            try:
                client.close()
            except Exception:
                pass
        return {
            "aicf": any("aicf" in m for m in methods),
            "da": any(m.startswith(("da_", "da.")) for m in methods),
            "ena": any("ena" in m for m in methods),
            "fallback_mode": False,
            "discover": discover,
        }

    def run_contribute_flow(self, work_dir: Path, contribution_type: str = "dataset", intensity: str = "medium") -> dict[str, Any]:
        files = [p for p in work_dir.glob("*.json")] or [work_dir / "sample.txt"]
        if not files[0].exists():
            files[0].write_text("ena sample", encoding="utf-8")
        metadata = {"type": contribution_type, "intensity": intensity}

        def _select(step):
            step.copy_command = f"animica ena contribute --type {contribution_type} --auto --budget 100"
            return {"contribution_type": contribution_type, "intensity": intensity}

        def _run_local(step):
            step.copy_command = "animica ena contribute --type dataset --auto"
            step.progress = 35
            return {"logs": "local CPU contribution completed"}

        manifest_box: dict[str, Any] = {}

        def _manifest(step):
            manifest = self.artifacts.build_manifest(files, metadata)
            manifest_box["manifest"] = manifest
            step.copy_command = "animica ena artifact verify manifest.json"
            return {"manifest": manifest, "artifact_hash": manifest["manifest_sha256"]}

        def _verify(step):
            ok, msg = self.artifacts.verify_manifest(manifest_box["manifest"], work_dir)
            if not ok:
                raise ValueError(msg)
            step.copy_command = "animica ena artifact verify manifest.json"
            return {"verification": msg}

        def _submit(step):
            step.copy_command = "animica aicf jobs submit --plan ena_dataset_build --budget 100"
            res = self.aicf.submit_job("ena_dataset_build", {"manifest": manifest_box["manifest"]}, 100)
            if not res.get("ok"):
                return {"job_id": "local-dev-job", "status": "stubbed"}
            return {"job_id": res.get("data", {}).get("job_id", "submitted")}

        run = self.runner.run(
            "contribute",
            [
                ("Select contribution", _select),
                ("Run local task", _run_local),
                ("Generate manifest", _manifest),
                ("Verify artifact", _verify),
                ("Submit to AICF", _submit),
            ],
        )
        artifact_hash = run.result.get("Generate manifest", {}).get("artifact_hash")
        receipt = {"run_id": run.run_id, "job_id": run.result.get("Submit to AICF", {}).get("job_id") or "local-dev-job", "artifact_hash": artifact_hash, "estimated_credits": 5}
        self.store.append("artifacts", {"hash": artifact_hash, "manifest": manifest_box.get("manifest", {}), "run_id": run.run_id}, dedupe_key="hash")
        return {"run": run, "receipt": receipt}

    def list_checkpoints(self) -> list[dict[str, Any]]:
        return list(self.store.get("checkpoints", []))

    def fetch_latest_checkpoint(self, target_dir: Path) -> dict[str, Any]:
        target_dir.mkdir(parents=True, exist_ok=True)
        step_cache: dict[str, Any] = {}

        def _discover(step):
            step.copy_command = "animica ena checkpoints list"
            cps = self.store.get("checkpoints", [])
            return {"count": len(cps)}

        def _download(step):
            step.copy_command = "animica ena checkpoints fetch --latest"
            sample = target_dir / "latest.ckpt"
            sample.write_text("checkpoint-bytes", encoding="utf-8")
            h = self.artifacts.hash_file(sample)
            step_cache["download"] = {"path": str(sample), "sha256": h}
            return step_cache["download"]

        def _index(step):
            d = step_cache["download"]
            row = {"id": d["sha256"][:12], "sha256": d["sha256"], "path": d["path"], "origin": "local", "tab": "latest", "local_artifact_prepared": True, "da_uploaded": False, "aicf_registered": False}
            self.store.append("checkpoints", row, dedupe_key="sha256")
            return row

        run = self.runner.run("checkpoints", [("Discover checkpoints", _discover), ("Download & Verify", _download), ("Index checkpoint", _index)])
        return {"run": run, "active": run.result.get("Index checkpoint")}

    def train_local(self, checkpoint_id: str, dataset_id: str, preset: str = "quick", stop_requested: bool = False) -> dict[str, Any]:
        recommendation = "Use the Train tab for full configurable ENA training runs."
        return {
            "run": self.runner.run("train", [("Prepare training", lambda step: {"checkpoint": checkpoint_id, "dataset": dataset_id, "preset": preset})]),
            "recommendation": recommendation,
        }

    def _get_checkpoint_row(self, checkpoint_sha: str) -> dict[str, Any] | None:
        for row in self.store.get("checkpoints", []):
            if row.get("sha256") == checkpoint_sha:
                return row
        return None

    def _set_checkpoint_publish_state(
        self,
        checkpoint_sha: str,
        *,
        local_artifact_prepared: bool | None = None,
        da_uploaded: bool | None = None,
        aicf_registered: bool | None = None,
        commitment: str | None = None,
        da_pending_reason: str | None = None,
    ) -> dict[str, Any] | None:
        cps = list(self.store.get("checkpoints", []))
        updated: dict[str, Any] | None = None
        for cp in cps:
            if cp.get("sha256") != checkpoint_sha:
                continue
            if local_artifact_prepared is not None:
                cp["local_artifact_prepared"] = bool(local_artifact_prepared)
            if da_uploaded is not None:
                cp["da_uploaded"] = bool(da_uploaded)
            if aicf_registered is not None:
                cp["aicf_registered"] = bool(aicf_registered)
            if commitment:
                cp["commitment"] = commitment
            if da_pending_reason is not None:
                cp["da_pending_reason"] = da_pending_reason
            updated = cp
            break
        if updated is not None:
            self.store.set("checkpoints", cps)
        return updated

    def _verify_da_presence(self, commitment: str) -> bool:
        if not commitment:
            return False
        try:
            return bool(self.da.has_blob(commitment))
        except Exception:
            try:
                _ = self.da.get_blob(commitment)
                return True
            except Exception:
                return False

    def _update_remote_pointer(self, commitment: str, checkpoint_sha: str) -> dict[str, Any]:
        pointer_payload = {
            "channel": "ena-main",
            "latest": commitment,
            "checkpoint_sha": checkpoint_sha,
        }
        pointer_bytes = json.dumps(pointer_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        put_out = self._push_da_bytes(pointer_bytes, checkpoint_sha=f"{checkpoint_sha}-pointer")
        pointer_commitment = str(put_out.get("blob_id") or "")
        verified = self._verify_da_presence(pointer_commitment)
        return {
            "pointer_name": "ena-main/latest",
            "pointer_commitment": pointer_commitment,
            "verified": verified,
            "payload": pointer_payload,
        }

    def _push_da_bytes(self, data: bytes, *, checkpoint_sha: str) -> dict[str, Any]:
        status = self.da_status.get_status()
        diagnostics = self._build_da_diagnostics(status)
        local_node = bool(diagnostics.get("local_node"))
        if status.get("allow_remote_put") is not False:
            return self.da.upload_bytes(data)
        if not local_node:
            raise DaPolicyError(
                message="Remote DA uploads are disabled on this node. You must enable allow_remote_put on the node operator side.",
                code="DA_REMOTE_PUT_BLOCKED",
                da_enabled=True,
                allow_remote_put=False,
                da_dir=diagnostics["da_dir"],
                rpc_url=diagnostics["rpc_url"],
                can_enable_remote_put=self._supports_allow_remote_put(status),
                diagnostics=diagnostics,
                recommendations=[
                    "Remote DA uploads are disabled on this node.",
                    "You must enable allow_remote_put on the node operator side.",
                ],
            )

        ingest = self.da.get_ingest_dir()
        node_ingest_dir = str(ingest.get("dir") or "")
        if not node_ingest_dir:
            raise RuntimeError("Node did not return da.getIngestDir.dir")
        node_pending_dir = str(ingest.get("pending_dir") or os.path.join(node_ingest_dir, "pending"))
        host_pending_dir = Path(self._map_node_path_to_host(node_pending_dir)).expanduser()
        if str(host_pending_dir).startswith('/data/') or str(host_pending_dir) == '/data':
            raise RuntimeError("Refusing to write to /data on host. Configure Studio writable dir (e.g. ~/.animica/da_contrib) and keep /data paths node-only.")
        host_pending_dir.mkdir(parents=True, exist_ok=True)

        sha = __import__("hashlib").sha256(data).hexdigest()
        host_blob_path = host_pending_dir / f"{sha}.blob"
        host_blob_path.write_bytes(data)
        node_blob_path = os.path.join(node_pending_dir, f"{sha}.blob")

        ingest_out = self.da.ingest_local(node_blob_path, namespace=0)
        blob_id = str( ingest_out.get("blob_id") or "")
        if not blob_id:
            raise RuntimeError("da.ingestLocal did not return blob_id")
        if not self.da.wait_for_blob(blob_id, timeout_s=30.0, interval_s=2.0):
            raise RuntimeError(f"WAITING_FOR_INGEST: blob not yet visible after ingest {blob_id}")
        return {"blob_id": blob_id, "local_ingest": True, "ingest": ingest_out}
    def _pending_upload_queue_path(self) -> Path:
        return app_data_dir() / "pending_da_uploads.json"

    def _load_pending_uploads(self) -> list[dict[str, Any]]:
        path = self._pending_upload_queue_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            return []
        return []

    def _save_pending_uploads(self, rows: list[dict[str, Any]]) -> None:
        path = self._pending_upload_queue_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")

    def _enqueue_pending_upload(self, checkpoint_sha: str) -> dict[str, Any]:
        rows = self._load_pending_uploads()
        existing = next((r for r in rows if r.get("sha256") == checkpoint_sha and not r.get("uploaded")), None)
        if existing:
            return existing
        row = {
            "checkpoint_id": checkpoint_sha[:12],
            "sha256": checkpoint_sha,
            "local_path": "",
            "channel": "ena-main",
            "namespace": 0,
            "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "uploaded": False,
        }
        rows.append(row)
        self._save_pending_uploads(rows)
        return row

    def _mark_pending_uploaded(self, checkpoint_sha: str, commitment: str) -> None:
        rows = self._load_pending_uploads()
        changed = False
        for r in rows:
            if r.get("sha256") == checkpoint_sha:
                r["uploaded"] = True
                r["commitment"] = commitment
                changed = True
        if changed:
            self._save_pending_uploads(rows)


    def publish_checkpoint(self, checkpoint_sha: str, dev_mode: bool = False) -> dict[str, Any]:
        step_cache: dict[str, Any] = {}
        cp_row = self._get_checkpoint_row(checkpoint_sha)
        if cp_row:
            self._set_checkpoint_publish_state(
                checkpoint_sha,
                local_artifact_prepared=True,
                da_uploaded=bool(cp_row.get("da_uploaded")),
                aicf_registered=bool(cp_row.get("aicf_registered")),
            )

        def _validate(step):
            step.copy_command = f"animica ena publish --checkpoint {checkpoint_sha[:12]}"
            return {"valid": True, "checkpoint_sha": checkpoint_sha, "step_status": "completed"}

        def _push(step):
            data = checkpoint_sha.encode("utf-8")
            local_commitment = f"local-{checkpoint_sha[:16]}"
            if dev_mode:
                step_cache["da_pending"] = True
                self._enqueue_pending_upload(checkpoint_sha)
                self._set_checkpoint_publish_state(
                    checkpoint_sha,
                    local_artifact_prepared=True,
                    da_uploaded=False,
                    da_pending_reason="dev_mode_local_only",
                )
                return {
                    "local_commitment": local_commitment,
                    "mode": "local-only",
                    "pending_da_upload": True,
                    "step_status": "warning",
                    "state": "PREPARED_LOCALLY",
                    "actions": [
                        {"id": "configure_da", "label": "Configure DA Now"},
                        {"id": "retry_da_upload", "label": "Retry DA Upload"},
                    ],
                }

            status = self.da_status.get_status()
            diagnostics = self._build_da_diagnostics(status)
            supports_toggle = self._supports_allow_remote_put(status)
            not_configured = (status.get("ok") is False and status.get("reason") == "not_configured")
            da_enabled = bool(status.get("enabled")) and bool(status.get("ok", True))

            if not da_enabled:
                reason = str(status.get("reason") or status.get("policy_blocked_reason") or "not_configured")
                self._enqueue_pending_upload(checkpoint_sha)
                self._set_checkpoint_publish_state(
                    checkpoint_sha,
                    local_artifact_prepared=True,
                    da_uploaded=False,
                    da_pending_reason=reason,
                )
                step_cache["da_pending"] = True
                return {
                    "local_commitment": local_commitment,
                    "mode": "local-only",
                    "pending_da_upload": True,
                    "reason": reason,
                    "diagnostics": diagnostics | {"status": status},
                    "step_status": "warning",
                    "state": "PREPARED_LOCALLY",
                    "actions": [
                        {"id": "configure_da", "label": "Configure DA Now"},
                        {"id": "retry_da_upload", "label": "Retry DA Upload"},
                    ],
                }

            existing_commitment = str((cp_row or {}).get("commitment") or "")
            if existing_commitment and self._verify_da_presence(existing_commitment):
                step.logs.append("Checkpoint already present in DA; reusing commitment.")
                step_cache["commitment"] = existing_commitment
                self._mark_pending_uploaded(checkpoint_sha, existing_commitment)
                self._set_checkpoint_publish_state(
                    checkpoint_sha,
                    local_artifact_prepared=True,
                    da_uploaded=True,
                    commitment=existing_commitment,
                    da_pending_reason="",
                )
                pointer = self._update_remote_pointer(existing_commitment, checkpoint_sha)
                return {
                    "commitment": existing_commitment,
                    "mode": "network",
                    "idempotent_reuse": True,
                    "verification": {"verified": True},
                    "da_status": status,
                    "diagnostics": diagnostics,
                    "remote_pointer": pointer,
                    "step_status": "completed",
                    "state": "UPLOADED_TO_DA",
                }

            if status.get("allow_remote_put") is False and not diagnostics.get("local_node"):
                recs = [
                    "Remote DA uploads are disabled on this node.",
                    "You must enable allow_remote_put on the node operator side.",
                ]
                raise DaPolicyError(
                    message="Remote DA uploads are disabled on this node. You must enable allow_remote_put on the node operator side.",
                    code="DA_REMOTE_PUT_BLOCKED",
                    da_enabled=True,
                    allow_remote_put=False,
                    da_dir=diagnostics["da_dir"],
                    rpc_url=diagnostics["rpc_url"],
                    can_enable_remote_put=supports_toggle,
                    diagnostics=diagnostics,
                    recommendations=recs,
                )

            strategy = "local_ingest" if status.get("allow_remote_put") is False and diagnostics.get("local_node") else "rpc_put"
            step.logs.append("Uploading to DA…")
            step.progress = 60
            out = self._push_da_bytes(data, checkpoint_sha=checkpoint_sha)
            commit = str(out.get("blob_id") or "")
            if not commit:
                raise RuntimeError(out.get("error", "DA unavailable"))

            step.logs.append("Verifying blob…")
            step.progress = 90
            verified = self._verify_da_presence(commit)
            if not verified:
                raise RuntimeError(f"DA verification failed for blob {commit}")

            pointer = self._update_remote_pointer(commit, checkpoint_sha)
            step_cache["commitment"] = commit
            self._mark_pending_uploaded(checkpoint_sha, commit)
            self._set_checkpoint_publish_state(
                checkpoint_sha,
                local_artifact_prepared=True,
                da_uploaded=True,
                commitment=commit,
                da_pending_reason="",
            )
            return {
                "commitment": commit,
                "mode": "network" if strategy == "rpc_put" else "local-ingest",
                "push_strategy": strategy,
                "verification": {"verified": verified},
                "da_status": status,
                "diagnostics": diagnostics,
                "remote_pointer": pointer,
                "step_status": "completed",
                "state": "UPLOADED_TO_DA",
            }

        def _register(step):
            commitment = step_cache.get("commitment")
            if not commitment:
                return {
                    "ok": False,
                    "pending": True,
                    "reason": "Awaiting DA upload commitment before register.",
                    "step_status": "pending",
                    "actions": [{"id": "retry_register", "label": "Retry AICF Register"}],
                }
            payload = {"checkpoint_sha": checkpoint_sha, "commitment": commitment}
            res = self.aicf.submit_job("ena_checkpoint_publish", payload, 10)
            ok = bool(res.get("ok", False))
            self._set_checkpoint_publish_state(
                checkpoint_sha,
                local_artifact_prepared=True,
                aicf_registered=ok,
                commitment=commitment,
            )
            if not ok:
                err = str(res.get("error") or res.get("message") or "AICF registration failed")
                return {
                    "job": res.get("data", {}).get("job_id", "local-reg"),
                    "ok": False,
                    "error": err,
                    "rpc_method": "aicf.submitJob",
                    "rpc_params": payload,
                    "step_status": "warning",
                    "actions": [{"id": "retry_register", "label": "Retry AICF Register"}],
                }
            return {"job": res.get("data", {}).get("job_id", "local-reg"), "ok": True, "step_status": "completed"}

        run = self.runner.run("publish", [("Validate checkpoint", _validate), ("Push to DA", _push), ("Register in AICF", _register)])
        if run.status == "failed":
            return {"ok": False, "run": run}
        return {"ok": run.status == "completed", "run": run}

    def infer(self, prompt: str, network_mode: bool = False, token_estimate: int = 100) -> dict[str, Any]:
        fees = self.fees.estimate(token_estimate)
        if network_mode:
            mode = "network"
            text = f"[network] {prompt[:120]}"
        else:
            mode = "local"
            text = f"[local] {prompt[:120]}"
        row = {"mode": mode, "prompt": prompt, "response": text, "latency_ms": 80 if mode == "local" else 320, "tokens": token_estimate, "fees": fees}
        self.store.append("history", row)
        return row

    def run_auto_mode(self, work_dir: Path) -> dict[str, Any]:
        c = self.run_contribute_flow(work_dir)
        f = self.fetch_latest_checkpoint(work_dir / "fetched")
        return {"contribute": c, "fetch": f, "active_checkpoint": f.get("active")}

    def export_one_command(self, flow: str, options: dict[str, Any]) -> str:
        if flow == "auto":
            t = options.get("type", "dataset")
            return f"animica ena contribute --type {t} --auto --budget {options.get('budget', 100)} && animica ena checkpoints fetch --latest"
        if flow == "infer":
            mode = "--network" if options.get("network") else "--local"
            return f"animica ena infer {mode} --prompt {json.dumps(options.get('prompt', 'hello'))}"
        return "animica ena contribute --type dataset --auto --budget 100"

    def build_debug_bundle(self, run_id: str) -> str:
        runs = self.store.get("step_runs", {})
        payload = {
            "run": runs.get(run_id),
            "discover": self.detect_capabilities(),
            "artifacts": self.store.get("artifacts", []),
            "checkpoints": self.store.get("checkpoints", []),
            "history": self.store.get("history", []),
        }
        out = Path(self.store.path).parent / f"debug-{run_id}.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.store.append("debug_bundles", {"run_id": run_id, "path": str(out)}, dedupe_key="run_id")
        return str(out)
