#!/usr/bin/env python3
"""Stage: build CPT, SFT, diff, mutation, incidents, eval splits.

Each split is a JSONL of training-ready records.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_DS_DIR = _RUN_DIR / "datasets"
_DS_DIR.mkdir(parents=True, exist_ok=True)


def _read_clean() -> Iterator[dict]:
    clean = _RUN_DIR / "corpora" / "clean.jsonl"
    with clean.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _cpt_records(cfg) -> Iterable[dict]:
    include = set(cfg.datasets["splits"]["cpt"]["include_labels"])
    exclude_tags = set(cfg.datasets["splits"]["cpt"].get("exclude_tags", []))
    for r in _read_clean():
        if r.get("label") not in include:
            continue
        if exclude_tags & set(r.get("tags", [])):
            continue
        yield {"text": r["text"], "source": r["path"], "label": r["label"]}


def _sft_views(rec: dict, cfg) -> Iterator[dict]:
    """Synthesize multi-view SFT examples from a clean chunk."""
    path = rec["path"]
    label = rec.get("label", "")
    text = rec["text"]
    # explain_view
    yield {
        "messages": [
            {"role": "user",
             "content": f"What does the code in `{path}` do?"},
            {"role": "assistant",
             "content": (f"Summary of `{path}`:\n\n```{_lang(label)}\n"
                          f"{_truncate(text, 1200)}\n```\n\n"
                          f"This file is part of the Animica monorepo "
                          f"({label}). It implements the logic shown above; "
                          f"see surrounding files in the same directory for "
                          f"the broader context.")},
        ],
        "view": "explain_view", "source": path,
    }
    # operator_command_view (only for ops + cli files)
    if "ops/" in path or "/cli/" in path or path.endswith(".sh"):
        yield {
            "messages": [
                {"role": "user",
                 "content": f"What's the right command to interact with "
                             f"`{path}`?"},
                {"role": "assistant",
                 "content": _operator_hint_from_path(path)},
            ],
            "view": "operator_command_view", "source": path,
        }


def _lang(label: str) -> str:
    return {
        "code/python": "python", "code/rust": "rust",
        "code/go": "go", "code/typescript": "typescript",
        "code/javascript": "javascript", "code/shell": "bash",
        "code/sql": "sql", "code/html": "html", "code/css": "css",
        "code/proto": "proto",
    }.get(label, "")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "\n...(truncated)..."


def _operator_hint_from_path(path: str) -> str:
    if path.startswith("ops/docker/docker-compose"):
        return (f"```bash\n"
                f"docker compose -f {path} up -d\n"
                f"docker compose -f {path} ps\n"
                f"docker compose -f {path} logs -f\n"
                f"```\n"
                f"Run from the repo root; the compose file references "
                f"`context: ../..` so it must be invoked relative to the "
                f"repo tree.")
    if path.endswith(".sh"):
        return f"```bash\nbash {path}\n```"
    if "/cli/" in path:
        # Pull the subcommand name from filename
        sub = Path(path).stem
        return f"```bash\nanimica {sub} --help\n```"
    return f"```bash\nfile {path}\n```"


def _git_diffs(cfg, repo_root: Path,
               max_commits: int) -> Iterator[dict]:
    try:
        out = subprocess.check_output(
            ["git", "log",
             f"--max-count={max_commits}",
             "--pretty=format:%H%x09%s"],
            cwd=str(repo_root), text=True, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return
    prefer = cfg.datasets["splits"]["diff"]["prefer_subject_patterns"]
    prefer_re = re.compile("|".join(re.escape(p) for p in prefer),
                            re.IGNORECASE) if prefer else None
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, _, subject = line.partition("\t")
        if prefer_re and not prefer_re.search(subject):
            continue
        try:
            patch = subprocess.check_output(
                ["git", "show", "--unified=3", "--no-color", sha],
                cwd=str(repo_root), text=True, timeout=10,
            )
        except subprocess.SubprocessError:
            continue
        if len(patch) > 50_000:
            continue
        yield {"messages": [
            {"role": "user",
             "content": f"Fix the regression described by: {subject}"},
            {"role": "assistant",
             "content": f"Patch:\n```diff\n{_truncate(patch, 6000)}\n```"},
        ], "view": "patch_view", "source": f"git:{sha[:12]}"}


def _eval_from_packs(cfg) -> Iterator[dict]:
    for pack in cfg.incidents["packs"]:
        yield {
            "id": pack["id"],
            "title": pack["title"],
            "tags": pack.get("tags", []),
            "messages": [
                {"role": "user", "content": pack["prompt"]},
                {"role": "assistant", "content": pack["gold"]},
            ],
            "view": pack.get("gold_view", "explain_view"),
            "source": f"incident:{pack['id']}",
        }


def main() -> int:
    cfg = load_config()
    rng = random.Random(int(cfg.training["determinism"]["seed"]))
    repo_root = Path(os.environ.get("ANIMICA_REPO_ROOT") or cfg.repo_root)

    cpt_path = _DS_DIR / "cpt.jsonl"
    sft_path = _DS_DIR / "sft.jsonl"
    diff_path = _DS_DIR / "diff.jsonl"
    mutation_path = _DS_DIR / "mutation.jsonl"
    incidents_path = _DS_DIR / "incidents.jsonl"
    eval_path = _DS_DIR / "eval.jsonl"

    # CPT
    n_cpt = 0
    with cpt_path.open("w", encoding="utf-8") as fh:
        for r in _cpt_records(cfg):
            fh.write(json.dumps(r) + "\n")
            n_cpt += 1
            if n_cpt >= 200_000:  # soft cap on simulate-mode runs
                break

    # SFT from clean corpora (multi-view synthesizers).
    n_sft = 0
    max_sft = int(cfg.datasets["splits"]["sft"]["max_total_examples"])
    with sft_path.open("w", encoding="utf-8") as fh:
        for r in _read_clean():
            for ex in _sft_views(r, cfg):
                fh.write(json.dumps(ex) + "\n")
                n_sft += 1
                if n_sft >= max_sft:
                    break
            if n_sft >= max_sft:
                break

    # diff/patch from git log.
    n_diff = 0
    max_diff = int(cfg.datasets["splits"]["diff"]["git_log_max_commits"])
    with diff_path.open("w", encoding="utf-8") as fh:
        for ex in _git_diffs(cfg, repo_root, max_diff):
            fh.write(json.dumps(ex) + "\n")
            n_diff += 1
            if n_diff >= 5000:
                break

    # mutation: simple identifier-rename mutations on python-labelled chunks.
    n_mut = 0
    eligible = set(cfg.datasets["splits"]["mutation"]["eligible_labels"])
    with mutation_path.open("w", encoding="utf-8") as fh:
        for r in _read_clean():
            if r.get("label") not in eligible:
                continue
            text = r["text"]
            m = re.search(r"\b([a-z_][a-z_0-9]{3,})\b", text)
            if not m:
                continue
            name = m.group(1)
            mutated = re.sub(rf"\b{name}\b", name + "_BUG", text, count=3)
            fh.write(json.dumps({
                "messages": [
                    {"role": "user",
                     "content": f"This snippet was mutated to introduce "
                                 f"a bug — restore the original symbol name:"
                                 f"\n\n```\n{_truncate(mutated, 2000)}\n```"},
                    {"role": "assistant",
                     "content": f"The renamed symbol is `{name}_BUG`. "
                                 f"Restoring it to `{name}` undoes the "
                                 f"mutation:\n```diff\n- {name}_BUG\n+ {name}\n```"},
                ],
                "view": "patch_view",
                "source": r["path"],
            }) + "\n")
            n_mut += 1
            if n_mut >= 5000:
                break

    # Incidents: SFT seeds.
    n_inc = 0
    with incidents_path.open("w", encoding="utf-8") as fh:
        for ex in _eval_from_packs(cfg):
            fh.write(json.dumps(ex) + "\n")
            n_inc += 1

    # Eval: held-out fraction of sft + curated incident eval set.
    n_eval = 0
    eval_frac = float(cfg.datasets["splits"]["eval"]["fraction"])
    with eval_path.open("w", encoding="utf-8") as fh, \
         sft_path.open("r", encoding="utf-8") as sfh:
        sft_lines = sfh.readlines()
        rng.shuffle(sft_lines)
        holdout = int(len(sft_lines) * eval_frac)
        for line in sft_lines[:holdout]:
            fh.write(line)
            n_eval += 1
        eval_hold = float(cfg.incidents["synthesis"]["eval_holdout_fraction"])
        for ex in _eval_from_packs(cfg):
            if rng.random() < eval_hold:
                fh.write(json.dumps(ex) + "\n")
                n_eval += 1

    summary = {
        "schema": 1, "run_id": _RUN_ID,
        "n_cpt": n_cpt, "n_sft": n_sft, "n_diff": n_diff,
        "n_mutation": n_mut, "n_incidents": n_inc, "n_eval": n_eval,
    }
    (_DS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[datasets] cpt={n_cpt} sft={n_sft} diff={n_diff} "
          f"mutation={n_mut} incidents={n_inc} eval={n_eval}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
