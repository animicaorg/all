# Governance Templates

Reusable, schema-compatible templates for proposals, ballots, and tallies.  
Use these to draft governance items that validate against the JSON Schemas in `governance/schemas/` and conform to the naming conventions defined in this repository.

---

## What’s here

This folder is meant to hold the following starter files (ask for any you’re missing and we’ll generate them):

- `proposal.md.tmpl` — human-readable Markdown proposal template.
- `proposal.json.tmpl` — base envelope for any proposal (conforms to `proposal.schema.json`).
- `param_change.json.tmpl` — payload for parameter changes (conforms to `param_change.schema.json`).
- `upgrade.json.tmpl` — payload for network upgrades (conforms to `upgrade.schema.json`).
- `pq_rotation.json.tmpl` — payload for PQ key/algorithm rotations (conforms to `pq_rotation.schema.json`).
- `ballot.json.tmpl` — ballot a voter signs/submits (conforms to `ballot.schema.json`).
- `tally.json.tmpl` — finalized tally result (conforms to `tally.schema.json`).

> Schemas live in: `governance/schemas/`. Registries & policy live in: `governance/registries/`.

---

## IDs, slugs & filenames

- **Proposal ID format:** `GOV-YYYY.MM-<ShortSlug>`  
  Examples: `GOV-2026.02-Params-A`, `GOV-2026.03-Upgrade-1.2.0`

- **Working directory layout (suggested):**

governance/proposals/YYYY/MM//
proposal.md
proposal.json
payload.json           # param_change.json / upgrade.json / pq_rotation.json
ballots/               # submitted ballot JSONs (optional mirror)
tally/                 # final tally JSON + signatures

---

## Create a new proposal (quick start)

1. **Copy templates**
 ```bash
 YEAR=2026
 MONTH=02
 SLUG=Params-A
 BASE="governance/proposals/$YEAR/$MONTH/$SLUG"
 mkdir -p "$BASE"
 cp governance/templates/proposal.md.tmpl      "$BASE/proposal.md"
 cp governance/templates/proposal.json.tmpl    "$BASE/proposal.json"
 cp governance/templates/param_change.json.tmpl "$BASE/payload.json"  # choose correct payload template

	2.	Edit fields
	•	In proposal.md: write the human-facing rationale, risk analysis, and rollout plan.
	•	In proposal.json: set proposal_id, proposal_kind, network, window, etc.
	•	In payload.json: fill proposal-specific fields (parameters, upgrade version, or PQ plan).
	3.	Validate JSON against schemas
Using ajv (no project install required):

npx --yes ajv-cli validate \
  -s governance/schemas/proposal.schema.json -d "$BASE/proposal.json" --strict=true

# Validate the payload depending on kind:
npx --yes ajv-cli validate \
  -s governance/schemas/param_change.schema.json -d "$BASE/payload.json" --strict=true


	4.	Canonical JSON & content hash (for reproducibility)
Produce a deterministic hash (SHA3-256) over canonical JSON:

python - <<'PY'



import json,sys,hashlib
p=json.load(open(sys.argv[1]))
c=json.dumps(p, separators=(”,”,”:”), sort_keys=True).encode()
print(“0x”+hashlib.sha3_256(c).hexdigest())
PY “$BASE/proposal.json”

Record this hash in the proposal discussion and commit message.

5. **Optional: sign proposal envelope**
If your process includes author/submitter signatures (e.g., Dilithium3):
```bash
# Example placeholder command; integrate with your key tooling.
animica-sig sign --alg=dilithium3 --in "$BASE/proposal.json" --out "$BASE/proposal.sig.json"


⸻

Submitting ballots
	1.	Copy ballot template

mkdir -p "$BASE/ballots"
cp governance/templates/ballot.json.tmpl "$BASE/ballots/ballot.example.json"


	2.	Fill voter fields
	•	proposal_id must match exactly.
	•	choice ∈ {for,against,abstain}.
	•	Include the voter address, power_at_snapshot (if required by the method), and signature.
	3.	Validate

npx --yes ajv-cli validate \
  -s governance/schemas/ballot.schema.json \
  -d "$BASE/ballots/ballot.example.json" --strict=true



⸻

Tallying
	1.	Start from tally.json.tmpl

mkdir -p "$BASE/tally"
cp governance/templates/tally.json.tmpl "$BASE/tally/tally.json"


	2.	Fill snapshot, window, policy, totals
	•	totals.support_ratio = FOR / (FOR + AGAINST) (string decimal in [0,1]).
	•	Set outcome.passed and reasons (e.g., “Quorum met; Support ≥ threshold”).
	3.	Validate

npx --yes ajv-cli validate \
  -s governance/schemas/tally.schema.json \
  -d "$BASE/tally/tally.json" --strict=true



⸻

Conventions
	•	Numbers as strings: Large integers are encoded as base-10 strings to avoid precision loss.
	•	Ratios: Decimals in [0,1] with up to 18 fractional digits.
	•	Canonicalization: Always jq -S . or the Python canonicalization snippet above before hashing.
	•	Signatures: Prefer PQ algorithms (dilithium3, sphincs_shake_128s) where applicable.

⸻

Useful one-liners
	•	Pretty print & sort keys

jq -S . "$BASE/proposal.json" | tee "$BASE/proposal.canonical.json"


	•	Ensure only known fields (schema-driven)

npx --yes ajv-cli validate --strict=true --all-errors \
  -s governance/schemas/proposal.schema.json \
  -d "$BASE/proposal.json"


	•	Compute & capture hash in file

HASH=$(python - <<'PY' "$BASE/proposal.canonical.json"



import json,sys,hashlib
c=open(sys.argv[1],‘rb’).read()
print(“0x”+hashlib.sha3_256(c).hexdigest())
PY
)
echo “{"proposal_canonical_hash":"$HASH"}” > “$BASE/proposal.hash.json”

---

## Review checklist (maintainers)

- [ ] `proposal.json` & payload validate against schemas.
- [ ] Canonical JSON hash recorded and stable across machines.
- [ ] Ballot template matches the intended tally method & snapshot policy.
- [ ] No extraneous fields; all decimals/ints encoded per schema.
- [ ] Proposal directory adheres to `GOV-YYYY.MM-<Slug>` layout.

---

## Need more templates?

Ask for:
- `policy.json.tmpl` (per-proposal overrides)
- `rollout_plan.md.tmpl` (phased upgrade plans)
- `signoff_manifest.json.tmpl` (multisig signoff summary)

