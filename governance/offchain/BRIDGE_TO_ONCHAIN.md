# BRIDGE TO ON-CHAIN
_Mapping off-chain Snapshot signals → on-chain governance transactions (non-binding)_

**Version:** 1.0  
**Status:** Active (optional feature)  
**Scope:** Document the *transparent* linkage between advisory Snapshot signals and the **binding** on-chain proposal + ballot + tally flow.

Related: `governance/offchain/SNAPSHOT_INTEGRATION.md`, `governance/scripts/tally_votes.py`, `governance/scripts/generate_ballot.py`, `governance/scripts/validate_proposal.py`, `governance/diagrams/GOVERNANCE_FLOW.mmd`.

> **Important:** Off-chain signals never substitute for on-chain quorum/threshold rules or eligibility. They are metadata only.

---

## 1) Canonical Mapping Model

We maintain a **manifest pair** per proposal ID:

governance/ops/snapshots/<ID>.snapshot.json # metadata about the Snapshot vote (space, strategy, urls)
governance/ops/snapshots/<ID>.votes.json # raw exported votes (unchanged)

nginx
Copy code

The on-chain artifacts are:

governance/examples/ballots/<ID>.json # example ballot (structure)
governance/examples/tallies/<ID>.json # example tally (structure)
chain txids # produced when the binding vote runs on-chain

python
Copy code

**Linkage is by `proposalId` ONLY.** The manifest `proposalId` must equal the proposal header `id:` in the repo.

---

## 2) Minimal Snapshot Manifest (recap)

See `SNAPSHOT_INTEGRATION.md §4`. Required keys:

```json
{
  "schemaVersion": "1.0",
  "platform": "snapshot",
  "space": "animica.eth",
  "proposalId": "GOV-2025-11-VM-OPC-01",
  "snapshotUrl": "https://snapshot.org/#/animica.eth/proposal/0xabc...",
  "start": "2025-11-03T17:00:00Z",
  "end": "2025-11-10T17:00:00Z",
  "strategy": { "id": "erc20-balance-of", "network": "1", "address": "0x...", "params": { "decimals": 18, "symbol": "ANM" } },
  "metadata": { "title": "Activate VM opcode: OP_BLAKE3", "discussion": "https://github.com/animica/..." },
  "results": { "yes": 0, "no": 0, "abstain": 0, "totalVotingPower": 0, "quorumHint": 0 },
  "checksums": { "exportVotesJsonSha256": "<sha256-or-null>" }
}
3) Repository-Level Cross-References
Add the following optional block to the proposal file’s YAML header to make UIs link both ways:

yaml
Copy code
offchain:
  snapshot:
    manifest: governance/ops/snapshots/GOV-2025-11-VM-OPC-01.snapshot.json
    votesExport: governance/ops/snapshots/GOV-2025-11-VM-OPC-01.votes.json
    checksum: <sha256 of votes export>
Tooling MUST treat this as display-only.

4) Local Reproducibility: Checksums & Freezing
Export votes JSON from Snapshot (UI/API).

Compute and record checksum:

bash
Copy code
shasum -a 256 governance/ops/snapshots/GOV-2025-11-VM-OPC-01.votes.json | awk '{print $1}' \
  > governance/ops/snapshots/GOV-2025-11-VM-OPC-01.votes.sha256
Paste the same hash into the manifest’s checksums.exportVotesJsonSha256.

This allows a reviewer to verify the export matches the manifest.

5) Optional Signal → Comment Summary
You may summarize the off-chain signal in the proposal PR description:

yaml
Copy code
Advisory Signal (Snapshot):
- Yes: 62.1% (123.4k VP)
- No:  33.7% (67.0k VP)
- Abs: 4.2%  (8.4k VP)
Export hash: <sha256>; Manifest: governance/ops/snapshots/<ID>.snapshot.json
This is informative. The binding vote is still executed on-chain.

6) Mapping Choices & Identities
Choices: Map Snapshot "Yes/No/Abstain" to labels used by on-chain ballots. No numerical weight is imported.

Identities: Snapshot accounts (e.g., EVM addresses) are not Animica bech32m by default; do not derive on-chain eligibility from them.

Conflicts: If Snapshot suggests risk-increasing changes that violate bounds, stewards must defer to PARAMS_BOUNDARIES.md.

7) Optional: Programmatic Link Builders
For explorers/website components, derive URLs from manifests:

json
Copy code
{
  "links": {
    "snapshot": "https://snapshot.org/#/animica.eth/proposal/0xabc...",
    "discussion": "https://github.com/animica/.../pull/1234",
    "onchain": {
      "proposalPage": "animica://gov/GOV-2025-11-VM-OPC-01",
      "tallyJson": "governance/examples/tallies/GOV-2025-11-VM-OPC-01.json"
    }
  }
}
8) CLI Snippets (helpers)
Validate proposal (schema + bounds + deltas):

bash
Copy code
python governance/scripts/validate_proposal.py path/to/proposal.md --strict --pretty
Generate ballot JSON from proposal header:

bash
Copy code
python governance/scripts/generate_ballot.py path/to/proposal.md \
  --out governance/examples/ballots/GOV-2025-11-VM-OPC-01.json
Tally on-chain ballots (binding) to JSON:

bash
Copy code
python governance/scripts/tally_votes.py \
  --ballots-dir governance/ops/ballots \
  --proposal-id GOV-2025-11-VM-OPC-01 \
  --out governance/examples/tallies/GOV-2025-11-VM-OPC-01.json \
  --pretty
None of these commands ingest Snapshot votes. They operate solely on on-chain ballots.

9) UI / Explorer Recommendations
Display the Snapshot card with Advisory badge and the export checksum.

Cross-link the on-chain proposal/tally/activation height.

If the Snapshot signal and on-chain outcome differ, show a neutral message:
“On-chain outcome is authoritative; see risk rationale and bounds check.”

10) Security Notes
Never accept Snapshot signatures as transaction authorization.

Keep manifests and exports immutable once the on-chain vote is scheduled (only add onchainOutcome fields post-facto).

Treat Snapshot quorum/strategy as context only; record it for auditability.

11) Post-Vote Archival
After the on-chain vote concludes, append to the manifest:

json
Copy code
{
  "onchainOutcome": "approved",
  "onchain": {
    "tallyJson": "governance/examples/tallies/GOV-2025-11-VM-OPC-01.json",
    "voteOpen": "2025-11-12T17:00:00Z",
    "voteClose": "2025-11-19T17:00:00Z",
    "activation": { "height": 1234567, "timestamp": "2025-11-26T17:00:00Z", "txid": "0x..." }
  }
}
Keep both the advisory Snapshot files and the binding on-chain artifacts under version control.

12) FAQ
Q: Can Snapshot power unlock on-chain ballots?
A: No. Eligibility and quorum are defined by Animica’s on-chain registries and snapshots (height/time).

Q: Can we auto-mirror Snapshot results on-chain?
A: No. Any automation must stop at publishing comments/labels; decisions remain on-chain.

Q: What if Snapshot is attacked/Sybil’d?
A: That’s why it’s advisory. Record the export and continue with standard governance.

13) Change Log
1.0 (2025-10-31): Initial bridge model, CLI snippets, UI guidance, and archival fields.
