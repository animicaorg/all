# Wallet Security — Phishing, Blind Signing, and Safe Simulation

This guide summarizes **practical defenses** for Animica wallets (browser extension + Flutter desktop/mobile) with an emphasis on:
- **Phishing resistance** (web + RPC + supply chain)
- **Blind-signing avoidance** (human-verifiable summaries)
- **Safe simulation** (preflight checks before spending)

Use it as a day-to-day checklist and as policy guidance for teams shipping wallet UIs.

---

## 1) Phishing Resistance

### 1.1 Origins, Permissions, Providers
- **Only connect to dapps you trust.** The extension shows the **origin** (scheme + host + port). Verify the URL **character by character** (no look-alike domains).
- The injected provider is **`window.animica`**. Dapps must call `provider.request(...)`; the wallet will **prompt for origin approval**. Revoke unused sites in **Settings → Connected Sites**.
- **Never paste seed phrases or private keys** into web pages. The wallet **never** asks you to reveal them after onboarding.

### 1.2 RPC & Network Phishing
- Prefer known RPC URLs (see **Network** tab and `website/chains/*`). **Pin ChainId** in the wallet; the signer enforces domain-separated bytes that include ChainId.
- A malicious RPC can lie about state. When sending significant value:
  - Cross-check **Explorer** data (headers, balances).
  - Use **simulation** (see §3) and compare expected state deltas.
  - If something seems off (nonce resets, sudden fee spikes), **cancel** and re-verify.

### 1.3 Supply-Chain Hygiene
- **Install the wallet from official sources only.** Verify publisher/ID in the extension store or the installer signature (see installers docs).
- Auto-updates are signed. If your platform flags the binary/signature, **stop** and verify checksums against the release notes.

### 1.4 Social & Content Phishing
- Avoid links from support chats/DMs promising “airdrops”, “emergency upgrades”, or “fee refunds”.
- **Permissions popups**: Approving a site to **connect** ≠ approving it to **spend**. Spending requires an explicit **transaction** or **permit** with a full signing screen.

---

## 2) Blind-Signing: Don’t Do It

**Blind signing** = authorizing a cryptographic digest without a human-readable summary.

### 2.1 Wallet Policy
- The wallet **displays a structured summary** for every signing action:
  - Network / ChainId
  - Sender address (tap to compare checksummed bech32)
  - Action **type** (transfer, deploy, call)
  - To / contract (with ENS/labels where available)
  - Amount, **max fee** (base + tip), gas limit
  - Nonce, memo (if any)
- **Blind mode is disabled by default.** Only enable it for **development** and **never** for production use.

### 2.2 What You Must Check
- **Recipient:** Exact address or verified contract (link to Explorer from the popup).
- **Amount & Fee Ceilings:** Fees can be manipulated; confirm both price and limit.
- **Method & Params:** For contract calls, the wallet decodes known ABIs and shows named args. If the method is unknown, proceed only if you trust the contract author and have simulated the call.

### 2.3 Advanced Defenses
- **Address Book:** Use an allowlist for frequent recipients to highlight mismatches.
- **Spending Limits & Session Permits:** Prefer **permits** with tight scopes (token, spender, ceiling, expiry) over unlimited approvals.

---

## 3) Simulation: Verify Before You Sign

### 3.1 What Simulation Catches
- Reverts, **unexpected state changes**, and obvious spend drains.
- Gas estimates and **event emissions** (so UIs can confirm expected logs).

### 3.2 How We Simulate (Tooling)
- **Browser extension** and **Studio Web** can run **local simulation** using the deterministic Python VM (via `studio-wasm`) or **RPC preflight** against a dev node.
- Simulation runs with **no side effects**: state writes are discarded.

### 3.3 Recommended Flow
1. **Build Tx → Simulate**  
   - For transfers: verify **post-balance** and **fee**.  
   - For contract calls: verify **storage deltas** and **events** match expectations.
2. **Compare Results** between local (WASM) and RPC when possible (discrepancies are a red flag).
3. **Sign** only if simulations agree and the summary looks correct.

> Tip: For high value ops, **dry-run on testnet** first with identical calldata and contract bytecode.

---

## 4) Configuration Hardening (Users)

- **Auto-lock** after inactivity; require **WebAuthn** (passkey/security key) to unlock (see `docs/wallets/HARDWARE.md`).
- Pin a **default network** and hide unknown networks; disallow dapps from **adding** networks silently.
- Enable **Address Book** + **Blocklist** for known scams.
- Turn on **phishing database** checks (community lists) — the wallet can warn on bad origins.

---

## 5) Configuration Hardening (Developers)

- Always use **domain-separated SignBytes**; never sign raw JSON or ABI-encoded blobs.
- Enforce **summary builders** for new Tx types; fail closed if a summary cannot be rendered.
- Implement **permit editors** that default to **minimal scope** (token, spender, value, deadline).
- Gate all spend/sign actions on **user-presence** (WebAuthn) when available.
- Provide **deterministic simulation** in CI (golden vectors) to catch UI/API regressions.
- Keep an **allowlist of RPCs** and **pin ChainId** in configs.

---

## 6) Red Flags Checklist (Stop & Re-Verify)

- The signing popup **cannot decode** the contract method but asks for a **large approval**.
- Fees are **10× higher** than typical or the nonce looks wrong.
- The **recipient** differs by a few characters (homograph attack).
- A site demands your **seed phrase** or **private key**.
- Simulation shows **unexpected state changes** or extra events.

---

## 7) Incident Response

- **Disconnect** the suspicious site (wallet → Connected Sites).
- **Revoke approvals** on Explorer for affected tokens/contracts.
- **Rotate keys**: move funds to a fresh PQ account (new mnemonic).
- Report the domain/contract to community lists.

---

## 8) FAQ

**Does simulation guarantee safety?**  
No. It reduces risk but cannot prevent *all* malicious behaviors (e.g., logic that only exploits post-deployment state). Combine simulation with source/bytecode verification and small initial limits.

**Why are blind signatures dangerous if the digest is correct?**  
Because humans cannot verify the digest. Attackers rely on UI ambiguity; summaries make intent human-auditable.

**Can a malicious RPC trick simulation?**  
Local (WASM) simulation is independent, but it may use **stale state**. Cross-check with RPC and Explorer and prefer testnets for practice.

---

## 9) References

- `docs/wallets/HARDWARE.md` — WebAuthn + hardware co-signing
- `docs/dev/CONTRACTS_VERIFY.md` — verifying source and code hash
- `website/src/components/MetricTicker.tsx` — live node status patterns
- `execution/specs/RECEIPTS.md` — events/logs/bloom semantics for simulation parity

