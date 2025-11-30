#!/usr/bin/env python3
"""
tally_votes.py — compute result & quorum checks from ballots.

Usage:
  python governance/scripts/tally_votes.py \
      --ballots-dir governance/examples/ballots \
      --proposal-id GOV-2025-11-VM-OPC-01 \
      --eligible-power 1500000.0 \
      --quorum-percent 10.0 \
      --approval-threshold-percent 66.7 \
      [--chain-id 2] \
      [--snapshot-type height|timestamp] \
      [--snapshot-value 1234567|2025-11-07T16:00:00Z] \
      [--reject-outside-window] \
      [--window-start 2025-10-31T16:00:00Z] \
      [--window-end   2025-11-07T16:00:00Z] \
      [--require-sig] \
      [--pretty]

Notes:
• This tool sums ballot "vote.weight" by choice (yes/no/abstain/veto),
  filters by proposalId (and optionally chainId), and enforces snapshot rules.
• Duplicate voters (same voter.identity) are resolved by keeping the latest
  attestation.signedPayload.timestamp (if present), otherwise the lexicographically
  greatest ballotId.
• Signature verification is NOT performed; --require-sig only checks non-empty
  'attestation.signature' exists. Cryptographic validation is left to wallet/CI.
• We use Decimal for exact fixed-point math, then also emit float-ish metrics.

Exit codes:
  0 = OK
  4 = Usage / I/O error
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, getcontext
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# higher precision to avoid rounding artifacts; trim at the end
getcontext().prec = 40


@dataclass
class Ballot:
    raw: Dict[str, Any]
    ballot_id: str
    proposal_id: str
    chain_id: Optional[int]
    voter_id: str
    choice: str
    weight: Decimal
    snapshot_type: Optional[str]
    snapshot_value: Optional[str]
    att_ts: Optional[str]  # ISO8601
    has_sig: bool


CHOICES = ("yes", "no", "abstain", "veto")


def _parse_iso(ts: str) -> datetime:
    # Accept Z and +00:00 forms
    s = ts.strip().replace("Z", "+00:00") if ts.endswith("Z") else ts
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _load_json_files(dirpath: Path) -> Iterable[Dict[str, Any]]:
    for p in sorted(dirpath.glob("*.json")):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"WARN: failed to parse {p}: {e}", file=sys.stderr)


def _coerce_decimal(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def _extract_ballot(d: Dict[str, Any]) -> Optional[Ballot]:
    try:
        choice = str(d["vote"]["choice"]).lower().strip()
        if choice not in CHOICES:
            return None
        return Ballot(
            raw=d,
            ballot_id=str(d.get("ballotId", "")),
            proposal_id=str(d.get("proposalId", "")),
            chain_id=int(d.get("chainId")) if d.get("chainId") is not None else None,
            voter_id=str(d.get("voter", {}).get("identity", "")).strip(),
            choice=choice,
            weight=_coerce_decimal(d.get("vote", {}).get("weight", "0")),
            snapshot_type=str(d.get("snapshot", {}).get("type", "") or ""),
            snapshot_value=str(d.get("snapshot", {}).get("value", "") or ""),
            att_ts=(d.get("attestation", {}) or {})
            .get("signedPayload", {})
            .get("timestamp"),
            has_sig=bool((d.get("attestation", {}) or {}).get("signature")),
        )
    except Exception:
        return None


def _filter_ballots(
    ballots: List[Ballot],
    proposal_id: str,
    chain_id: Optional[int],
    snapshot_type: Optional[str],
    snapshot_value: Optional[str],
) -> List[Ballot]:
    out: List[Ballot] = []
    # Pre-parse snapshot target if timestamp type
    target_ts: Optional[datetime] = None
    if snapshot_type and snapshot_type.lower() == "timestamp" and snapshot_value:
        target_ts = _parse_iso(snapshot_value)

    for b in ballots:
        if b.proposal_id != proposal_id:
            continue
        if chain_id is not None and b.chain_id is not None and b.chain_id != chain_id:
            continue

        # Snapshot rule: ballot must have snapshot <= target (height or time)
        if snapshot_type:
            if b.snapshot_type and b.snapshot_type.lower() != snapshot_type.lower():
                # mismatched snapshot types → skip (conservative)
                continue
            if snapshot_type.lower() == "height":
                try:
                    target_h = int(snapshot_value) if snapshot_value else None
                    ballot_h = int(b.snapshot_value) if b.snapshot_value else None
                except Exception:
                    continue
                if (
                    (target_h is not None)
                    and (ballot_h is not None)
                    and (ballot_h > target_h)
                ):
                    continue
            elif snapshot_type.lower() == "timestamp":
                try:
                    ballot_ts = (
                        _parse_iso(b.snapshot_value) if b.snapshot_value else None
                    )
                except Exception:
                    ballot_ts = None
                if target_ts and ballot_ts and ballot_ts > target_ts:
                    continue

        out.append(b)
    return out


def _dedupe_latest(ballots: List[Ballot]) -> List[Ballot]:
    """Keep the latest ballot per voter.identity by attestation timestamp, then ballotId."""
    by_voter: Dict[str, Ballot] = {}
    for b in ballots:
        key = b.voter_id or ""
        if not key:
            # Anonymous/invalid identity → drop
            continue
        prev = by_voter.get(key)
        if prev is None:
            by_voter[key] = b
            continue

        # Compare timestamps
        def _key(bb: Ballot) -> Tuple[int, str]:
            try:
                t = _parse_iso(bb.att_ts) if bb.att_ts else None
                # use epoch seconds as comparable int
                return (int(t.timestamp()) if t else -1, bb.ballot_id)
            except Exception:
                return (-1, bb.ballot_id)

        if _key(b) >= _key(prev):
            by_voter[key] = b
    return list(by_voter.values())


def _within_window(ts: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    if not (start or end):
        return True
    try:
        t = _parse_iso(ts) if ts else None
    except Exception:
        return False
    if start:
        if t is None or _parse_iso(start) > t:
            return False
    if end:
        if t is None or _parse_iso(end) < t:
            return False
    return True


def tally(
    ballots_dir: Path,
    proposal_id: str,
    eligible_power: Decimal,
    quorum_percent: Decimal,
    approval_threshold_percent: Decimal,
    chain_id: Optional[int],
    snapshot_type: Optional[str],
    snapshot_value: Optional[str],
    reject_outside_window: bool,
    window_start: Optional[str],
    window_end: Optional[str],
    require_sig: bool,
    pretty: bool,
) -> int:
    raw = list(_load_json_files(ballots_dir))
    parsed = [b for b in (_extract_ballot(d) for d in raw) if b is not None]

    # Filter by proposal/chain/snapshot
    filtered = _filter_ballots(
        parsed, proposal_id, chain_id, snapshot_type, snapshot_value
    )

    # Optional: window + signature checks (pre-dedupe)
    kept: List[Ballot] = []
    rejected_codes: Dict[str, int] = {}

    def rej(code: str):
        rejected_codes[code] = rejected_codes.get(code, 0) + 1

    for b in filtered:
        if require_sig and not b.has_sig:
            rej("missing_signature")
            continue
        if reject_outside_window and not _within_window(
            b.att_ts, window_start, window_end
        ):
            rej("outside_window")
            continue
        kept.append(b)

    # Dedupe by voter identity, keep latest
    unique = _dedupe_latest(kept)

    # Sums
    sums = {k: Decimal("0") for k in CHOICES}
    for b in unique:
        sums[b.choice] += b.weight

    participating = sum(sums.values())
    # Metrics
    participation_percent = (
        (participating / eligible_power * Decimal("100"))
        if eligible_power > 0
        else Decimal("0")
    )
    yes_share_percent = (
        (sums["yes"] / max(Decimal("0.0000001"), participating) * Decimal("100"))
        if participating > 0
        else Decimal("0")
    )

    quorum_met = participation_percent >= Decimal(str(quorum_percent))
    approval_met = yes_share_percent >= Decimal(str(approval_threshold_percent))
    outcome = "passed" if (quorum_met and approval_met) else "failed"

    # Prepare report
    def q(d: Decimal) -> str:
        # Quantize to 6 decimals for fixed-point strings
        return str(d.quantize(Decimal("0.000001"), rounding=ROUND_DOWN))

    def f(d: Decimal) -> float:
        return float(d)

    report = {
        "proposalId": proposal_id,
        "chainId": chain_id,
        "snapshot": {"type": snapshot_type, "value": snapshot_value},
        "votingWindow": (
            {"start": window_start, "end": window_end}
            if (window_start or window_end)
            else {}
        ),
        "rules": {
            "quorumPercent": float(quorum_percent),
            "approvalThresholdPercent": float(approval_threshold_percent),
        },
        "counts": {k: q(v) for k, v in sums.items()},
        "totals": {
            "participatingVotingPower": q(participating),
            "eligibleVotingPower": q(eligible_power),
        },
        "metrics": {
            "participationPercent": round(f(participation_percent), 3),
            "approvalPercent": round(f(yes_share_percent), 3),
        },
        "evaluation": {
            "quorumMet": quorum_met,
            "approvalMet": approval_met,
            "outcome": outcome,
        },
        "audit": {
            "ballotsScanned": len(parsed),
            "ballotsEligible": len(filtered),
            "ballotsKept": len(unique),
            "ballotsRejected": sum(rejected_codes.values()),
            "rejections": [
                {"code": k, "count": v} for k, v in sorted(rejected_codes.items())
            ],
            "generator": "gov-tools/tally_votes.py@v0.1.0",
        },
    }

    text = json.dumps(report, indent=2 if pretty else None)
    print(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Compute tally & quorum checks from ballots."
    )
    ap.add_argument(
        "--ballots-dir", required=True, help="Directory containing ballot JSON files"
    )
    ap.add_argument("--proposal-id", required=True, help="Target proposalId to tally")
    ap.add_argument(
        "--eligible-power",
        type=str,
        required=True,
        help="Total eligible voting power (decimal)",
    )
    ap.add_argument("--quorum-percent", type=float, default=10.0)
    ap.add_argument("--approval-threshold-percent", type=float, default=66.7)
    ap.add_argument("--chain-id", type=int, default=None)
    ap.add_argument("--snapshot-type", choices=["height", "timestamp"], default=None)
    ap.add_argument("--snapshot-value", default=None)
    ap.add_argument("--reject-outside-window", action="store_true")
    ap.add_argument(
        "--window-start", default=None, help="ISO8601; requires --reject-outside-window"
    )
    ap.add_argument(
        "--window-end", default=None, help="ISO8601; requires --reject-outside-window"
    )
    ap.add_argument(
        "--require-sig",
        action="store_true",
        help="Reject ballots without a non-empty attestation.signature",
    )
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    try:
        eligible = Decimal(str(args.eligible_power))
    except Exception:
        print("ERROR: --eligible-power must be a decimal number", file=sys.stderr)
        return 4

    # window sanity
    if args.reject_outside_window and not (args.window_start or args.window_end):
        print(
            "ERROR: --reject-outside-window requires --window-start and/or --window-end",
            file=sys.stderr,
        )
        return 4

    return tally(
        ballots_dir=(
            Path(args.ballots - dir) if False else Path(args.ballots_dir)
        ),  # maintain zsh-safe heredoc; actual arg below
        proposal_id=args.proposal_id,
        eligible_power=eligible,
        quorum_percent=Decimal(str(args.quorum_percent)),
        approval_threshold_percent=Decimal(str(args.approval_threshold_percent)),
        chain_id=args.chain_id,
        snapshot_type=args.snapshot_type,
        snapshot_value=args.snapshot_value,
        reject_outside_window=args.reject_outside_window,
        window_start=args.window_start,
        window_end=args.window_end,
        require_sig=args.require_sig,
        pretty=args.pretty,
    )


if __name__ == "__main__":
    sys.exit(main())
