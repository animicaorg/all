# Architecture Decision Records (ADRs)

This folder contains the **authoritative log of significant technical/product decisions** for Animica. ADRs make intent, trade-offs, and consequences durable and discoverable long after the relevant PRs are merged.

---

## Why ADRs?

- Capture **context** and **constraints** at the time of the decision  
- Force explicit **trade-off analysis** and alternatives  
- Enable new contributors and auditors to **reconstruct rationale**  
- Provide a safe mechanism to **amend/supersede** decisions over time

---

## Where things live

- ADR documents: `governance/adrs/ADR-YYYY-NN-<kebab-title>.md`
- Canonical template: `governance/templates/ADR_TEMPLATE.md`
- Related governance refs: `governance/MAINTAINERS.md`, `governance/PROCESS.md`, `governance/ROLES.md`

> Keep ADRs **small (≈1–2 pages)** and link to deeper design docs, specs, and diagrams in `/docs/**`.

---

## Naming & ID conventions

- **ID format:** `ADR-YYYY-NN` (year + zero-padded sequence for that year)  
  - Example: `ADR-2025-03-adopt-kzg-for-da-commitments.md`
- **Filename:** `ADR-YYYY-NN-<kebab-title>.md`
- **Front-matter:** copy from template and fill `title`, `status`, `date`, `owners`, `component`, `scope`, and link metadata.

### Status values

`Proposed | Accepted | Rejected | Deprecated | Superseded by ADR-YYYY-NN`

> Never delete ADRs. When revisiting, **append** context and change status instead of rewriting history.

---

## How to author a new ADR

1. **Copy the template**
   ```bash
   cp governance/templates/ADR_TEMPLATE.md governance/adrs/ADR-$(date +%Y)-XX-your-title.md
   # Replace XX with the next sequence number; see "Index & numbering" below.

	2.	Fill out sections:
	•	Context (problem, forces, constraints, assumptions)
	•	Decision (one-sentence + details)
	•	Rationale (why this beats alternatives)
	•	Alternatives (table)
	•	Impact analysis (compatibility, security, performance, ops)
	•	Rollout plan & abort switches
	•	Testing strategy
	3.	Link evidence (benchmarks, issues/PRs, prior ADRs, external research).
	4.	Open a PR marked ADR: with the ID and title.

⸻

Review & acceptance workflow
	•	Required reviewers: at least one maintainer of each affected component (see governance/MAINTAINERS.md).
	•	Security review: required for changes touching consensus rules, cryptography, key handling, or trust roots.
	•	Decision meeting (optional): use the ADR as the single artifact; keep minutes as a comment with outcomes.

Acceptance checklist (gate):
	•	Compatibility/migration plan reviewed
	•	Security review complete (actions tracked)
	•	Performance budgets/benchmarks documented
	•	Observability (metrics/logs/alerts) defined
	•	Rollout & rollback plan with owners
	•	Docs updated (user/dev/ops) and examples added
	•	Test coverage plan implemented in CI
	•	Licensing & third-party deps vetted

These items are mirrored in the template’s “Readiness Checklist”.

⸻

Amending or superseding decisions
	•	Minor clarifications: update the ADR, add an “Amendment” subsection with date and summary.
	•	Material changes: create a new ADR and set:
	•	Old ADR → status: "Superseded by ADR-YYYY-NN" and link
	•	New ADR → links.supersedes: ADR-YYYY-NN

⸻

Index & numbering

Use one of these approaches to find the next sequence number and (optionally) regenerate a table of contents:

Quick shell (POSIX)

# List existing IDs sorted, show next suggestion for current year
year=$(date +%Y)
ls governance/adrs/ADR-${year}-*.md 2>/dev/null | sed -E 's/.*ADR-([0-9]{4})-([0-9]{2}).*/\2/' \
  | sort -n | tail -n1 | awk -v y="$year" '{printf "Next ID: ADR-%s-%02d\n", y, ($1==""?1:$1+1)}'

# Generate a simple TOC (Markdown) with status badges
printf "# ADR Index\n\n" > governance/adrs/INDEX.md
for f in governance/adrs/ADR-*.md; do
  title=$(sed -n 's/^title:[[:space:]]*"$begin:math:text$.*$end:math:text$".*/\1/p' "$f" | head -n1)
  status=$(sed -n 's/^status:[[:space:]]*"$begin:math:text$.*$end:math:text$".*/\1/p' "$f" | head -n1)
  echo "- [$(basename "$f")]($f) — _${status:-Unknown}_ — ${title:-<no title>}" >> governance/adrs/INDEX.md
done

Tiny Python (more robust front-matter parsing)

python3 - <<'PY'
import re, pathlib, sys, yaml
root = pathlib.Path("governance/adrs")
rows = []
for p in sorted(root.glob("ADR-*.md")):
    meta = {"title":"<no title>","status":"Unknown"}
    with p.open() as fh:
        head = []
        if fh.readline().strip() == "---":
            for line in fh:
                if line.strip() == "---": break
                head.append(line)
            try: meta.update(yaml.safe_load("".join(head)) or {})
            except Exception: pass
    rows.append((p.name, meta.get("status","Unknown"), meta.get("title","<no title>")))
out = ["# ADR Index\n"]
out += [f"- [{name}](./{name}) — _{status}_ — {title}" for name,status,title in rows]
(pathlib.Path("governance/adrs/INDEX.md")).write_text("\n".join(out)+ "\n")
print("Wrote governance/adrs/INDEX.md", file=sys.stderr)
PY

(If pyyaml is not available in CI, the shell version is fine.)

⸻

Style guidance
	•	Be concise; link instead of inlining walls of text.
	•	Prefer ASCII diagrams or Mermaid (/docs/diagrams/*.mmd) for flows.
	•	Use normative language carefully (MUST/SHOULD/MAY).
	•	Keep security, performance, and operability explicit.
	•	Provide metrics (or budgets) and observability hooks.
	•	Note failure modes and rollback paths.

⸻

Example candidates for ADRs
	•	Switch DA commitment from Merkle to NMT + KZG for blobs
	•	Introduce PLONK KZG verifier in zk/ with native fast-path
	•	Enforce PQ address default (Dilithium3) for wallets & SDKs
	•	Add optimistic scheduler behind a feature flag in execution
	•	Modify fee market (base/tip split, surge multiplier bounds)

⸻

FAQ

Q: Can I add an ADR after code merges?
A: Yes, but it should reference the merged PR(s) and explain why the ADR was back-filled.

Q: Multiple components?
A: Keep one ADR that spans them, but tag all affected components and ensure each owner signs off.

Q: What about minor implementation details?
A: Use design docs in the relevant module; reserve ADRs for cross-cutting or externally-visible decisions.

⸻

Checklist for authors (TL;DR)
	•	Use the template; set ID, title, status=Proposed, date
	•	Fill context, decision, rationale, alternatives
	•	Impact (compat/security/perf/ops), rollout, testing
	•	Link issues/PRs/specs/diagrams/benchmarks
	•	Request reviews from responsible maintainers & security
	•	Update status to Accepted/Rejected (or supersede) post-decision
	•	Regenerate INDEX.md

⸻

Stewardship: The Governance Editors curate this folder for coherence and discoverability. See governance/ROLES.md.

