"""
animica.ena.pool
================

Collaborative **training pools**: many contributors fund and train *one* model
together in rounds, the promoted checkpoint is served for inference while the
next round trains, and rewards are split proportionally across roles. See
``docs/ena/training-pool.md`` for the full design.

Roles that earn from a pool:

* **funders** pay ANM (``fund_quote`` → wallet pays with ``memo = pool_hash`` →
  ``fund_confirm``); weight ∝ ANM contributed.
* **trainers** ``claim_shard`` a deterministic dataset slice, train it, and
  ``submit_shard`` a checkpoint + metrics; weight ∝ verified work (gpu_hours,
  falling back to samples).
* **servers** (Phase 2) serve the promoted checkpoint; weight ∝ tokens served.

``aggregate`` merges the round's submitted adapters (weighted by work), runs an
optional **eval gate**, and on success promotes the result to the pool's
``served_checkpoint`` and advances the round. ``payout`` splits the round budget
by ``reward_split`` (bps) and then proportionally within each role by weight.

The core imports stdlib only; torch/transformers/safetensors are imported lazily
inside the merge helper so ``import animica.ena.pool`` works on a CPU-only box.
"""

from __future__ import annotations

import hashlib
import json as _json
import threading
from pathlib import Path
from typing import Any, Optional

from . import datasets as ds
from . import payments as pay
from . import training
from .errors import PoolError
from .models import (
    Pool, PoolContribution, PoolShard,
    canonical_json, hash_obj, new_uuid, now_ts, sha3_hex,
)

# Pool lifecycle.
POOL_STATUS_OPEN = "open"
POOL_STATUS_TRAINING = "training"
POOL_STATUS_PAUSED = "paused"
POOL_STATUS_CLOSED = "closed"

# Shard lifecycle (mirrors the useful-work job lifecycle).
SHARD_OPEN = "open"
SHARD_CLAIMED = "claimed"
SHARD_SUBMITTED = "submitted"
SHARD_VERIFIED = "verified"
SHARD_REJECTED = "rejected"

ROLES = ("funders", "trainers", "servers")
_ROLE_SINGULAR = {"funders": "funder", "trainers": "trainer", "servers": "server"}
_REWARD_SPLIT_TOTAL = 10000  # basis points

DEFAULT_REWARD_SPLIT = {"funders": 2000, "trainers": 6000, "servers": 2000}


# ---------------------------------------------------------------------------
# pure helpers (deterministic; no I/O)
# ---------------------------------------------------------------------------

def _validate_reward_split(split: dict[str, int]) -> None:
    total = sum(int(split.get(r, 0)) for r in ROLES)
    if total != _REWARD_SPLIT_TOTAL:
        raise PoolError(
            f"reward_split must sum to {_REWARD_SPLIT_TOTAL} bps, got {total}",
            hint="keys: funders, trainers, servers")


def _shard_bucket(rec: dict[str, Any], n: int) -> int:
    """Stable bucket for a record (same hashing as datasets.split)."""
    h = hashlib.sha3_256(_json.dumps(rec, sort_keys=True).encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % n


def _work_weight(receipt: dict[str, Any], metrics: dict[str, Any]) -> float:
    """Verified work weight for a trainer submission: gpu_hours, then samples."""
    gh = float(receipt.get("gpu_hours", 0) or 0)
    if gh > 0:
        return gh
    samples = float(metrics.get("samples", metrics.get("samples_processed", 0)) or 0)
    return samples if samples > 0 else 1.0


def _split_proportional(total: int, weighted: list[tuple[str, float]]) -> dict[str, int]:
    """Split integer ``total`` across ``(key, weight)`` pairs proportional to weight.

    Integer-safe: floor each allocation, then hand the leftover units to the
    largest fractional remainders (ties broken by weight, then key) so the parts
    always sum back to ``total``. Mirrors the XMR pool payout split.
    """
    pairs = [(k, float(w)) for k, w in weighted if w and w > 0]
    if total <= 0 or not pairs:
        return {}
    wsum = sum(w for _, w in pairs)
    raw = {k: total * w / wsum for k, w in pairs}
    floor = {k: int(v) for k, v in raw.items()}
    remainder = total - sum(floor.values())
    order = sorted(pairs, key=lambda kw: (-(raw[kw[0]] - floor[kw[0]]), -kw[1], kw[0]))
    i = 0
    while remainder > 0 and order:
        floor[order[i % len(order)][0]] += 1
        remainder -= 1
        i += 1
    return floor


def _checkpoint_digest(checkpoint_path: Optional[str], shard: dict[str, Any]) -> str:
    """SHA3 of the checkpoint weights if present, else a deterministic anchor."""
    if checkpoint_path:
        p = Path(checkpoint_path)
        files: list[Path] = []
        if p.is_dir():
            for marker in ("adapter_model.safetensors", "model.safetensors",
                           "pytorch_model.bin"):
                if (p / marker).is_file():
                    files.append(p / marker)
            if not files:
                files = sorted(p.glob("*.safetensors"))[:1]
        elif p.is_file():
            files = [p]
        if files:
            h = hashlib.sha3_256()
            for f in files:
                try:
                    with f.open("rb") as fh:
                        for block in iter(lambda: fh.read(1 << 20), b""):
                            h.update(block)
                except OSError:
                    continue
            return h.hexdigest()
    return sha3_hex(canonical_json({
        "shard_id": shard["shard_id"], "path": checkpoint_path or "",
        "sha256": shard["sha256"]}))


def _try_merge_adapters(submitted: list[dict[str, Any]], out: Path,
                        weights: dict[str, float]) -> Optional[dict[str, Any]]:
    """Real weighted average of LoRA adapter tensors (lazy torch/safetensors)."""
    try:  # pragma: no cover - exercised only with the gpu extra + real weights
        import torch  # type: ignore  # noqa: F401
        from safetensors.torch import load_file, save_file  # type: ignore
    except Exception:
        return None
    files: dict[str, Path] = {}
    for s in submitted:
        cp = s.get("checkpoint_path")
        if not cp:
            return None
        p = Path(cp)
        f = (p / "adapter_model.safetensors") if p.is_dir() else p
        if not f.is_file():
            return None
        files[s["shard_id"]] = f
    acc: dict[str, Any] = {}
    for sid, f in files.items():
        w = float(weights.get(sid, 0.0))
        tensors = load_file(str(f))
        for k, v in tensors.items():
            fv = v.float() * w
            acc[k] = fv if k not in acc else acc[k] + fv
    if not acc:
        return None
    merged_path = out / "adapter_model.safetensors"
    save_file(acc, str(merged_path))
    h = hashlib.sha3_256()
    with merged_path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return {"path": str(out), "hash": h.hexdigest(), "merged": True}


def merge_checkpoints(submitted: list[dict[str, Any]], out_dir: str | Path,
                      weights: dict[str, float]) -> dict[str, Any]:
    """Merge a round's submitted checkpoints.

    Produces a real weighted-average adapter when torch+safetensors and the
    actual weights are present; otherwise writes a deterministic merge plan
    (used by CPU/command-backend pools and tests) and hashes that.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    real = _try_merge_adapters(submitted, out, weights)
    if real:
        return real
    plan_doc = {
        "schema": "ena.pool.merge.v1",
        "weights": weights,
        "checkpoints": {s["shard_id"]: s.get("checkpoint_path") for s in submitted},
    }
    plan_path = out / "merge-plan.json"
    plan_path.write_text(canonical_json(plan_doc), encoding="utf-8")
    return {"path": str(plan_path), "hash": hash_obj(plan_doc), "merged": False}


# ---------------------------------------------------------------------------
# PoolService
# ---------------------------------------------------------------------------

class PoolService:
    """Coordinator for collaborative training pools (backed by the ENA store)."""

    def __init__(self, cfg, store, jobs=None, demand=None) -> None:
        self.cfg = cfg
        self.store = store
        self.jobs = jobs
        self.demand = demand
        # Serializes compound state transitions (create / shard-gen / fund /
        # submit / aggregate / payout) so the threaded HTTP server can't race
        # check-then-act sequences. The store has its own row lock; this guards
        # the higher-level invariants.
        self._lock = threading.RLock()

    # -- internals --------------------------------------------------------
    def _pool_dir(self, pool_id: str) -> Path:
        return self.cfg.artifacts_dir() / "pools" / pool_id

    def get(self, pool_id: str) -> dict[str, Any]:
        pool = self.store.get_pool(pool_id)
        if not pool:
            raise PoolError(f"pool not found: {pool_id}")
        return pool

    # -- create -----------------------------------------------------------
    def create(self, base_model: str, dataset: str, *, method: str = "lora",
               name: Optional[str] = None, num_shards: int = 4,
               hyperparameters: Optional[dict[str, Any]] = None,
               reward_split: Optional[dict[str, int]] = None,
               eval_gate: Optional[dict[str, Any]] = None,
               requester: Optional[str] = None,
               model_id: Optional[str] = None) -> dict[str, Any]:
        if method not in training.METHODS:
            raise PoolError(f"unknown method: {method}",
                            hint=f"one of: {', '.join(training.METHODS)}")
        src = Path(dataset)
        if not src.is_file():
            raise PoolError(f"dataset not found: {src}")
        num_shards = int(num_shards)
        if num_shards < 1:
            raise PoolError("num_shards must be >= 1")
        reward_split = dict(reward_split or DEFAULT_REWARD_SPLIT)
        _validate_reward_split(reward_split)

        sha = ds.sha256_file(src)
        rows = ds.row_count(src)
        name = name or src.stem
        spec = {"base_model": base_model, "dataset_sha256": sha,
                "method": method, "name": name}
        pool_id = "enapool-" + hash_obj(spec)[:32]
        pool_hash = sha3_hex(canonical_json(
            {**spec, "pool_id": pool_id, "pool_hash": ""}))
        hp = hyperparameters or training._default_hparams(method)
        hp.setdefault("method", method)
        pool = Pool(
            pool_id=pool_id, pool_hash=pool_hash, name=name,
            status=POOL_STATUS_OPEN, base_model=base_model, method=method,
            dataset_id="ds-" + sha[:16], dataset_path=str(src.resolve()),
            dataset_sha256=sha, dataset_rows=rows, num_shards=num_shards,
            round=1, hyperparameters=hp, reward_split=reward_split,
            eval_gate=eval_gate, treasury_address=self.cfg.treasury_address,
            requester=requester, model_id=model_id,
        ).to_dict()
        if model_id:
            self.ensure_global_model(model_id, base_model)
        # atomic check-then-create under the service lock
        with self._lock:
            if self.store.get_pool(pool_id):
                raise PoolError(
                    f"pool already exists: {pool_id}",
                    hint="same base_model/dataset/method/name already has a pool")
            self.store.upsert_pool(pool)
        return pool

    def list_pools(self, status: Optional[str] = None,
                   limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_pools(status=status, limit=limit)

    # -- funding (multi-contributor; reuses the demand/payment rails) ------
    def _require_funding(self) -> None:
        if not self.cfg.demand_enabled():
            raise PoolError("pool funding requires a treasury",
                            hint="set ENA_TREASURY_ADDRESS on the coordinator")

    def fund_quote(self, pool_id: str, reward_anm: float,
                   requester: Optional[str] = None) -> dict[str, Any]:
        pool = self.get(pool_id)
        self._require_funding()
        reward_anm = float(reward_anm)
        if reward_anm < self.cfg.demand_min_anm:
            raise PoolError(
                f"reward {reward_anm} below minimum {self.cfg.demand_min_anm} ANM")
        required_nano = pay.anm_to_nano(reward_anm)
        return {
            "pool_id": pool_id, "pool_hash": pool["pool_hash"],
            "round": pool["round"],
            "treasury_address": self.cfg.treasury_address,
            "chain_id": self.cfg.chain_id,
            "required_nano": required_nano, "required_anm": reward_anm,
            "memo": pool["pool_hash"],
        }

    def fund_confirm(self, pool_id: str, txid: str,
                     requester: Optional[str] = None) -> dict[str, Any]:
        pool = self.get(pool_id)
        self._require_funding()
        txid = pay._norm_hex(txid)
        if self.store.payment_seen(txid):
            return {"pool_id": pool_id, "funded": False, "reason": "txid_already_used"}
        result = pay.verify_payment(
            pay.AnimicaRPC(self.cfg.rpc_url), txid,
            treasury_address=self.cfg.treasury_address,
            min_nano=pay.anm_to_nano(self.cfg.demand_min_anm),
            expect_memo=pool["pool_hash"],
            require_confirmed=self.cfg.payment_confirmations > 0,
            require_memo=self.cfg.payment_require_memo)
        if result["pending"]:
            return {"pool_id": pool_id, "funded": False, "pending": True,
                    "reason": result["reason"], "tx_status": result["status"]}
        if not result["ok"]:
            return {"pool_id": pool_id, "funded": False, "pending": False,
                    "reason": result["reason"], "tx_status": result["status"]}
        recorded = self.store.record_payment({
            "txid": txid, "job_id": pool_id, "value_nano": result["value_nano"],
            "from_addr": result["from_addr"], "status": "verified",
            "created_at": now_ts()})
        if not recorded:
            return {"pool_id": pool_id, "funded": False, "reason": "txid_already_used"}

        anm = pay.nano_to_anm(result["value_nano"])
        contrib = PoolContribution(
            contribution_id="ctr-" + new_uuid()[:16], pool_id=pool_id,
            round=pool["round"], role="funder",
            address=requester or result["from_addr"], weight=float(anm),
            amount_nano=int(result["value_nano"]), ref=txid,
        ).to_dict()
        self.store.add_contribution(contrib)
        pool["budget_nano"] = int(pool.get("budget_nano", 0)) + int(result["value_nano"])
        if pool["status"] == POOL_STATUS_OPEN:
            pool["status"] = POOL_STATUS_TRAINING
        pool["updated_at"] = now_ts()
        self.store.upsert_pool(pool)
        return {"pool_id": pool_id, "funded": True, "reward_anm": anm,
                "budget_nano": pool["budget_nano"],
                "contribution_id": contrib["contribution_id"],
                "from_addr": result["from_addr"]}

    # -- sharding + claim (trainer fan-out) -------------------------------
    def _ensure_shards(self, pool: dict[str, Any]) -> list[dict[str, Any]]:
        pool_id, rnd = pool["pool_id"], pool["round"]
        existing = self.store.list_shards(pool_id, round=rnd)
        if existing:
            return existing
        # serialize generation so concurrent claim_shard calls don't both
        # regenerate the whole dataset (double-check after taking the lock).
        with self._lock:
            existing = self.store.list_shards(pool_id, round=rnd)
            if existing:
                return existing
            n = int(pool["num_shards"])
            buckets: list[list[dict[str, Any]]] = [[] for _ in range(n)]
            for rec in ds.read_jsonl(pool["dataset_path"]):
                buckets[_shard_bucket(rec, n)].append(rec)
            out_dir = self._pool_dir(pool_id) / f"round-{rnd}"
            shards = []
            for i, recs in enumerate(buckets):
                path = out_dir / f"shard-{i}.jsonl"
                ds.write_jsonl(path, recs)
                shard = PoolShard(
                    shard_id=f"{pool_id}-r{rnd}-s{i}", pool_id=pool_id, round=rnd,
                    ordinal=i, total=n, path=str(path), row_count=len(recs),
                    sha256=ds.sha256_file(path), status=SHARD_OPEN,
                ).to_dict()
                self.store.upsert_shard(shard)
                shards.append(shard)
            return shards

    def _shard_manifest(self, pool: dict[str, Any],
                        shard: dict[str, Any]) -> dict[str, Any]:
        out_dir = str(self._pool_dir(pool["pool_id"]) / f"round-{shard['round']}" /
                      f"shard-{shard['ordinal']}-output")
        return {
            "run_name": f"{pool['name']}-r{shard['round']}-s{shard['ordinal']}",
            "backend": "python_transformers", "base_model": pool["base_model"],
            "output_dir": out_dir,
            "train": {"split": "train", "path": shard["path"],
                      "row_count": shard["row_count"], "sha256": shard["sha256"]},
            "hyperparameters": pool["hyperparameters"],
            "metadata": {"pool_id": pool["pool_id"], "shard_id": shard["shard_id"],
                         "round": shard["round"], "method": pool["method"],
                         "source_sha256": shard["sha256"]},
            "train_dataset": shard["path"], "train_sha256": shard["sha256"],
        }

    def claim_shard(self, pool_id: str,
                    worker_id: str) -> Optional[dict[str, Any]]:
        pool = self.get(pool_id)
        if pool["status"] in (POOL_STATUS_CLOSED, POOL_STATUS_PAUSED):
            raise PoolError(f"pool is {pool['status']}")
        self._ensure_shards(pool)
        shard = self.store.claim_one_shard(pool_id, pool["round"], worker_id)
        if not shard:
            return None
        shard["manifest"] = self._shard_manifest(pool, shard)  # not persisted
        return shard

    def submit_shard(self, pool_id: str, shard_id: str, *,
                     worker_id: Optional[str] = None,
                     run_id: Optional[str] = None,
                     checkpoint_path: Optional[str] = None,
                     metrics: Optional[dict[str, Any]] = None,
                     miner_address: Optional[str] = None,
                     provider_id: str = "ena-pool") -> dict[str, Any]:
        pool = self.get(pool_id)
        shard = self.store.get_shard(shard_id)
        if not shard or shard["pool_id"] != pool_id:
            raise PoolError(f"shard not found: {shard_id}")
        if shard["status"] in (SHARD_SUBMITTED, SHARD_VERIFIED):
            raise PoolError(f"shard already submitted: {shard_id}")
        metrics = dict(metrics or {})
        ckpt_hash = _checkpoint_digest(checkpoint_path, shard)
        run = {
            "run_id": run_id or f"{shard_id}-run",
            "created_at": shard.get("created_at", now_ts()), "updated_at": now_ts(),
            "metrics": metrics, "output_dir": checkpoint_path or "",
            "checkpoint_paths": [checkpoint_path] if checkpoint_path else [],
        }
        manifest = {
            "base_model": pool["base_model"], "hyperparameters": pool["hyperparameters"],
            "metadata": {"method": pool["method"], "source_sha256": shard["sha256"]},
        }
        receipt = training.build_training_receipt(
            run, manifest, miner_address=miner_address or worker_id or "pool",
            provider_id=provider_id, chain_id=self.cfg.chain_id)

        shard.update(
            status=SHARD_SUBMITTED, worker_id=worker_id or shard.get("worker_id"),
            miner_address=miner_address, run_id=run["run_id"],
            checkpoint_path=checkpoint_path, checkpoint_hash=ckpt_hash,
            metrics=metrics, training_receipt=receipt, updated_at=now_ts())
        self.store.upsert_shard(shard)

        weight = _work_weight(receipt, metrics)
        contrib = PoolContribution(
            contribution_id="ctr-" + new_uuid()[:16], pool_id=pool_id,
            round=shard["round"], role="trainer", address=miner_address,
            worker_id=worker_id or shard.get("worker_id"), weight=weight,
            ref=shard_id, receipt_hash=receipt.get("receipt_hash"),
        ).to_dict()
        self.store.add_contribution(contrib)
        return {"shard_id": shard_id, "status": shard["status"],
                "checkpoint_hash": ckpt_hash, "weight": weight,
                "receipt_hash": receipt.get("receipt_hash"),
                "contribution_id": contrib["contribution_id"]}

    # -- aggregate + eval gate + promote ----------------------------------
    def aggregate(self, pool_id: str, *, eval_score: Optional[float] = None,
                  min_submitted: Optional[int] = None) -> dict[str, Any]:
        pool = self.get(pool_id)
        rnd = pool["round"]
        all_shards = self.store.list_shards(pool_id, round=rnd)
        submitted = [s for s in all_shards
                     if s["status"] in (SHARD_SUBMITTED, SHARD_VERIFIED)]
        if not submitted:
            raise PoolError(f"no submitted shards for round {rnd}")
        need = min_submitted if min_submitted is not None else (len(all_shards) or 1)
        if len(submitted) < need:
            return {"pool_id": pool_id, "round": rnd, "promoted": False,
                    "reason": "insufficient_shards",
                    "submitted": len(submitted), "needed": need}

        raw = {s["shard_id"]: _work_weight(s.get("training_receipt", {}),
                                           s.get("metrics", {})) for s in submitted}
        tot = sum(raw.values()) or 1.0
        weights = {k: round(v / tot, 6) for k, v in raw.items()}
        merged = merge_checkpoints(
            submitted, self._pool_dir(pool_id) / f"round-{rnd}" / "merged", weights)
        candidate = {
            "round": rnd, "path": merged["path"], "checkpoint_hash": merged["hash"],
            "merged": merged.get("merged", False),
            "shards": [s["shard_id"] for s in submitted], "weights": weights,
            "created_at": now_ts(),
        }

        gate = pool.get("eval_gate")
        gate_info: dict[str, Any] = {"gated": bool(gate)}
        if gate:
            threshold = float(gate.get("min_score", gate.get("threshold", 0.0)))
            gate_info.update(threshold=threshold,
                             metric=gate.get("metric", "score"), score=eval_score)
            if eval_score is None:
                return {"pool_id": pool_id, "round": rnd, "promoted": False,
                        "reason": "awaiting_eval", "candidate": candidate,
                        "gate": gate_info}
            if float(eval_score) < threshold:
                gate_info["passed"] = False
                return {"pool_id": pool_id, "round": rnd, "promoted": False,
                        "reason": "eval_below_threshold", "candidate": candidate,
                        "gate": gate_info}
            gate_info["passed"] = True

        candidate["eval"] = gate_info
        pool["served_checkpoint"] = candidate
        pool["round"] = rnd + 1
        pool["status"] = POOL_STATUS_TRAINING
        pool["updated_at"] = now_ts()
        self.store.upsert_pool(pool)
        # advance the canonical global model's head so all pools/servers converge
        if pool.get("model_id"):
            self._set_global_head(pool["model_id"], pool_id,
                                  checkpoint_hash=candidate["checkpoint_hash"],
                                  round=candidate["round"])
        return {"pool_id": pool_id, "round": rnd, "promoted": True,
                "served_checkpoint": candidate, "next_round": pool["round"],
                "gate": gate_info, "model_id": pool.get("model_id")}

    # -- payout (proportional, role-split) --------------------------------
    def payout(self, pool_id: str, *, round: Optional[int] = None) -> dict[str, Any]:
        # serialize the whole read-unpaid → allocate → mark-paid sequence so two
        # concurrent payouts can't pay the same contributions twice.
        with self._lock:
            pool = self.get(pool_id)
            rnd = (pool["round"] - 1) if round is None else int(round)
            if rnd < 1:
                raise PoolError("no completed round to pay out")
            contribs = [c for c in self.store.list_contributions(pool_id, round=rnd)
                        if not c.get("paid")]
            budget = int(pool.get("budget_nano", 0))
            if not contribs:
                return {"pool_id": pool_id, "round": rnd, "entries": [],
                        "reason": "nothing_to_pay"}
            if budget <= 0:
                return {"pool_id": pool_id, "round": rnd, "entries": [],
                        "reason": "no_budget"}

            split = pool["reward_split"]
            # split the FULL budget across roles by bps via the same integer-safe
            # splitter used within roles — no nanos lost to floor division.
            role_budget = _split_proportional(
                budget, [(role, float(int(split.get(role, 0)))) for role in ROLES])
            entries: list[dict[str, Any]] = []
            paid_ids: list[str] = []
            total_paid = 0
            for role in ROLES:
                singular = _ROLE_SINGULAR[role]
                members = [c for c in contribs if c["role"] == singular]
                if not members or role_budget.get(role, 0) <= 0:
                    continue
                by_addr: dict[str, float] = {}
                ids_by_addr: dict[str, list[str]] = {}
                for c in members:
                    key = c.get("address") or c.get("worker_id") or c["contribution_id"]
                    by_addr[key] = by_addr.get(key, 0.0) + float(c.get("weight", 0))
                    ids_by_addr.setdefault(key, []).append(c["contribution_id"])
                alloc = _split_proportional(role_budget[role], list(by_addr.items()))
                for addr, nano in alloc.items():
                    if nano <= 0:
                        continue
                    entries.append({"role": singular, "address": addr, "nano": nano,
                                    "anm": pay.nano_to_anm(nano), "weight": by_addr[addr]})
                    paid_ids.extend(ids_by_addr[addr])
                    total_paid += nano

            self.store.mark_contributions_paid(paid_ids)
            pool["budget_nano"] = budget - total_paid
            pool["paid_out_nano"] = int(pool.get("paid_out_nano", 0)) + total_paid
            pool["updated_at"] = now_ts()
            self.store.upsert_pool(pool)
            return {"pool_id": pool_id, "round": rnd, "budget_nano": budget,
                    "paid_nano": total_paid, "remaining_nano": pool["budget_nano"],
                    "reward_split": split, "entries": entries}

    # -- status / leaderboard ---------------------------------------------
    def status(self, pool_id: str) -> dict[str, Any]:
        pool = self.get(pool_id)
        rnd = pool["round"]
        shard_status: dict[str, int] = {}
        for s in self.store.list_shards(pool_id, round=rnd):
            shard_status[s["status"]] = shard_status.get(s["status"], 0) + 1
        by_role: dict[str, int] = {}
        for c in self.store.list_contributions(pool_id):
            by_role[c["role"]] = by_role.get(c["role"], 0) + 1
        return {
            "pool_id": pool_id, "name": pool["name"], "status": pool["status"],
            "base_model": pool["base_model"], "method": pool["method"],
            "round": rnd, "num_shards": pool["num_shards"],
            "shards": shard_status, "contributions": by_role,
            "shards_submitted": shard_status.get("submitted", 0) + shard_status.get("verified", 0),
            "funded": int(pool.get("budget_nano", 0)) > 0,
            "budget_nano": int(pool.get("budget_nano", 0)),
            "budget_anm": pay.nano_to_anm(int(pool.get("budget_nano", 0))),
            "paid_out_nano": int(pool.get("paid_out_nano", 0)),
            "served_checkpoint": pool.get("served_checkpoint"),
            "reward_split": pool["reward_split"],
        }

    def leaderboard(self, pool_id: str, limit: int = 10) -> list[dict[str, Any]]:
        agg: dict[tuple[str, str], float] = {}
        for c in self.store.list_contributions(pool_id):
            key = (c["role"], c.get("address") or c.get("worker_id") or "?")
            agg[key] = agg.get(key, 0.0) + float(c.get("weight", 0))
        rows = [{"role": r, "who": who, "weight": round(w, 6)}
                for (r, who), w in agg.items()]
        rows.sort(key=lambda x: x["weight"], reverse=True)
        return rows[:limit]

    # -- serving (serve-while-train; servers earn the 'servers' bps) --------
    def get_served_checkpoint(self, pool_id: str) -> dict[str, Any]:
        """The pool's currently-promoted checkpoint + its global model id.

        ``model_id`` (``pool_id:checkpoint_hash``) is the uniform handle servers
        advertise and clients request — the anchor for the one global model.
        """
        pool = self.get(pool_id)
        served = pool.get("served_checkpoint")
        if not served:
            raise PoolError(f"pool {pool_id} has no promoted checkpoint yet",
                            hint="aggregate a round first")
        return {
            "pool_id": pool_id,
            "model_id": f"{pool_id}:{served['checkpoint_hash']}",
            "base_model": pool["base_model"], "round": served["round"],
            "checkpoint_hash": served["checkpoint_hash"], "path": served["path"],
            "merged": served.get("merged", False), "eval": served.get("eval", {}),
            "created_at": served.get("created_at"),
        }

    def register_server(self, pool_id: str, worker_id: str, endpoint: str, *,
                        address: Optional[str] = None,
                        metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Register an inference server for this pool's promoted checkpoint."""
        served = self.get_served_checkpoint(pool_id)
        rec = {
            "server_id": "srv-" + sha3_hex(f"{pool_id}|{worker_id}")[:16],
            "pool_id": pool_id, "worker_id": worker_id, "address": address,
            "endpoint": endpoint, "model_id": served["model_id"],
            "checkpoint_hash": served["checkpoint_hash"], "round": served["round"],
            "status": "active", "metadata": metadata or {},
            "created_at": now_ts(), "updated_at": now_ts(),
        }
        self.store.upsert_server(rec)
        return rec

    def record_served(self, pool_id: str, worker_id: str, tokens: int, *,
                      address: Optional[str] = None, run_id: Optional[str] = None,
                      latency_ms: Optional[float] = None,
                      served_round: Optional[int] = None) -> dict[str, Any]:
        """Credit a server for tokens served (weight → 'servers' payout bucket).

        Credits the round whose checkpoint was *served* (``served_round``), not
        the round currently training — once ``aggregate`` advances the round, a
        served-checkpoint request still belongs to the round it was promoted in.
        """
        pool = self.get(pool_id)
        rnd = int(served_round) if served_round is not None else pool["round"]
        contrib = PoolContribution(
            contribution_id="ctr-" + new_uuid()[:16], pool_id=pool_id, round=rnd,
            role="server", address=address, worker_id=worker_id,
            weight=float(tokens), ref=run_id or worker_id,
            metadata={"latency_ms": latency_ms} if latency_ms is not None else {},
        ).to_dict()
        self.store.add_contribution(contrib)
        return {"pool_id": pool_id, "worker_id": worker_id, "round": rnd,
                "tokens": int(tokens), "contribution_id": contrib["contribution_id"]}

    def list_servers(self, pool_id: str,
                     status: Optional[str] = "active") -> list[dict[str, Any]]:
        self.get(pool_id)
        return self.store.list_servers(pool_id, status=status)

    # -- one global model (many pools converge on one model) --------------
    def ensure_global_model(self, model_id: str, base_model: str) -> dict[str, Any]:
        """Register (idempotently) a canonical model that pools contribute to."""
        existing = self.store.get_model(model_id)
        if existing:
            return existing
        with self._lock:
            existing = self.store.get_model(model_id)
            if existing:
                return existing
            model = {"model_id": model_id, "base_model": base_model,
                     "head": None, "pools": [], "created_at": now_ts(),
                     "updated_at": now_ts()}
            self.store.upsert_model(model)
            return model

    def get_global_model(self, model_id: str) -> dict[str, Any]:
        model = self.store.get_model(model_id)
        if not model:
            raise PoolError(f"global model not found: {model_id}")
        return model

    def list_models(self) -> list[dict[str, Any]]:
        return self.store.list_models()

    def _set_global_head(self, model_id: str, pool_id: str, *,
                         checkpoint_hash: str, round: int) -> None:
        """Advance the canonical model's promoted head (called on promotion)."""
        with self._lock:
            model = self.store.get_model(model_id)
            if not model:
                return
            model["head"] = {"pool_id": pool_id, "checkpoint_hash": checkpoint_hash,
                             "round": int(round), "promoted_at": now_ts()}
            pools = set(model.get("pools") or [])
            pools.add(pool_id)
            model["pools"] = sorted(pools)
            model["updated_at"] = now_ts()
            self.store.upsert_model(model)
