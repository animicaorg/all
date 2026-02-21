from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from animica.cli.rpc import call_rpc


@dataclass
class DaPutResult:
    commitment: str


class DevDaAdapter:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_blob(self, blob: bytes) -> DaPutResult:
        commitment = hashlib.sha256(blob).hexdigest()
        (self.root / commitment).write_bytes(blob)
        return DaPutResult(commitment=commitment)

    def get_blob(self, commitment: str) -> bytes:
        return (self.root / commitment).read_bytes()


class RealDaAdapter:
    def __init__(self, rpc_url: str, rpc_trace: list[dict[str, Any]]):
        self.rpc_url = rpc_url
        self.rpc_trace = rpc_trace

    def _rpc(self, method: str, params: list[Any]) -> Any:
        self.rpc_trace.append({"method": method, "params": params})
        return call_rpc(method, params, rpc_url=self.rpc_url, allow_remote=True)

    def put_blob(self, blob: bytes) -> DaPutResult:
        blob_hex = "0x" + blob.hex()
        for method in ("da_putBlob", "da.putBlob"):
            try:
                out = self._rpc(method, [blob_hex])
                if isinstance(out, dict):
                    commitment = out.get("commitment") or out.get("id") or out.get("hash")
                else:
                    commitment = str(out)
                if commitment:
                    return DaPutResult(commitment=str(commitment))
            except Exception:
                continue
        raise RuntimeError("DA put failed: neither da_putBlob nor da.putBlob accepted")

    def get_blob(self, commitment: str) -> bytes:
        for method in ("da_getBlob", "da.getBlob"):
            try:
                out = self._rpc(method, [commitment])
                if isinstance(out, dict):
                    data = out.get("blob") or out.get("data")
                else:
                    data = out
                if isinstance(data, str) and data.startswith("0x"):
                    return bytes.fromhex(data[2:])
                if isinstance(data, str):
                    return base64.b64decode(data)
            except Exception:
                continue
        raise RuntimeError("DA get failed: neither da_getBlob nor da.getBlob accepted")


class DevAicfAdapter:
    def __init__(self, aicf_share_bp: int = 500, relayer_share_bp: int = 100):
        self.aicf_share_bp = aicf_share_bp
        self.relayer_share_bp = relayer_share_bp
        self.balances: dict[str, int] = {"payer": 1_000_000}
        self.aicf_credits = 0
        self.relayer_credits = 0
        self.fees_collected = 0

    def route_fee(self, fee: int, payer: str = "payer") -> dict[str, int]:
        if self.balances.get(payer, 0) < fee:
            raise RuntimeError("insufficient payer balance")
        self.balances[payer] -= fee
        aicf_cut = (fee * self.aicf_share_bp) // 10_000
        relayer_cut = (fee * self.relayer_share_bp) // 10_000
        operator_cut = fee - aicf_cut - relayer_cut
        self.aicf_credits += aicf_cut
        self.relayer_credits += relayer_cut
        self.fees_collected += fee
        return {"charged": fee, "aicf": aicf_cut, "relayer": relayer_cut, "operator": operator_cut}

    def apply_block_reward_slice(self, reward: int, aicf_bp: int = 500) -> int:
        minted = (reward * aicf_bp) // 10_000
        self.aicf_credits += minted
        return minted


def _deterministic_dataset(seed: int, samples: int = 8, dim: int = 4) -> tuple[list[list[float]], list[float]]:
    import random

    rnd = random.Random(seed)
    xs: list[list[float]] = []
    ys: list[float] = []
    for _ in range(samples):
        row = [rnd.uniform(-1.0, 1.0) for _ in range(dim)]
        y = 0.3 * row[0] - 0.2 * row[1] + 0.1
        xs.append(row)
        ys.append(y)
    return xs, ys


def _train_toy_model(seed: int, steps: int = 3, lr: float = 0.05) -> dict[str, Any]:
    import random

    rnd = random.Random(seed)
    w = [rnd.uniform(-0.1, 0.1) for _ in range(4)]
    b = rnd.uniform(-0.1, 0.1)
    xs, ys = _deterministic_dataset(seed=seed)
    losses: list[float] = []
    for _ in range(steps):
        dw = [0.0] * 4
        db = 0.0
        loss = 0.0
        for x, y in zip(xs, ys):
            pred = sum(i * j for i, j in zip(x, w)) + b
            err = pred - y
            loss += err * err
            for i in range(4):
                dw[i] += 2 * err * x[i] / len(xs)
            db += 2 * err / len(xs)
        w = [wi - lr * gi for wi, gi in zip(w, dw)]
        b -= lr * db
        losses.append(loss / len(xs))
    return {"weights": w, "bias": b, "losses": losses, "tokenizer": {"vocab": ["<pad>", "hello", "ena"]}}


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pack_snapshot(snapshot_dir: Path, model_data: dict[str, Any], manifest_base: dict[str, Any]) -> dict[str, Any]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    weights_bytes = _canonical_json_bytes({"weights": model_data["weights"], "bias": model_data["bias"]})
    tokenizer_bytes = _canonical_json_bytes(model_data["tokenizer"])
    weights_hash = hashlib.sha256(weights_bytes).hexdigest()
    tokenizer_hash = hashlib.sha256(tokenizer_bytes).hexdigest()

    (snapshot_dir / "weights.bin").write_bytes(weights_bytes)
    (snapshot_dir / "tokenizer.json").write_bytes(tokenizer_bytes)

    manifest = dict(manifest_base)
    manifest.update({"weights_hash": weights_hash, "tokenizer_hash": tokenizer_hash})

    preimage = {
        "model_name": manifest["model_name"],
        "model_version": manifest["model_version"],
        "chain_id": manifest["chain_id"],
        "block_height": manifest["block_height"],
        "commit_hash": manifest["commit_hash"],
        "params_summary": manifest["params_summary"],
        "tokenizer_hash": tokenizer_hash,
        "weights_hash": weights_hash,
        "determinism": manifest["determinism"],
    }
    full_hash = hashlib.sha256(_canonical_json_bytes(preimage)).hexdigest()
    manifest["full_snapshot_hash"] = full_hash

    (snapshot_dir / "manifest.json").write_bytes(_canonical_json_bytes(manifest))
    return manifest


def _load_snapshot(snapshot_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    weights = json.loads((snapshot_dir / "weights.bin").read_bytes())
    return manifest, weights


def _discover_da(rpc_url: str, rpc_trace: list[dict[str, Any]]) -> bool:
    for method in ("rpc.discover", "rpc_discover"):
        try:
            rpc_trace.append({"method": method, "params": []})
            out = call_rpc(method, [], rpc_url=rpc_url, allow_remote=True)
            text = json.dumps(out).lower()
            if "da_putblob" in text or "da.putblob" in text:
                return True
        except Exception:
            continue
    return False


def _write_debug_bundle(work_dir: Path, report: dict[str, Any], exc: Exception) -> Path:
    bundle = work_dir / "ena_smoke_debug_bundle.zip"
    env = {
        "python": os.sys.version,
        "platform": platform.platform(),
        "threads": {"OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"), "MKL_NUM_THREADS": os.getenv("MKL_NUM_THREADS")},
    }
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("manifest.json", "manifest_2.json", "rpc_trace.json", "report.json"):
            p = work_dir / name
            if p.exists():
                zf.write(p, name)
        zf.writestr("error.txt", f"{type(exc).__name__}: {exc}\n")
        zf.writestr("env.json", json.dumps(env, indent=2))
    return bundle


def run_ena_smoke_test(work_dir: Path | None = None, rpc_url: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    wd = work_dir or Path(tempfile.mkdtemp(prefix="ena-smoke-"))
    wd.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

    rpc_url = rpc_url or os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    rpc_trace: list[dict[str, Any]] = []
    report: dict[str, Any] = {"ok": False, "work_dir": str(wd), "rpc_url": rpc_url, "steps": []}

    try:
        model = _train_toy_model(seed=7, steps=3)
        report["steps"].append("train")

        manifest_base = {
            "model_name": "ena-toy",
            "model_version": "0.0.1",
            "chain_id": 31337,
            "block_height": 0,
            "commit_hash": "dev-smoke-commit",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "params_summary": {"arch": "tiny-linear", "dim": 4, "steps": 3},
            "da_commitment": None,
            "determinism": {"seed": 7, "omp_threads": 1, "mkl_threads": 1},
        }
        snap1 = wd / "snapshot"
        manifest = _pack_snapshot(snap1, model, manifest_base)
        (wd / "manifest.json").write_text(json.dumps(manifest, indent=2))

        snap2 = wd / "snapshot_2"
        manifest2 = _pack_snapshot(snap2, model, manifest_base)
        (wd / "manifest_2.json").write_text(json.dumps(manifest2, indent=2))
        assert manifest["full_snapshot_hash"] == manifest2["full_snapshot_hash"], "snapshot hash must be stable across consecutive packs"
        report["steps"].append("pack")

        snapshot_blob = (snap1 / "manifest.json").read_bytes() + (snap1 / "weights.bin").read_bytes() + (snap1 / "tokenizer.json").read_bytes()
        if _discover_da(rpc_url, rpc_trace):
            da = RealDaAdapter(rpc_url, rpc_trace)
            da_mode = "rpc"
        else:
            da = DevDaAdapter(wd / "da_stub")
            da_mode = "dev_stub"
        put = da.put_blob(snapshot_blob)
        refetched = da.get_blob(put.commitment)
        assert refetched == snapshot_blob, "DA bytes mismatch"
        assert hashlib.sha256(refetched).hexdigest() == hashlib.sha256(snapshot_blob).hexdigest(), "DA hash mismatch"
        manifest["da_commitment"] = put.commitment
        report["steps"].append("da_roundtrip")

        loaded_manifest, loaded_weights = _load_snapshot(snap1)
        pred = sum(v * w for v, w in zip([0.1, -0.2, 0.3, -0.4], loaded_weights["weights"])) + loaded_weights["bias"]
        assert isinstance(pred, float)
        report["inference"] = {"output": pred}
        report["steps"].append("inference")

        aicf = DevAicfAdapter()
        min_fee = 1
        raw_tx_hex = "0x" + b"ena-smoke".hex()
        rpc_req = {"method": "tx_sendRawTransaction", "params": [raw_tx_hex]}
        assert isinstance(rpc_req["params"], list) and len(rpc_req["params"]) == 1 and isinstance(rpc_req["params"][0], str)
        rpc_trace.append(rpc_req)
        fee_result = aicf.route_fee(min_fee)
        assert fee_result["charged"] == min_fee
        assert fee_result["aicf"] >= 0
        assert fee_result["relayer"] >= 0
        report["fee"] = fee_result

        before = aicf.aicf_credits
        minted = aicf.apply_block_reward_slice(10_000)
        after = aicf.aicf_credits
        assert after >= before
        report["aicf"] = {"before_reward_slice": before, "minted": minted, "after_reward_slice": after}
        report["steps"].append("fees_and_credits")

        required_fields = {
            "model_name", "model_version", "chain_id", "block_height", "commit_hash", "created_at", "params_summary",
            "tokenizer_hash", "weights_hash", "full_snapshot_hash", "da_commitment", "determinism",
        }
        missing = sorted(required_fields - set(manifest.keys()))
        assert not missing, f"manifest missing required fields: {missing}"

        report.update(
            {
                "ok": True,
                "da_mode": da_mode,
                "hashes": {
                    "full_snapshot_hash": manifest["full_snapshot_hash"],
                    "weights_hash": manifest["weights_hash"],
                    "tokenizer_hash": manifest["tokenizer_hash"],
                    "da_commitment": put.commitment,
                },
                "timings": {"total_seconds": round(time.perf_counter() - started, 3)},
            }
        )
        (wd / "rpc_trace.json").write_text(json.dumps(rpc_trace, indent=2))
        (wd / "report.json").write_text(json.dumps(report, indent=2))
        return report
    except Exception as exc:
        report["error"] = str(exc)
        (wd / "rpc_trace.json").write_text(json.dumps(rpc_trace, indent=2))
        (wd / "report.json").write_text(json.dumps(report, indent=2))
        bundle = _write_debug_bundle(wd, report, exc)
        report["debug_bundle"] = str(bundle)
        raise
