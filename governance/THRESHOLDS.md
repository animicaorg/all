# Thresholds & Quorums by Proposal Type

This document STANDARDIZES participation quorums and approval supermajorities
for each proposal class. Semantics of *quorum*, *approval ratio*, *abstain*,
quiet-ending, and snapshot are defined in `governance/VOTING.md`.

> **Counting rules (summary; see VOTING.md):**
> - **Quorum** counts **Support + Against + Abstain** over snapshot VP.
> - **Approval ratio** counts **Support / (Support + Against)**.
> - **Abstain** helps quorum but does **not** affect approval ratio.

---

## Canonical table

| Proposal Class        | Quorum (min participation) | Approval Supermajority (min) | Notes (why this level) |
|-----------------------|:--------------------------:|:----------------------------:|------------------------|
| **ParamChange**       | **15%**                    | **> 50%** (simple majority)  | Frequent, reversible by later vote. |
| **Policy**            | **15%**                    | **> 50%**                    | Process & documentation updates. |
| **PQRotation**        | **20%**                    | **≥ 60%**                    | Cryptography posture; caution around migrations. |
| **Upgrade (soft)**    | **20%**                    | **≥ 60%**                    | Feature flags; backward compatible. |
| **Upgrade (hard)**    | **25%**                    | **≥ 66.7%** (2/3)            | Coordination heavy; chain split risk. |
| **Treasury**          | **20%**                    | **≥ 60%**                    | Economic spend/allocation; protect against capture. |

**Tie rule:** if `Support == Against`, the proposal fails (threshold unmet).

**Quiet-ending:** Per `VOTING.md`, if the outcome flips in the last 24h or the
margin to threshold is <1.0% of total VP, the window auto-extends (max 72h).

---

## Rationale by class (brief)

- **ParamChange/Policy:** Operability and documentation; lower barrier enables agility.
- **PQRotation:** Security-sensitive but typically staged; supermajority required.
- **Upgrades:** **Soft** can roll back/feature-flag; **Hard** demands 2/3 to avoid contentious splits.
- **Treasury:** Funds and incentives; supermajority reduces governance capture risk.

---

## Formalization

Let `VP_total` be snapshot voting power, `VP_sup`, `VP_agn`, `VP_abs` aggregates.

- **Quorum check:**  
  `(VP_sup + VP_agn + VP_abs) / VP_total ≥ quorum(class)`
- **Approval check:**  
  `VP_sup / (VP_sup + VP_agn) ≥ threshold(class)`

A proposal passes **iff** both checks hold.

---

## Configuration surface (optional YAML)

Operators MAY expose the canonical policy as read-only config:

```yaml
thresholds:
  ParamChange:   { quorum: 0.15, approval: 0.5000001 } # strict ">" majority
  Policy:        { quorum: 0.15, approval: 0.5000001 }
  PQRotation:    { quorum: 0.20, approval: 0.60 }
  UpgradeSoft:   { quorum: 0.20, approval: 0.60 }
  UpgradeHard:   { quorum: 0.25, approval: 0.6667 }
  Treasury:      { quorum: 0.20, approval: 0.60 }

Implementations SHOULD use fixed-point math with explicit rounding rules.

⸻

Change control

Changing any quorum/threshold requires a Policy or Process proposal and
applies only to proposals opened after activation. Emergency actions are
governed by SECURITY_COUNCIL.md and do not retroactively alter thresholds.

⸻

Versioning
	•	v1.0 — Initial canonical thresholds, aligned with governance/VOTING.md.

