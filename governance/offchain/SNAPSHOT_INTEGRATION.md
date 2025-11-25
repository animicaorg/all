# SNAPSHOT INTEGRATION
_Optional off-chain signaling for proposals (informative, not binding)_

**Version:** 1.0  
**Status:** Active (optional feature)  
**Scope:** Using Snapshot (or compatible) off-chain voting to gather community sentiment and delegate input for proposals that will ultimately be decided **on-chain**.

Related: `governance/ops/snapshots/` (manifests), `governance/scripts/tally_votes.py`, `governance/policies/TRANSPARENCY.md`, `governance/diagrams/GOVERNANCE_FLOW.mmd`.

---

## 1) Purpose

Snapshot-style votes provide:
- **Early signal** on contentious changes (VM opcodes, PQ rotations, params).  
- **Broader participation** with low friction (no gas/signing fees).  
- **Documentation** of discussion links & alternatives prior to scheduling an on-chain vote.

> Off-chain results are **advisory**. On-chain governance (ballots per schema) is the source of truth.

---

## 2) Supported Platform(s)

- **Snapshot** (https://snapshot.org) via a project space (e.g., `animica.eth` or custom ENS).
- Compatible forks are acceptable if they export the standard `spaces`, `proposals`, `votes` JSON.

---

## 3) Standard Workflow

1. **Draft Proposal** in the repo with machine-readable header & risk docs.  
2. **Open Snapshot Vote** with a link back to the PR and a canonical title **matching the proposal ID** (`GOV-YYYY-MM-SHORT`).  
3. **Archive Manifest** for the Snapshot vote to `governance/ops/snapshots/` (see §4).  
4. **Run Signal Tally** locally (optional) and post a **signal summary** in the PR.  
5. **Schedule On-Chain Vote** only after stewards sign off on bounds & risk gates.  
6. **Publish Minutes** referencing both Snapshot result and on-chain outcome.

---

## 4) Snapshot Manifest (Repository Record)

Each off-chain signal must have a JSON manifest checked in to this repo. Store at:

governance/ops/snapshots/<proposal-id>.snapshot.json

pgsql
Copy code

**Schema (minimal):**
```json
{
  "schemaVersion": "1.0",
  "platform": "snapshot",
  "space": "animica.eth",
  "proposalId": "GOV-2025-11-VM-OPC-01",
  "snapshotUrl": "https://snapshot.org/#/animica.eth/proposal/0xabcdef...",
  "start": "2025-11-03T17:00:00Z",
  "end": "2025-11-10T17:00:00Z",
  "strategy": {
    "id": "erc20-balance-of",
    "network": "1",
    "address": "0xTokenOrAdapterAddress",
    "params": { "decimals": 18, "symbol": "ANM" }
  },
  "metadata": {
    "title": "Activate VM opcode: OP_BLAKE3",
    "discussion": "https://github.com/animica/animica/pull/1234",
    "choices": ["Yes", "No", "Abstain"]
  },
  "results": {
    "yes": 0,
    "no": 0,
    "abstain": 0,
    "totalVotingPower": 0,
    "quorumHint": 0
  },
  "checksums": {
    "exportVotesJsonSha256": null
  }
}
Keep results zeroed initially; update after exporting from Snapshot. checksums.exportVotesJsonSha256 should be the SHA-256 of the raw votes export for reproducibility.

5) Exporting & Verifying Votes
5.1 Export
Use Snapshot’s UI/API to export votes (JSON). Save to:

bash
Copy code
governance/ops/snapshots/<proposal-id>.votes.json
5.2 Verify Integrity
Compute and store a checksum:

bash
Copy code
shasum -a 256 governance/ops/snapshots/GOV-2025-11-VM-OPC-01.votes.json | awk '{print $1}' \
  > governance/ops/snapshots/GOV-2025-11-VM-OPC-01.votes.sha256
Update the manifest’s checksums.exportVotesJsonSha256 to match.

5.3 Summarize Results
A tiny helper script (optional) can summarize votes to fill the results block. Example Python one-liner:

bash
Copy code
python - <<'PY'
import json, sys, decimal
pid="GOV-2025-11-VM-OPC-01"
votes=json.load(open(f"governance/ops/snapshots/{pid}.votes.json"))
yes=no=abst=total=decimal.Decimal(0)
for v in votes:
    p=decimal.Decimal(str(v.get("vp",0)))
    c=v.get("choice")
    total+=p
    if isinstance(c,str): c=c.lower()
    if c in (1,"yes"): yes+=p
    elif c in (2,"no"): no+=p
    else: abst+=p
man=json.load(open(f"governance/ops/snapshots/{pid}.snapshot.json"))
man["results"].update({
  "yes": float(yes), "no": float(no), "abstain": float(abst),
  "totalVotingPower": float(total), "quorumHint": float(total)
})
json.dump(man, open(f"governance/ops/snapshots/{pid}.snapshot.json","w"), indent=2)
print("Updated manifest results.")
PY
quorumHint is purely informational; the on-chain quorum rules still apply.

6) Mapping to On-Chain Governance
Identity: Off-chain addresses may not equal Animica bech32m addresses. Treat Snapshot as advisory, not an eligibility gate.

Text Choices → On-chain Options: Map Yes/No/Abstain to vote=yes|no|abstain for guidance only.

Quorum/Threshold: Never substitute Snapshot quorum for on-chain gov.vote.* params.

Conflicts: If off-chain signal contradicts risk rules or bounds, stewards MUST default to safety and proceed with on-chain process only.

7) Strategies & Voting Power
Recommended Snapshot strategies for signaling:

erc20-balance-of for mirrored ANM representations (if bridged/wrapped).

delegation to reflect social delegates.

multistrategy to combine weights (document composition clearly).

Document strategy details in the manifest. If wrapping/mirroring is used, link to the contract, indexer, or adapter.

8) Security & Integrity
Pinning: Save exports & manifests in-repo; optional IPFS pin of the raw export hash.

Replay Risk: Off-chain identities are not 1:1 with chain accounts; never use Snapshot signatures as transaction authorization.

Sybil Resistance: Off-chain signals are susceptible to Sybil attacks; treat as advice.

Transparency: Link discussion, PRs, and evidence. Redactions follow TRANSPARENCY.md.

9) Presentation & Communications
When announcing an off-chain signal:

Include proposal ID, brief summary, and clear non-binding disclaimer.

State start/end times (UTC) and strategy.

After close, post result summary with checksum of the export and a link to the manifest.

Suggested disclaimer:

“This Snapshot vote is advisory only. The binding decision will occur on-chain under Animica’s governance rules.”

10) Example Manifest Pair
governance/ops/snapshots/GOV-2025-11-VM-OPC-01.snapshot.json

governance/ops/snapshots/GOV-2025-11-VM-OPC-01.votes.json (+ .sha256)

11) Optional: Bridge to On-Chain Metadata
Proposals may include a link to the Snapshot manifest in their header (non-binding):

yaml
Copy code
offchain:
  snapshot:
    manifest: governance/ops/snapshots/GOV-2025-11-VM-OPC-01.snapshot.json
    checksum: <sha256 of votes export>
Tooling MUST NOT ingest off-chain votes into binding tallies; this link is for display only (explorer, docs).

12) Minimal QA Checklist
 Snapshot title includes proposal ID.

 Strategy documented and reproducible.

 Votes export saved + checksum recorded.

 Manifest committed; results updated.

 PR updated with link and short summary.

 On-chain vote remains scheduled per calendar.

13) Decommissioning / Archival
After the on-chain vote:

Label the Snapshot manifest with the on-chain outcome:

json
Copy code
"results": { "...": "..." },
"onchainOutcome": "approved"   // or "rejected" / "superseded"
Keep files under version control for traceability; update links in Transparency minutes.

14) FAQ
Can Snapshot replace deposits or bounds checks? No. Deposits and bounds apply only to on-chain proposals.

Can we gate on-chain voting power based on Snapshot? No. Only on-chain registries and snapshots define eligibility.

Can abstain win? Signal-only; on-chain rules decide.

15) Change Log
1.0 (2025-10-31): Initial Snapshot integration guide, manifest schema, export workflow, and security notes.

