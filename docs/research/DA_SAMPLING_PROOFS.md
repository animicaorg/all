# Data Availability Sampling: Probabilities & Bounds

This note derives probability-of-detection bounds for **Data Availability Sampling (DAS)** over erasure-coded, namespaced Merkle (NMT) matrices. It provides sizing guidance for the number of random samples required to detect withholding with target failure probability \(p_{\mathrm{fail}}\), and discusses stratified designs, multi-client aggregation, and practical caveats.

> TL;DR — If an adversary withholds a fraction \(f\) of the total shares in an erasure-coded blob, the probability that a verifier taking \(s\) *independent, uniformly random* samples misses all the bad shares is approximately \((1-f)^s\). Solving \( (1-f)^s \le p_{\mathrm{fail}} \) yields
> \[
> s \;\ge\; \frac{\ln p_{\mathrm{fail}}}{\ln(1-f)} \;\approx\; \frac{1}{f}\,\ln\!\left(\frac{1}{p_{\mathrm{fail}}}\right)
> \]
> (for small \(f\)). With coding rate \(k/n\), any withholding \(f \ge 1 - k/n\) makes decoding impossible; robust settings choose \(s\) against this worst case or a policy fraction \(f_\star\) slightly below it.

---

## 1) Model

- **Erasure code.** A blob is split into \(k\) data rows and expanded to \(n\) rows via systematic Reed–Solomon: rate \(R=k/n\), redundancy \(1-R\). Let total *shares* be \(N = n \cdot m\), where \(m\) is the number of column positions (after chunking).
- **Recoverability.** Any \(k\) rows (equivalently, sufficiently many shares covering those rows) suffice to reconstruct, assuming per-row RS and a valid **Namespaced Merkle Tree (NMT)** root committed in the block header.
- **Adversary.** Withholds a set of shares \(\mathcal{B}\subset[N]\) with size \(b = |\mathcal{B}|\), fraction \(f=b/N\). If the adversary ensures that no set of \(k\) rows is fully available (or equivalently, enough shares per row are missing), decoding fails.
- **Sampling.** A verifier issues \(s\) random *positions* (row, col), obtaining either a share + Merkle branch or a *miss/timeout*. A *fail to detect* event means all \(s\) probes land on available (non-withheld) positions.

> **Light-client check.** Each sampled share must verify its NMT branch against the DA root. This excludes *equivocation* attacks (serving inconsistent leaves) if the consensus binds the root.

---

## 2) Exact Tail (Without Replacement)

If samples are drawn **without replacement** uniformly from the \(N\) positions, probability of missing all \(b\) bad positions:
\[
\Pr[\text{miss all bad}] \;=\;
\frac{\binom{N-b}{s}}{\binom{N}{s}}
\quad\text{(Hypergeometric tail).}
\]
This is tight but cumbersome to invert. For sizing and intuition, we use standard upper bounds below.

---

## 3) Independence Approximation (With Replacement)

Treating samples as independent (good approximation when \(s\ll N\)):
\[
\Pr[\text{miss all bad}] \;\approx\; (1 - f)^s \;\le\; e^{-fs}.
\]
Hence to achieve target \(p_{\mathrm{fail}}\),
\[
s \;\ge\; \frac{\ln(1/p_{\mathrm{fail}})}{f}.
\]
This is conservative w.r.t. the hypergeometric for small \(s/N\).

---

## 4) Worst-Case Withholding vs Code Rate

Decoding requires at least a fraction \(R = k/n\) of *structure* to be present. An adversary that withholds any \(f \ge 1 - R\) (i.e., more than the parity budget) **can** force unavailability.

Thus, a robust per-blob single-verifier policy plugs \( f_\star = 1 - R \) into the bound:
\[
s \;\ge\; \frac{\ln(1/p_{\mathrm{fail}})}{1 - R}.
\]
**Example.** If \(R=1/2\) and we target \(p_{\mathrm{fail}}=10^{-9}\), then
\( s \ge \ln(10^{9})/(1/2) \approx 2 \cdot 20.72 \approx 42 \) samples.

> In practice, protocol designers may use \( f_\star = 1 - R - \epsilon \) for margin (e.g., \(\epsilon=0.05\)) to account for partial-row correlations or network flakiness.

---

## 5) Row/Column Stratified Sampling

Matrices enable **stratified** plans that are more robust to *row-concentrated* withholding:

- **Row-first**: pick \(r\) random rows; in each, sample \(c\) random columns ⇒ total \(s=r\cdot c\).
- **Column-first**: symmetric variant.

If the adversary withholds a *minimal blocking set* (e.g., all shares from \(n-k+1\) rows), pure random sampling yields
\[
\Pr[\text{miss}] \approx \left(1 - \frac{n-k+1}{n}\right)^{r} \;=\; \left(\frac{k-1}{n}\right)^{r}
\]
when we demand hitting at least one *bad* row (and then at least one bad cell inside the row). Adding per-row cell sampling further decreases failure probability.

**Guideline.** Choose \(r \ge \ln(1/p_{\mathrm{fail}})/\ln\!\bigl(n/(k)\bigr)\), then a small \(c\) (e.g., 2–4) to mitigate within-row sparse withholding.

---

## 6) Union Bounds Across Blobs / Blocks

If a light client validates \(B\) blobs per block and wants total failure probability \(p_{\mathrm{block}}\), allocate per-blob budget \(p_{\mathrm{fail}} = p_{\mathrm{block}}/B\) and size \(s\) accordingly. Across a session of \(T\) blocks, union bound yields \(p_{\mathrm{session}} \le T \cdot p_{\mathrm{block}}\).

---

## 7) Multi-Client Aggregation

With \(M\) independent verifiers, each taking \(s\) samples (seeded by a public beacon/VRF), the network-level miss probability (all miss) is:
\[
\Pr[\text{all miss}] \;\approx\; (1 - f)^{Ms} \;=\; \bigl((1-f)^s\bigr)^{M}.
\]
This is a powerful effect: even modest \(s\) per client composes to very strong guarantees if seeds are independent (or *delayed* to prevent adversarial adaptivity).

---

## 8) Timeouts and Partial Observability

A *timeout* counts as detection (i.e., treated as missing share). Model network unreliability with a per-probe success \(q\). Effective bad fraction is \( f' = f + (1-f)(1-q) \). Replace \(f\) by \(f'\) in sizing. Conservative designs use lower bounds on \(q\).

---

## 9) Concrete Sizing Examples

**Example A (balanced redundancy).**  
\(R=1/2\), \(p_{\mathrm{fail}}=10^{-12}\).  
\(s \ge \ln(10^{12})/(1/2) \approx 2\cdot27.63 \approx 56\) random samples.

**Example B (higher rate).**  
\(R=2/3\), \(p_{\mathrm{fail}}=10^{-9}\).  
\(s \ge \ln(10^9)/(1/3) \approx 3\cdot 20.72 \approx 62\) samples.

**Example C (multi-client).**  
\(R=1/2\), target block-level \(10^{-9}\), \(M=8\) clients each do \(s=8\) → network miss \(\approx (1/2)^{8\cdot 8} \approx 1/2^{64} \ll 10^{-9}\).

> These are *rule-of-thumb*; stratified plans can reduce \(s\) for the same target.

---

## 10) Proof Carriage: What is Verified

Each sampled share must be accompanied by:

1. **NMT branch** proving inclusion for its namespace (or a range proof if querying a namespace slice).  
2. **Position binding** (row/col) consistent with the erasure layout committed in the blob envelope.  
3. **Integrity hash** of the leaf payload (namespace || length || data as per codec).  
4. **Header binding**: the NMT root is part of the block header’s DA commitment; light client checks the header against the consensus chain (or light-proof for headers).

Successful verification + timely retrieval implies availability of the sampled positions under the assumed network model.

---

## 11) Adversarial Considerations

- **Adaptive withholding.** Use sampling seeds derived from a **finalized** randomness beacon (commit–reveal → VDF), so the adversary cannot pre-delete shares adaptively after seeing queries.  
- **Correlated omissions.** Stratification (Sec. 5) counters row- or column-centric attacks.  
- **Equivocation.** NMT proofs bound leaves; malicious responders cannot serve mutually inconsistent shares without violating the root.  
- **Response DoS.** Rate-limit queries and accept *any* honest mirror. A single honest replica suffices for detection with the same probability bounds.

---

## 12) Practical Policy

- Pick **coding rate** \(R\in[1/2, 2/3]\) for light clients: redundancy ensures modest \(s\) for stringent \(p_{\mathrm{fail}}\).  
- Default **independent sampling**: \( s = \left\lceil \frac{\ln(1/p_{\mathrm{fail}})}{1-R} \right\rceil \).  
- Prefer **stratified**: e.g., \(r=16\) rows, \(c=4\) columns each (\(s=64\)), with row- then column-seeding.  
- **Multi-client amplification**: publish recommended \(s\) per client and aggregate guarantees for explorers/wallets.  
- **Auditing**: record sampled indices + proofs (or their hashes) to enable third-party verification of sampling performed.

---

## 13) Bounding via Chernoff (Alternative View)

Let \(X_j\in\{0,1\}\) indicate “hit bad share” on sample \(j\) with \(\mathbb{E}[X_j]=f\). Then
\[
\Pr\Big[\sum_{j=1}^s X_j = 0\Big] = (1-f)^s \le e^{-fs}.
\]
For *at least one* detection with probability \(1-\delta\), choose \(s \ge \frac{1}{f}\ln(1/\delta)\). This is the same sizing as Sec. 3 and is often used in protocol specs.

---

## 14) Notes on Namespaces

If sampling over a **namespace range**, effective \(N\) is the number of shares *in-range*. Withholding strategy can target a single namespace; size \(s\) against the worst-case \(f_\star\) *within that namespace*. When verifying availability for a *contract-specific* namespace, ensure at least \(\tilde{s}\) samples hit that namespace (e.g., by dedicated draws).

---

## 15) Checklist

- [ ] DA root is bound into block header; header is authenticated (light client OK).  
- [ ] Sampling seeds derive from unbiasable beacon (commit–reveal → VDF).  
- [ ] Queries spread across rows/cols (stratified) or are fully uniform.  
- [ ] Per-sample proofs verify (NMT inclusion/range, codec, indices).  
- [ ] Sample count sized for \(p_{\mathrm{fail}}\), with per-blob or per-block union bounds.  
- [ ] Multi-client deployment publicizes aggregate guarantees.  
- [ ] Logs/proofs-of-sampling are retained (hashes) for audits.

---

### Appendix: Inverting the Hypergeometric (Monotone Bound)

For completeness, if exact sizing is desired without independence approximation:

Find smallest \(s\) with
\[
\frac{\binom{N-b}{s}}{\binom{N}{s}} \le p_{\mathrm{fail}}.
\]
Use monotonicity in \(s\) and binary search; or upper-bound via
\[
\frac{\binom{N-b}{s}}{\binom{N}{s}} \le \left(1-\frac{b}{N}\right)^s \cdot \exp\!\left(\frac{s(s-1)}{2}\cdot\frac{b}{N(N-b)}\right),
\]
which tightens the simple \((1-f)^s\) approximation when \(s\) is not negligible vs \(N\).

---

**Summary.** DAS provides exponentially decreasing miss probability in the number of random samples. Code rate \(R\) sets the **withholding threshold**; sizing against \(f_\star = 1-R\) with independence (or hypergeometric) yields simple, robust configurations. Stratification, unbiased seeding, and multi-client aggregation together make detection extremely reliable in practice.
