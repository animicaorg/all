#!/usr/bin/env python3
"""Stage: build a lightweight repo graph from inventory + source.

Reads:  runs/<run_id>/inventory/files.jsonl + ai/configs/graph.yaml
Writes: runs/<run_id>/graph/nodes.jsonl + edges.jsonl + summary.json
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_OUT_DIR = _RUN_DIR / "graph"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

_IMPORT_FROM = re.compile(r"^from\s+([\w.]+)\s+import", re.MULTILINE)
_IMPORT_PLAIN = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)
_PYDEF = re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE)
_RUST_SYM = re.compile(
    r"^(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl|mod)\s+(\w+)",
    re.MULTILINE)
_GO_SYM = re.compile(r"^(?:func|type|var|const)\s+(\w+)", re.MULTILINE)
_TS_SYM = re.compile(
    r"^(?:export\s+)?(?:async\s+)?"
    r"(?:function|class|interface|type|const|let|var)\s+(\w+)",
    re.MULTILINE)
_RPC_DECL = re.compile(r"rpc_method\(['\"]([\w.]+)['\"]")
_CLI_ADD = re.compile(r"app\.add_typer\((\w+)\.app,\s*name=['\"](\w+)['\"]")
_ENV_REF = re.compile(r"os\.environ(?:\.get)?\(['\"]([A-Z_][A-Z0-9_]*)['\"]")


def _emit(fh, kind: str, **fields) -> None:
    fh.write(json.dumps({"kind": kind, **fields}) + "\n")


def main() -> int:
    cfg = load_config()
    repo_root = Path(os.environ.get("ANIMICA_REPO_ROOT") or cfg.repo_root)
    inv = _RUN_DIR / "inventory" / "files.jsonl"
    if not inv.is_file():
        print(f"[graph] missing {inv}", file=sys.stderr)
        return 1

    n_nodes = 0
    n_edges = 0
    seen_symbols: set[tuple[str, str]] = set()    # (file, name)
    rpc_methods: set[str] = set()
    cli_subs: set[str] = set()

    with (_OUT_DIR / "nodes.jsonl").open("w", encoding="utf-8") as nfh, \
         (_OUT_DIR / "edges.jsonl").open("w", encoding="utf-8") as efh, \
         inv.open("r", encoding="utf-8") as ifh:

        for line in ifh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("oversize"):
                continue
            path = rec["path"]
            label = rec.get("label", "")
            _emit(nfh, "file", path=path, label=label,
                  tags=rec.get("tags", []))
            n_nodes += 1
            if not label.startswith("code/") and not label.startswith("docs/"):
                continue
            full = repo_root / path
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Imports (python only — cheap, high signal).
            if label == "code/python":
                for m in _IMPORT_FROM.finditer(text):
                    _emit(efh, "imports", src=path, dst=m.group(1))
                    n_edges += 1
                for m in _IMPORT_PLAIN.finditer(text):
                    _emit(efh, "imports", src=path, dst=m.group(1))
                    n_edges += 1
                for m in _PYDEF.finditer(text):
                    sym = m.group(1)
                    key = (path, sym)
                    if key not in seen_symbols:
                        seen_symbols.add(key)
                        _emit(nfh, "symbol", file=path, name=sym,
                              language="python")
                        n_nodes += 1
            elif label == "code/rust":
                for m in _RUST_SYM.finditer(text):
                    _emit(nfh, "symbol", file=path, name=m.group(1),
                          language="rust")
                    n_nodes += 1
            elif label == "code/go":
                for m in _GO_SYM.finditer(text):
                    _emit(nfh, "symbol", file=path, name=m.group(1),
                          language="go")
                    n_nodes += 1
            elif label in {"code/typescript", "code/javascript"}:
                for m in _TS_SYM.finditer(text):
                    _emit(nfh, "symbol", file=path, name=m.group(1),
                          language="ts")
                    n_nodes += 1
            # RPC method declarations.
            if path.startswith("rpc/") or "rpc" in path:
                for m in _RPC_DECL.finditer(text):
                    method = m.group(1)
                    if method not in rpc_methods:
                        rpc_methods.add(method)
                        _emit(nfh, "rpc_method", name=method, file=path)
                        n_nodes += 1
            # CLI subcommand registrations.
            if path.endswith("python/animica/cli/main.py"):
                for m in _CLI_ADD.finditer(text):
                    cli_subs.add(m.group(2))
                    _emit(nfh, "cli_subcommand", name=m.group(2),
                          source_module=m.group(1))
                    n_nodes += 1
            # Env-var settings nodes.
            for m in _ENV_REF.finditer(text):
                _emit(efh, "configured_by", src=path, dst=m.group(1))
                n_edges += 1

    summary = {
        "schema": 1, "run_id": _RUN_ID,
        "n_nodes": n_nodes, "n_edges": n_edges,
        "n_rpc_methods": len(rpc_methods),
        "n_cli_subcommands": len(cli_subs),
    }
    (_OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[graph] nodes={n_nodes} edges={n_edges} "
          f"rpc={len(rpc_methods)} cli={len(cli_subs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
