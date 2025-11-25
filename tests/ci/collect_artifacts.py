#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collect CI artifacts into a single output directory.

It gathers (if present):
- JUnit XML from tests/reports/
- Coverage XML and HTML reports from tests/reports/
- Bench outputs (JSON/MD) from tests/reports/bench/ and tests/bench/
- Writes a tiny index.txt summary

Usage:
  python tests/ci/collect_artifacts.py
  python tests/ci/collect_artifacts.py --out-dir outputs
  python tests/ci/collect_artifacts.py --reports-dir tests/reports --bench-dir tests/reports/bench --out-dir outputs

Exit code is 0 even if some sources are missing (best-effort collector).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

REPO = Path(__file__).resolve().parents[2]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _copy_file(src: Path, dst: Path) -> bool:
    try:
        _ensure_dir(dst.parent)
        shutil.copy2(src, dst)
        print(f"[collect] file: {src} -> {dst}")
        return True
    except Exception as exc:
        print(f"[collect] WARN: failed to copy {src} -> {dst}: {exc}")
        return False


def _copy_tree(src: Path, dst: Path) -> bool:
    try:
        if dst.exists():
            # Merge copy: copy files into existing tree (preserve previous)
            for root, _, files in os.walk(src):
                rel = Path(root).relative_to(src)
                out_root = dst / rel
                _ensure_dir(out_root)
                for f in files:
                    _copy_file(Path(root) / f, out_root / f)
        else:
            shutil.copytree(src, dst)
        print(f"[collect] dir : {src} -> {dst}")
        return True
    except Exception as exc:
        print(f"[collect] WARN: failed to copy dir {src} -> {dst}: {exc}")
        return False


def find_coverage_html_dirs(reports_dir: Path) -> List[Path]:
    # Common names used in run_fast_suite / run_full_suite and generic tools
    candidates: List[Path] = []
    for child in reports_dir.iterdir() if reports_dir.exists() else []:
        if child.is_dir() and (
            child.name.startswith("coverage-") and child.name.endswith("-html")
            or child.name == "htmlcov"
        ):
            candidates.append(child)
    return candidates


def find_junit_xmls(reports_dir: Path) -> List[Path]:
    xmls: List[Path] = []
    for p in reports_dir.glob("*.xml"):
        # Collect all XML; JUnit and coverage XML are both useful. We'll organize by name.
        xmls.append(p)
    return xmls


def find_bench_outputs(reports_bench_dir: Path, bench_src_dir: Path) -> List[Tuple[Path, str]]:
    items: List[Tuple[Path, str]] = []
    # JSON results written by test/bench runner (if any)
    if reports_bench_dir.exists():
        for p in reports_bench_dir.rglob("*.json"):
            items.append((p, f"results/{p.name}"))
        for p in reports_bench_dir.rglob("*.md"):
            items.append((p, f"results/{p.name}"))
    # Canonical baselines & human report in tests/bench/
    for name in ("baselines.json", "report.md"):
        p = bench_src_dir / name
        if p.exists():
            items.append((p, name))
    return items


def write_index(out_dir: Path, collected: List[str]) -> None:
    idx = out_dir / "index.txt"
    _ensure_dir(out_dir)
    idx.write_text(
        "Animica CI Artifacts\n"
        "====================\n\n"
        + "\n".join(f"- {line}" for line in collected)
        + ("\n" if collected else "No artifacts found.\n")
    )
    print(f"[collect] wrote {idx}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect junit/coverage/bench artifacts into an output directory.")
    parser.add_argument("--reports-dir", default=str(REPO / "tests" / "reports"), help="Directory with test reports.")
    parser.add_argument("--bench-dir", default=str(REPO / "tests" / "reports" / "bench"),
                        help="Directory with benchmark outputs (JSON/MD).")
    parser.add_argument("--bench-src", default=str(REPO / "tests" / "bench"),
                        help="Source directory for baselines.json/report.md.")
    parser.add_argument("--out-dir", default=str(REPO / "outputs"), help="Destination directory for artifacts.")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    bench_dir = Path(args.bench_dir)
    bench_src = Path(args.bench_src)
    out_dir = Path(args.out_dir)

    collected: List[str] = []
    _ensure_dir(out_dir)

    # 1) Copy JUnit & coverage XML
    xml_out = out_dir / "xml"
    for xml in find_junit_xmls(reports_dir):
        if _copy_file(xml, xml_out / xml.name):
            collected.append(f"xml/{xml.name}")

    # 2) Copy coverage HTML directories
    cov_html_out = out_dir / "coverage" / "html"
    for cov_dir in find_coverage_html_dirs(reports_dir):
        dst = cov_html_out / cov_dir.name
        if _copy_tree(cov_dir, dst):
            collected.append(f"coverage/html/{cov_dir.name}/")

    # 3) Copy coverage XML specifically into coverage/xml/
    cov_xml_out = out_dir / "coverage" / "xml"
    for cov_xml in reports_dir.glob("coverage-*.xml"):
        if _copy_file(cov_xml, cov_xml_out / cov_xml.name):
            collected.append(f"coverage/xml/{cov_xml.name}")
    # Also catch common xmlcov names
    for cov_xml in reports_dir.glob("*.xml"):
        if cov_xml.name.startswith("coverage") and cov_xml.name.endswith(".xml"):
            if _copy_file(cov_xml, cov_xml_out / cov_xml.name):
                collected.append(f"coverage/xml/{cov_xml.name}")

    # 4) Bench outputs (results from reports/bench, plus baselines/report from tests/bench)
    bench_out = out_dir / "bench"
    for src, rel in find_bench_outputs(bench_dir, bench_src):
        if _copy_file(src, bench_out / rel):
            collected.append(f"bench/{rel}")

    # 5) Optional: copy Playwright traces/videos if they exist (common CI desire)
    # Look for playwright-report/ or test-results/ at repo root or under tests/reports
    for cand in (
        REPO / "playwright-report",
        REPO / "test-results",
        reports_dir / "playwright-report",
        reports_dir / "test-results",
    ):
        if cand.exists() and cand.is_dir():
            dst = out_dir / cand.name
            if _copy_tree(cand, dst):
                collected.append(f"{cand.name}/")

    # 6) Tiny index
    write_index(out_dir, collected)

    print("[collect] done.")
    # Always succeed (artifact collection shouldn't fail the job)
    return 0


if __name__ == "__main__":
    sys.exit(main())
