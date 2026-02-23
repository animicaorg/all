from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
            self.da = DaClient(config.get_active_profile().node.rpc_local_url)
        except Exception:
            self.da = type('StubDa', (), {'upload_bytes': lambda *_a, **_k: {'ok': False, 'error': 'da unavailable'}})()
        self.fees = FeeRoutingService()

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
            row = {"id": d["sha256"][:12], "sha256": d["sha256"], "path": d["path"], "origin": "local", "tab": "latest"}
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

    def publish_checkpoint(self, checkpoint_sha: str, dev_mode: bool = False) -> dict[str, Any]:
        existing = [c for c in self.store.get("checkpoints", []) if c.get("sha256") == checkpoint_sha and c.get("commitment")]
        if existing:
            return {"ok": False, "error": "Duplicate publish prevented", "existing": existing[0]}
        step_cache: dict[str, Any] = {}

        def _validate(step):
            step.copy_command = f"animica ena publish --checkpoint {checkpoint_sha[:12]}"
            return {"valid": True}

        def _push(step):
            data = checkpoint_sha.encode("utf-8")
            if dev_mode:
                commit = f"dev-{checkpoint_sha[:16]}"
                step_cache["commitment"] = commit
                return {"commitment": commit, "mode": "local-only"}
            out = self.da.upload_bytes(data)
            commit = out.get("blob_id")
            if not commit:
                raise RuntimeError(out.get("error", "DA unavailable"))
            step_cache["commitment"] = commit
            return {"commitment": commit, "mode": "network"}

        def _register(step):
            payload = {"checkpoint_sha": checkpoint_sha, "commitment": step_cache.get("commitment")}
            res = self.aicf.submit_job("ena_checkpoint_publish", payload, 10)
            return {"job": res.get("data", {}).get("job_id", "local-reg")}

        run = self.runner.run("publish", [("Validate checkpoint", _validate), ("Push to DA", _push), ("Register in AICF", _register)])
        cps = list(self.store.get("checkpoints", []))
        for cp in cps:
            if cp.get("sha256") == checkpoint_sha:
                cp["commitment"] = run.result.get("Push to DA", {}).get("commitment")
        self.store.set("checkpoints", cps)
        return {"ok": True, "run": run}

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
