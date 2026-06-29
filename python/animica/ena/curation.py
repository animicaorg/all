"""
animica.ena.curation
====================

Coordinator-side quality gate + safe genome growth for worker-synthesized data.

This is the DNA-safety half of the synthesis flywheel. Workers generate
``{prompt, response}`` pairs **locally** (see :mod:`animica.ena.synth`); the
coordinator then runs a **deterministic, GPU-free, model-free** gate over the
submitted pairs and lets only healthy, novel, well-formed mutations accumulate
in a STAGING dataset. Growing the LIVE pool genome is a separate, explicit,
bounded :func:`promote_staging` step — never a blind append.

Pipeline::

    worker pairs ──▶ stage_submission()          (gate + within-staging dedupe)
                         │  writes  staging/synthesized.staging.jsonl
                         │  logs    staging/synthesized.rejected.jsonl
                         ▼
                    promote_staging(genome_path)  (genome dedupe + validate +
                                                   bounded append + sha/rows)

Every check is lexical/structural (length, emptiness, schema, token overlap with
the source chunk for groundedness, content-hash dedupe). No model is loaded here,
so curation runs in the coordinator's request path without the CPU-pegging /
read-timeout failure mode that in-request model jobs caused.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from . import datasets as ds
from .models import now_ts

# Default gate thresholds. Conservative but not punishing; tune per deployment
# via the ``thresholds`` arg or the job's ``params["gate"]``.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_prompt_len": 8,
    "max_prompt_len": 1200,
    "min_response_len": 12,
    "max_response_len": 6000,
    "min_groundedness": 0.12,  # fraction of response content-tokens seen in source
}

STAGING_DIRNAME = "staging"
STAGING_FILE = "synthesized.staging.jsonl"
REJECTED_FILE = "synthesized.rejected.jsonl"
# Hard cap on rows promoted into the live genome per cycle (no runaway growth).
DEFAULT_MAX_PROMOTE_ROWS = 200


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def _content_tokens(s: str) -> set[str]:
    # Reuse the curriculum eval tokenizer (stopword-filtered, len>=3) so
    # groundedness scoring matches how the pool grades the model.
    from .curriculum import _content_tokens as ct
    return ct(s)


def quality_gate(pair: dict[str, Any], source_chunk: str = "",
                 thresholds: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Validate a single synthesized pair. Deterministic, model-free.

    Returns ``{valid, checks, dedupe_key, groundedness, reason, record}`` where
    ``record`` is the normalized ``{prompt, response}`` (or None) and
    ``dedupe_key`` is the same content hash the dataset pipeline dedupes on.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: Any = None) -> bool:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        return bool(passed)

    norm = ds._normalize_record(pair) if isinstance(pair, dict) else None
    if not norm or "prompt" not in norm or "response" not in norm:
        add("schema_valid", False, "missing/unparseable prompt or response")
        return {"valid": False, "checks": checks, "dedupe_key": None,
                "groundedness": 0.0, "reason": "invalid schema", "record": None}
    add("schema_valid", True)

    prompt = norm["prompt"].strip()
    response = norm["response"].strip()

    ok = True
    ok &= add("prompt_length", th["min_prompt_len"] <= len(prompt) <= th["max_prompt_len"], len(prompt))
    ok &= add("response_length", th["min_response_len"] <= len(response) <= th["max_response_len"], len(response))
    ok &= add("non_empty", bool(prompt) and bool(response))

    groundedness = 1.0
    if source_chunk:
        src = _content_tokens(source_chunk)
        rsp = _content_tokens(response)
        groundedness = (len(src & rsp) / len(rsp)) if rsp else 0.0
        ok &= add("groundedness", groundedness >= th["min_groundedness"],
                  round(groundedness, 3))
    else:
        add("groundedness", True, "no source chunk; skipped")

    dedupe_key = ds._dedupe_key(norm)
    return {"valid": ok, "checks": checks, "dedupe_key": dedupe_key,
            "groundedness": round(groundedness, 3),
            "reason": "valid" if ok else "failed gate", "record": norm}


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------

def staging_dir(cfg) -> Path:
    d = cfg.artifacts_dir() / STAGING_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {ds._dedupe_key(r) for r in ds.read_jsonl(path)}


def stage_submission(cfg, job: dict[str, Any],
                     thresholds: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Gate a synthesis job's submitted pairs into the STAGING dataset.

    Reads ``job['result']['pairs']`` and the source chunk from
    ``job['params']['corpus']``. Survivors that are novel vs the current staging
    file are appended to it; everything rejected is appended (with its gate
    report) to the rejected log for audit. Idempotent: re-staging the same job
    adds nothing new (content-hash dedupe). Never touches the live genome.
    """
    result = job.get("result") or {}
    params = job.get("params") or {}
    pairs = result.get("pairs") or []
    source = params.get("corpus") or params.get("source_chunk") or ""
    th = {**(params.get("gate") or {}), **(thresholds or {})}

    d = staging_dir(cfg)
    staging_path = d / STAGING_FILE
    rejected_path = d / REJECTED_FILE

    seen = _staging_keys(staging_path)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicates = 0
    for pair in pairs:
        g = quality_gate(pair, source_chunk=source, thresholds=th)
        if not g["valid"]:
            rejected.append({"pair": pair, "reason": g["reason"], "checks": g["checks"],
                             "job_id": job.get("job_id")})
            continue
        key = g["dedupe_key"]
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        accepted.append({**g["record"], "_src": job.get("job_id")})

    if accepted:
        _append_jsonl(staging_path, accepted)
    if rejected:
        _append_jsonl(rejected_path, rejected)

    return {
        "job_id": job.get("job_id"),
        "submitted": len(pairs),
        "staged": len(accepted),
        "duplicates": duplicates,
        "rejected": len(rejected),
        "staging_path": str(staging_path),
        "staging_rows_total": ds.row_count(staging_path) if staging_path.is_file() else 0,
    }


# ---------------------------------------------------------------------------
# Gated promotion  (explicit, bounded, deduped vs the live genome)
# ---------------------------------------------------------------------------

def promote_staging(cfg, genome_path: str | Path, *,
                    max_rows: int = DEFAULT_MAX_PROMOTE_ROWS,
                    dry_run: bool = False) -> dict[str, Any]:
    """Grow the live training genome from STAGING — safely and explicitly.

    Steps: normalize staging → drop anything already present in the genome
    (content-hash) → validate shape → take at most ``max_rows`` → append to the
    genome → recompute sha256/row_count → remove the promoted rows from staging.

    Returns a summary including the new ``dataset_sha256`` / ``dataset_rows`` so
    the caller can update the pool record atomically. With ``dry_run`` nothing is
    written (preview only). This is the ONLY function that mutates the genome,
    and it is never called automatically.
    """
    d = staging_dir(cfg)
    staging_path = d / STAGING_FILE
    genome = Path(genome_path)
    if not staging_path.is_file() or ds.row_count(staging_path) == 0:
        return {"promoted": 0, "reason": "empty staging",
                "staging_rows": 0, "genome_path": str(genome)}

    # Normalize staging rows (strips the internal _src marker into training shape).
    norm = d / "staging.norm.jsonl"
    ds.normalize(staging_path, norm)

    genome_keys: set[str] = set()
    if genome.is_file():
        genome_keys = {ds._dedupe_key(r) for r in ds.read_jsonl(genome)}

    novel: list[dict[str, Any]] = []
    novel_keys: set[str] = set()
    for rec in ds.read_jsonl(norm):
        key = ds._dedupe_key(rec)
        if key in genome_keys or key in novel_keys:
            continue
        novel_keys.add(key)
        novel.append(rec)

    capped = novel[: max(0, int(max_rows))]

    # Validate the batch before it can touch the genome.
    preview = d / "staging.promote.jsonl"
    ds.write_jsonl(preview, capped)
    validation = ds.validate(preview) if capped else {"valid": False, "rows": 0}

    summary = {
        "staging_rows": ds.row_count(staging_path),
        "novel": len(novel),
        "promotable": len(capped),
        "max_rows": int(max_rows),
        "validation": validation,
        "genome_path": str(genome),
        "promoted": 0,
        "dry_run": bool(dry_run),
    }
    if dry_run or not capped or not validation.get("valid"):
        if not validation.get("valid"):
            summary["reason"] = "validation_failed" if capped else "no_novel_rows"
        return summary

    # Reversibility (HARD-4): snapshot the live genome BEFORE the first append,
    # so a bad promote can always be rolled back to the pre-append file. The .bak
    # is timestamped and records in the summary; we never append before it exists.
    if genome.is_file():
        backup = genome.with_suffix(genome.suffix + f".bak-{now_ts()}")
        shutil.copy2(genome, backup)
        summary["backup_path"] = str(backup)

    # Append survivors to the genome, then recompute its identity.
    _append_jsonl(genome, capped)
    summary["promoted"] = len(capped)
    summary["dataset_sha256"] = ds.sha256_file(genome)
    summary["dataset_rows"] = ds.row_count(genome)

    # qDNA (opt-in): seal each promoted gene into the quantum-sealed, Merkle-
    # anchored sidecar ledger. Sidecar-only (the genome file above is unchanged),
    # best-effort (never breaks growth), and OFF unless ANIMICA_ENA_QDNA is set.
    try:
        from . import genome as _genome
        if _genome.qdna_enabled():
            qres = _genome.record_for_genome(genome, capped)
            summary["qdna"] = {"epoch": qres["epoch"], "added": qres["added"],
                               "genome_root": qres["genome_root"]}
    except Exception as exc:  # noqa: BLE001 - qDNA never blocks the genome growth
        summary["qdna_error"] = str(exc)

    # Drop the promoted rows from staging (keep the rest for the next cycle).
    promoted_keys = {ds._dedupe_key(r) for r in capped}
    remaining = [r for r in ds.read_jsonl(staging_path)
                 if ds._dedupe_key(r) not in promoted_keys]
    ds.write_jsonl(staging_path, remaining)
    summary["staging_rows_after"] = len(remaining)
    return summary


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n
