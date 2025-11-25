# Data Availability Security: Withhold Attacks, Sampling Bounds, Mitigations

This note complements the DA specs and explains **what can go wrong** and **how our design defends** against it. It focuses on *withhold attacks* (publish header/roots but keep data), the math behind **Data Availability Sampling (DAS)**, and the practical mitigations we implement.

> Related:
> - Layout & erasure: `docs/da/ERASURE_LAYOUT.md`
> - DAS algorithm & probability: `docs/da/SAMPLING.md`
> - Retrieval & light-client duties: `docs/da/RETRIEVAL.md`
> - NMT rules & proofs: `docs/spec/MERKLE_NMT.md`

---

## 1) Threat model

**Adversary goal.** Get a block accepted while keeping **enough blob shares unavailable** so that honest nodes **cannot reconstruct** (or audit) the data.

**Capabilities considered:**
- Selective withhold of shares (arbitrary pattern; worst-case clustering).
- Timed/partial responses (serve some indices, stall others).
- Collusion between a proposer and a subset of DA mirrors.
- Network manipulations: eclipse/partition of some light clients.
- Attempted proof forgery (NMT inclusion/range) — prevented by soundness.

**Out of scope (here):**
- Cryptographic breaks of SHA3/Blake3 or RS code — we assume standard security.
- Byzantine majority of full nodes + validators (then any DA scheme fails).

---

## 2) What counts as “catastrophic” withholding?

For an \((k,n)\) Reed–Solomon code on the **extended** matrix, decoding requires at least **\(k\)** shares (per the layout’s stripe logic). Withholding **\(w \ge n-k+1\)** shares in a decoding set renders it **irrecoverable**.

We therefore treat an attack as *successful* if the adversary can publish a block whose referenced blobs are **not decodable** by honest nodes while honest auditors **fail to detect** unavailability during the grace window.

---

## 3) DAS detection probability (uniform sampling)

Let:
- \(n\) = total shares,
- \(w\) = withheld shares (adversary-controlled),
- \(m\) = total random samples (without replacement) performed by a light client
  across the DA matrix for the block.

The probability that **none** of the \(m\) samples hits a withheld share is:

\[
P_\text{miss} = \frac{\binom{n-w}{m}}{\binom{n}{m}} \;\; \approx \;\; \left(1-\frac{w}{n}\right)^m \;\; \le \;\; e^{-mw/n}.
\]

To detect “just over the decoding threshold”, plug in \(w^* = n-k+1\).
To target a failure probability \(P_\text{miss} \le \varepsilon\), choose:

\[
m \;\ge\; \frac{n}{w^*}\,\ln\!\left(\frac{1}{\varepsilon}\right).
\]

**Example (dev profile):** \(n=2048,\; k=1024 \Rightarrow w^*=1025\).  
With \(\varepsilon = 2^{-40} \Rightarrow \ln(1/\varepsilon)\approx 27.7\).  
Then \(m \ge \lceil 27.7 \cdot 2048/1025 \rceil = 56\).  
We recommend **96+** effective samples for margin and timing failures.

---

## 4) Clustering & stratified sampling

A rational adversary **clusters** missing shares (entire rows/columns/tiles) to increase \(P_\text{miss}\) under naive uniform sampling.

**Mitigation:** We stratify sampling by **rows and columns**. Suppose the matrix has \(R\) rows and \(C\) columns and we take \(s_r\) samples per selected row and \(s_c\) per selected column (or one per row/column in a pass). If the attacker fully erases \(t\) rows and \(u\) columns (worst clustering), the chance to avoid all erased strata is bounded by:

\[
P_\text{miss} \;\le\; \left(1-\frac{t}{R}\right)^{s_r R} \cdot \left(1-\frac{u}{C}\right)^{s_c C}
\;\;\lesssim\;\; e^{-s_r t} \cdot e^{-s_c u}.
\]

Takeaway: **one sample per row and per column** already crushes row/column erasures. We combine **stratified passes** (row/col tiles) with **uniform jitter** to cover arbitrary shapes.

---

## 5) Time & response games

**Adaptive withhold.** Serving initial indices but stalling later ones attempts to exploit the client’s finite patience.

**Policy:** Treat **timeouts as missing**. A proof that arrives after a strict deadline is **counted as unavailable** for the purpose of DAS. This blocks “slow-loris” and timing oracles.

**Mirrors & diversity.** Light clients query **multiple independent DA mirrors and P2P peers** in parallel (limited fan-out with backoff). Independence + deadlines throttles adaptive adversaries.

---

## 6) Eclipse/partition risks

If a client is fully **eclipsed**, an attacker can *appear* to satisfy DAS while globally withholding. To reduce impact:

- Connect to **diverse transports** (HTTP(S) mirrors + P2P) and **distinct ASNs/providers**.
- Enforce **minimum peer diversity** before accepting availability.
- Cache negative results and **re-audit** after connectivity changes or when reorg risk is high.

---

## 7) Proof integrity & namespace games

- **NMT proofs** include both **inclusion** and **namespace-range** constraints. We **reject** any proof whose namespace intervals would allow cross-namespace collisions.  
- All DA hashing is **domain-separated** (see `da/utils/hash.py`); proofs are bound to the **exact commitment** referenced in the block header (`header.da_root`).

---

## 8) Parameter guidance

Recommended **targets** (tune per network‐class):

| Network | \(n\) | \(k\) | Target \(\varepsilon\) | Baseline \(m\) (uniform) | Effective plan |
|---|---:|---:|---:|---:|---|
| Local/devnet | 1024 | 512 | \(2^{-24}\) | ≥ 32 | 32 uniform + 1 per row (fast) |
| Testnet | 2048 | 1024 | \(2^{-32}\) | ≥ 48 | 64 uniform + row/col stratified |
| Mainnet | 4096 | 2048 | \(2^{-40}\) | ≥ 56 | 96 uniform + row/col + tile jitter |

**Notes**
- “Effective plan” counts **distinct indices**; stratified passes may reuse bandwidth better than pure uniform sampling.
- Increase \(m\) if mirrors are few, latency is high, or you anticipate more targeted clustering attacks.

---

## 9) Node & service mitigations (server side)

- **Pin on commit**: any commitment referenced by a canonical block MUST be **pinned** for ≥ reorg horizon.
- **Strict schemas**: reject malformed envelopes and impossible index/proof requests.
- **Rate limits**: per-IP/token buckets; protect against index oracles and flooding.  
- **No oracle leaks**: constant-time-ish proof checks and uniform error messages where feasible.
- **Metrics & alerts**: export proof failure rates, timeouts, and per-commitment hot-spotting.

---

## 10) Light-client checklist

- [ ] Bind proofs to **exact `da_root`** from the audited header.  
- [ ] Use **stratified + uniform** sampling plan with strict deadlines.  
- [ ] Count **timeouts as missing**; backoff and diversify mirrors.  
- [ ] Maintain a **replayable PRNG seed** per block (e.g., \(H(\text{blockHash} \parallel \text{salt})\)).  
- [ ] Cache results; **invalidate on reorgs** past the audited height.  
- [ ] Refuse to accept availability if peer/mirror **diversity threshold** isn’t met.

---

## 11) Worked micro-example

- Matrix: \(n=2048, k=1024\) ⇒ threshold \(w^* = 1025\).  
- Attacker withholds exactly \(1025\) shares in **two full rows** \(t=2\) plus scattered columns.  
- Client plan: **1 per row** (2048/cols granularity) + **64 uniform**.  
  - Row pass: \(P_\text{miss,row} \le e^{-1 \cdot t} = e^{-2} \approx 0.135\).  
  - Uniform pass: \(P_\text{miss,uniform} \le e^{-64\cdot 1025/2048} \approx e^{-32} \approx 1.27\cdot 10^{-14}\).  
  - Combined (independent approx): \(\approx 1.7\cdot 10^{-15}\) \(< 2^{-48}\).

Conclusion: even modest stratification annihilates worst-case clustering.

---

## 12) Residual risks & future work

- **Coordinated eclipse** of large client populations remains a risk; we invest in **multi-network relays** and **watchtower audits**.  
- **Proof-of-custody** style mechanisms (anti-chunk withholding + slashing) are candidates for L2 overlays.
- Explore **verifiable retrieval** (rate-limited challenge–response) to deter lazy mirrors.

---

## 13) TL;DR

- Use **enough samples** \(m \sim \tfrac{n}{n-k+1}\ln(1/\varepsilon)\) as a baseline.  
- **Stratify** across rows/cols + add uniform jitter.  
- **Timeouts count as missing**; diversify mirrors; bind to header roots.  
- With these practices, withhold attacks become **detectable with negligible probability of miss** at practical costs.

