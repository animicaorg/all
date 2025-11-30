#!/usr/bin/env python3
"""
generate_ballot.py — produce ballot JSON from a proposal's header/front-matter.

Usage:
  python governance/scripts/generate_ballot.py <path/to/proposal.{json|yaml|yml|md}>
      [--chain-id 2]
      [--voter-id anim1...]
      [--pubkey 0x...]
      [--pubkey-type ed25519]
      [--choice yes|no|abstain|veto]
      [--weight 1.0]
      [--snapshot-type height|timestamp]
      [--snapshot-value 1234567|2025-10-31T00:00:00Z]
      [--client "animica-wallet/0.0.0"]
      [--network mainnet|testnet|localnet]
      [--reason "Why I voted"]
      [--out ballots/my_ballot.json]
      [--pretty]
      [--now "2025-10-31T16:00:00Z"]  # deterministic clock for CI

What it does:
• Reads the proposal payload (JSON, YAML, or Markdown with YAML front-matter).
• Extracts key fields (proposalId, votingPeriodDays) and creates a ballot JSON
  that adheres to governance/schemas/ballot.schema.json (shape-wise).
• Fills reasonable defaults and placeholders if CLI flags are omitted.
• Prints to stdout by default, or writes to --out.

Notes:
• This script does not sign the ballot. Use your wallet/CLI to sign the
  produced JSON (attestation fields remain placeholders).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

try:
    import yaml  # PyYAML
except Exception:
    print("ERROR: PyYAML is required. pip install pyyaml", file=sys.stderr)
    sys.exit(4)

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*$", re.DOTALL | re.MULTILINE)


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _load_payload(path: Path) -> Dict[str, Any]:
    text = _read_text(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    if suffix == ".md":
        m = FRONT_MATTER_RE.search(text)
        if not m:
            raise RuntimeError(
                f"{path} looks like Markdown but has no YAML front-matter delimited by '---' lines."
            )
        return yaml.safe_load(m.group(1)) or {}
    raise RuntimeError(f"Unsupported proposal extension: {path.name}")


def _iso_now(now_override: Optional[str]) -> datetime:
    if now_override:
        # Accept both 'Z' and '+00:00' variants
        s = (
            now_override.strip().replace("Z", "+00:00")
            if now_override.endswith("Z")
            else now_override
        )
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    return datetime.now(tz=timezone.utc)


def _to_iso(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_ballot(
    proposal: Dict[str, Any],
    chain_id: int,
    voter_id: str,
    pubkey: str,
    pubkey_type: str,
    choice: str,
    weight: str,
    snapshot_type: str,
    snapshot_value: str,
    client: str,
    network: str,
    reason: Optional[str],
    now_iso: Optional[str],
) -> Dict[str, Any]:
    # Extract proposalId (prefer explicit id; fallback to title slug)
    proposal_id = str(
        proposal.get("id") or proposal.get("proposal", {}).get("id") or ""
    ).strip()
    if not proposal_id:
        title = str(
            proposal.get("title")
            or proposal.get("proposal", {}).get("title")
            or "UNKNOWN"
        ).strip()
        safe = re.sub(r"[^A-Za-z0-9\-]+", "-", title).strip("-")
        proposal_id = f"UNKNOWN-{safe[:32]}"

    # Voting window (informational only, not required by ballot schema)
    now = _iso_now(now_iso)
    period_days = None
    # Accept either top-level "voting" or proposal.voting
    voting = proposal.get("voting") or proposal.get("proposal", {}).get("voting") or {}
    try:
        period_days = int(voting.get("votingPeriodDays"))
    except Exception:
        period_days = None
    window = {
        "start": _to_iso(now),
        "end": _to_iso(now + timedelta(days=period_days)) if period_days else None,
    }

    # Snapshot default if not provided
    if snapshot_type == "height":
        try:
            int(snapshot_value)  # validate numeric
        except Exception:
            snapshot_value = "0"  # placeholder
    else:
        # timestamp snapshot — ensure ISO string
        try:
            _ = _iso_now(snapshot_value)  # parse/validate
        except Exception:
            snapshot_value = _to_iso(now)

    ballot = {
        "formatVersion": "1.0",
        "ballotId": f"BAL-{now.strftime('%Y%m%d')}-{uuid4().hex[:8]}",
        "proposalId": proposal_id,
        "chainId": chain_id,
        "snapshot": {
            "type": snapshot_type,
            "value": snapshot_value,
        },
        "voter": {
            "identity": voter_id
            or "anim1__________________________________placeholder",
            "pubkey": pubkey or "0x" + "00" * 33,
            "pubkeyType": pubkey_type,
        },
        "vote": {
            "choice": choice,
            "weight": f"{float(weight):.6f}",
            "reason": (reason or "").strip(),
        },
        # Attestation left blank for wallets/CLIs to fill/sign
        "attestation": {
            "sigAlg": pubkey_type,
            "signature": "",
            "signedPayload": {
                "domain": "animica.gov/ballot",
                "version": "1",
                "timestamp": _to_iso(now),
                "nonce": "0x" + uuid4().hex[:10],
            },
        },
        "metadata": {
            "client": client,
            "network": network,
            "statement": (reason or "").strip(),
            "contact": "",
            "votingWindow": {k: v for k, v in window.items() if v},
        },
    }
    return ballot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate a ballot JSON from a proposal header/front-matter."
    )
    ap.add_argument("proposal", help="Path to proposal file (.json|.yaml|.yml|.md)")
    ap.add_argument("--chain-id", type=int, default=2)
    ap.add_argument("--voter-id", default="")
    ap.add_argument("--pubkey", default="")
    ap.add_argument(
        "--pubkey-type",
        default="ed25519",
        choices=["ed25519", "secp256k1", "dilithium3"],
    )
    ap.add_argument("--choice", default="yes", choices=["yes", "no", "abstain", "veto"])
    ap.add_argument("--weight", default="1.0")
    ap.add_argument(
        "--snapshot-type", default="height", choices=["height", "timestamp"]
    )
    ap.add_argument("--snapshot-value", default="0")
    ap.add_argument("--client", default="animica-wallet/0.0.0")
    ap.add_argument(
        "--network", default="testnet", choices=["mainnet", "testnet", "localnet"]
    )
    ap.add_argument("--reason", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument(
        "--now",
        default=None,
        help="Override clock (ISO8601). Useful for deterministic CI.",
    )
    args = ap.parse_args(argv)

    try:
        proposal = _load_payload(Path(args.proposal))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    ballot = build_ballot(
        proposal=proposal,
        chain_id=args.chain_id,
        voter_id=args.voter_id,
        pubkey=args.pubkey,
        pubkey_type=args.pubkey_type,
        choice=args.choice,
        weight=args.weight,
        snapshot_type=args.snapshot_type,
        snapshot_value=args.snapshot_value,
        client=args.client,
        network=args.network,
        reason=args.reason,
        now_iso=args.now,
    )

    out_text = json.dumps(ballot, indent=2 if args.pretty else None)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            out_text + ("\n" if not out_text.endswith("\n") else ""), encoding="utf-8"
        )
        print(f"Wrote {out_path}")
    else:
        print(out_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
