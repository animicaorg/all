#!/usr/bin/env python3
"""
chains/scripts/validate.py — validate Animica chain metadata against JSON Schemas.

Usage:
  python chains/scripts/validate.py
  python chains/scripts/validate.py --all
  python chains/scripts/validate.py --registry chains/registry.json
  python chains/scripts/validate.py --chains chains/animica.testnet.json chains/animica.localnet.json
  python chains/scripts/validate.py --fail-on-warn

Exit codes:
  0 = all validations passed
  1 = validation errors found or unexpected exception occurred
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Tuple

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except Exception as e:  # pragma: no cover
    sys.stderr.write(
        "ERROR: jsonschema is required. Install with:\n  pip install jsonschema\n"
    )
    raise

ROOT = Path(__file__).resolve().parents[2]  # repo root
SCHEMAS_DIR = ROOT / "chains" / "schemas"
CHAIN_SCHEMA_PATH = SCHEMAS_DIR / "chain.schema.json"
REGISTRY_SCHEMA_PATH = SCHEMAS_DIR / "registry.schema.json"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e


def compile_validator(schema_path: Path) -> Draft202012Validator:
    schema = load_json(schema_path)
    return Draft202012Validator(schema)


def validate_one(validator: Draft202012Validator, data: Any, label: str) -> List[str]:
    """Return a list of human-readable error strings for this document."""
    errors = []
    for err in validator.iter_errors(data):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{label}: {loc}: {err.message}")
    return errors


def find_chain_files_from_registry(registry_json: dict) -> List[Path]:
    entries = registry_json.get("entries", [])
    out: List[Path] = []
    for e in entries:
        p = ROOT / e.get("path", "")
        out.append(p)
    return out


def glob_chain_files() -> List[Path]:
    return sorted((ROOT / "chains").glob("*.json"))


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Validate Animica chains JSON files against schemas."
    )
    ap.add_argument(
        "--registry",
        default=str(ROOT / "chains" / "registry.json"),
        help="Path to registry.json (default: chains/registry.json)",
    )
    ap.add_argument(
        "--chains",
        nargs="*",
        default=[],
        help="Specific chain JSON files to validate. If omitted, uses registry entries or --all glob.",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Validate all chains/*.json found on disk (in addition to the registry).",
    )
    ap.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="(Reserved) Treat warnings as errors (currently no warnings emitted).",
    )
    args = ap.parse_args(argv)

    errors: List[str] = []
    warnings: List[str] = []

    # Compile validators
    if not CHAIN_SCHEMA_PATH.exists():
        errors.append(f"Missing schema: {CHAIN_SCHEMA_PATH}")
    if not REGISTRY_SCHEMA_PATH.exists():
        errors.append(f"Missing schema: {REGISTRY_SCHEMA_PATH}")
    if errors:
        for e in errors:
            sys.stderr.write(f"ERROR: {e}\n")
        return 1

    chain_validator = compile_validator(CHAIN_SCHEMA_PATH)
    registry_validator = compile_validator(REGISTRY_SCHEMA_PATH)

    # Validate registry.json
    reg_path = Path(args.registry)
    if not reg_path.exists():
        errors.append(f"Registry file not found: {reg_path}")
        # Continue; we can still validate explicit --chains/--all
    else:
        try:
            reg = load_json(reg_path)
            errors.extend(validate_one(registry_validator, reg, str(reg_path)))
        except Exception as e:
            errors.append(f"{reg_path}: {e}")
            reg = None  # type: ignore

    # Collect chain files to validate
    chain_paths: List[Path] = []
    if args.chains:
        chain_paths.extend([Path(p) for p in args.chains])
    elif reg_path.exists():
        try:
            reg = load_json(reg_path)
            chain_paths.extend(find_chain_files_from_registry(reg))
        except Exception:
            pass  # already reported
    if args.all:
        # Merge with on-disk glob (dedupe)
        extra = set(glob_chain_files())
        chain_paths = sorted(set(chain_paths).union(extra))

    # Basic presence checks & validate each chain JSON
    for p in chain_paths:
        if not p.exists():
            errors.append(f"Missing chain file referenced: {p}")
            continue
        try:
            data = load_json(p)
        except Exception as e:
            errors.append(str(e))
            continue

        # Schema validation
        errs = validate_one(chain_validator, data, str(p))
        errors.extend(errs)

        # Light consistency checks
        # 1) embedded checksum looks like a 64-hex or placeholder
        checksum = data.get("checksum")
        if isinstance(checksum, str):
            if (
                checksum
                != "0000000000000000000000000000000000000000000000000000000000000000"
                and not (
                    len(checksum) == 64
                    and all(c in "0123456789abcdef" for c in checksum.lower())
                )
            ):
                warnings.append(
                    f"{p}: checksum is not a 64-hex string (got: {checksum!r})"
                )
        else:
            warnings.append(f"{p}: checksum field missing or not a string")

        # 2) network/testnet coherence
        net = data.get("network")
        testnet = data.get("testnet")
        if net == "mainnet" and testnet is True:
            warnings.append(f"{p}: mainnet should not set testnet=true")
        if net in ("testnet", "localnet") and testnet is False:
            warnings.append(f"{p}: {net} should usually set testnet=true")

        # 3) chainId sanity
        cid = data.get("chainId")
        if isinstance(cid, int) and cid < 1:
            errors.append(f"{p}: chainId must be >= 1 (got {cid})")

    # Print results
    if errors:
        sys.stderr.write("\n== Validation Errors ==\n")
        for e in errors:
            sys.stderr.write(f"- {e}\n")

    if warnings:
        sys.stderr.write("\n== Warnings ==\n")
        for w in warnings:
            sys.stderr.write(f"- {w}\n")

    if errors or (warnings and args.fail_on_warn):
        return 1
    print("All validations passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
