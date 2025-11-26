# pytest: governance/tests/test_validate_examples.py
"""
Examples pass bounds/quorum rules.

This suite verifies:
1) Every example proposal under governance/examples/*.md passes the validator
   (schema + bounds) with --strict enabled.
2) The sample ballots tally to a PASSED outcome under reasonable quorum/approval
   thresholds, exercising the tallying logic (snapshot, window, chain, etc).

Run:
  pytest -q governance/tests/test_validate_examples.py
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import json
import re

import importlib.util

import pytest

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore[assignment]

# Repo layout
ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "governance" / "examples"
SCRIPTS = ROOT / "governance" / "scripts"
REGISTRIES = ROOT / "governance" / "registries"
SCHEMAS = ROOT / "governance" / "schemas"

# Make script modules importable
import sys
sys.path.insert(0, str(SCRIPTS))

# Import the CLI entrypoints as callables
import validate_proposal   # type: ignore
import tally_votes         # type: ignore


if yaml is None:
    pytest.skip("PyYAML not installed (pip install pyyaml)", allow_module_level=True)
if importlib.util.find_spec("jsonschema") is None or validate_proposal.Draft202012Validator is None:
    pytest.skip("jsonschema not installed (pip install jsonschema)", allow_module_level=True)


def discover_markdown_examples() -> List[Path]:
    return sorted(EXAMPLES.rglob("*.md"))


@pytest.mark.parametrize("md_file", discover_markdown_examples())
def test_example_md_files_validate_with_strict_bounds(md_file: Path, capsys: pytest.CaptureFixture) -> None:
    """
    Each Markdown example should:
      - contain YAML front-matter that maps to a known schema type
      - pass jsonschema validation
      - pass bounds checks (or emit only warnings if a bound is intentionally absent)
    """
    # Run the validator as a function to keep tests fast (no subprocess)
    rc = validate_proposal.main([
        str(md_file),
        "--schemas-dir", str(SCHEMAS),
        "--bounds", str(REGISTRIES / "params_bounds.json"),
        "--strict",
    ])
    captured = capsys.readouterr()
    # Parse the JSON report to surface detailed failures
    try:
        report = json.loads(captured.out or "{}")
    except Exception:
        report = {}
    assert rc == 0, f"Validator failed for {md_file}:\n{captured.out}\n{captured.err}"


def test_sample_ballots_pass_quorum_and_approval(capsys: pytest.CaptureFixture) -> None:
    """
    Tally the example ballots with realistic rules and ensure the example passes.
    Uses:
      - proposalId: GOV-2025-11-VM-OPC-01
      - chainId: 2
      - snapshot: height 1234567
      - window: includes the sample ballot timestamp
      - quorum 10%, approval 66.7%
      - eligible power set to 1 so a single yes vote meets thresholds
    """
    ballots_dir = EXAMPLES / "ballots"
    assert ballots_dir.exists(), f"Missing ballots dir: {ballots_dir}"

    rc = tally_votes.main([
        "--ballots-dir", str(ballots_dir),
        "--proposal-id", "GOV-2025-11-VM-OPC-01",
        "--eligible-power", "1.0",
        "--quorum-percent", "10.0",
        "--approval-threshold-percent", "66.7",
        "--chain-id", "2",
        "--snapshot-type", "height",
        "--snapshot-value", "1234567",
        "--reject-outside-window",
        "--window-start", "2025-10-31T00:00:00Z",
        "--window-end",   "2025-11-07T23:59:59Z",
        "--pretty",
    ])
    captured = capsys.readouterr()
    assert rc == 0, f"Tally script exited with {rc}:\n{captured.out}\n{captured.err}"

    # Parse report and assert outcome
    report = json.loads(captured.out)
    assert report["evaluation"]["outcome"] == "passed", f"Outcome not passed:\n{json.dumps(report, indent=2)}"
    assert report["evaluation"]["quorumMet"] is True
    assert report["evaluation"]["approvalMet"] is True

    # With eligible-power=1 and a single 'yes' ballot of weight 1:
    assert report["counts"]["yes"].startswith("1.000000")
    assert float(report["metrics"]["participationPercent"]) == pytest.approx(100.0, rel=0, abs=1e-6)
    assert float(report["metrics"]["approvalPercent"]) == pytest.approx(100.0, rel=0, abs=1e-6)
