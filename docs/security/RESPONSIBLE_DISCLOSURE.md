# Responsible Disclosure Policy

We’re grateful to the security community for helping keep the Animica ecosystem safe.  
If you believe you’ve found a vulnerability, please **report it privately** and give us reasonable time to remediate before any public disclosure.

> Our machine-readable policy and contacts also live at: `website/public/.well-known/security.txt`

---

## Contact

- **Email:** security@animica.dev  
- **PGP:** See fingerprint and public key in `.well-known/security.txt` (use for sensitive reports).
- **Languages:** English (preferred), Spanish.
- **Timezone:** UTC±00

We acknowledge every report within **3 business days**.

---

## What to Include

Please provide as much detail as possible:

1. **Summary:** Short description and affected component(s) (e.g., `rpc/`, `p2p/`, `wallet-extension/`, `studio-services/`, `da/`, `randomness/`, `zk/`, `sdk/*`, `explorer-desktop/`, `installers/*`).
2. **Impact:** What can an attacker achieve? (RCE, key theft, chain reorg, DoS, data exposure, fund loss, etc.)
3. **Environment:** Versions/commits, OS/arch, config flags, network (main/test/dev).
4. **Reproduction steps:** Minimal PoC, commands, payloads, sample keys/accounts (no real funds).
5. **Logs & artifacts:** Crash traces, screenshots, network captures as needed.
6. **Fix suggestion (optional):** Any patch or mitigation ideas.
7. **Preferred credit:** Name/handle and link, or **opt-out** if you prefer to remain anonymous.

When appropriate, encrypt with our **PGP key** (from `security.txt`).

---

## Scope

**In scope (examples):**
- Core node: `core/`, `consensus/`, `p2p/`, `rpc/`, `mempool/`, `execution/`, `da/`, `proofs/`, `randomness/`, `capabilities/`, `aicf/`, `vm_py/`.
- Wallets: **browser extension** and **Flutter** wallet.
- Studio & Explorer: `studio-web/`, `studio-wasm/`, `studio-services/`, `explorer-desktop/`.
- SDKs & codegen: `sdk/python`, `sdk/typescript`, `sdk/rust`.
- Installers, updaters, appcasts, signing flows.

**Out of scope (non-exhaustive):**
- Third-party sites/services not operated by us.
- Social engineering, physical attacks, stolen devices/SIM swap.
- SPF/DMARC/DKIM misconfig not leading to practical exploit.
- Denial of service from **volumetric traffic** alone (unless bypassing rate limits/quotas).
- Best-practice suggestions without a concrete vulnerability.
- Self-XSS that requires the victim to paste code into their own console.
- Issues only affecting outdated, EOL, or rooted/jailbroken environments.

If unsure about scope, **ask first** at security@animica.dev.

---

## Rules of Engagement (Good Faith)

To qualify for safe harbor and recognition:

- Do **not** access, modify, or exfiltrate data that isn’t yours.
- Do **not** impact availability or degrade services beyond minimal proof.
- Do **not** run automated scanners against production endpoints.
- Use **testnet/devnet** and your own accounts; **no real funds**.
- Respect rate limits and **never** attempt to mine private keys or brute-force credentials.
- Stop testing immediately if you encounter personal data.

---

## Coordinated Disclosure & Timelines

- **Acknowledgment:** ≤ **3 business days**.
- **Triage & severity assessment:** ≤ **7 business days**.
- **Fix target:**  
  - **Critical/High:** aim for **30 days** (may expedite with mitigations).  
  - **Medium/Low:** aim for **90 days**.  
  Timelines may extend for complex issues, ecosystem coordination, or compatibility concerns.
- **Advisory:** We publish a security advisory (and request a CVE via GitHub Security Advisories/CNA where applicable) once a fix/mitigation is available.
- **Credit:** We credit reporters (if desired) in release notes/advisories and our Hall of Fame.

Please **do not disclose** details publicly before our advisory or the agreed deadline.

---

## Recognition & Bounties

We currently run a **private bounty program** for high-impact findings affecting user funds, key material, consensus safety, or remote code execution. If your report may qualify, we will outline next steps after triage. Swag/recognition may be offered for other in-scope issues at our discretion.

---

## Safe Harbor

We will not initiate legal action against researchers who:
- Report vulnerabilities to us **promptly**, **privately**, and **without extortion**;
- **Avoid privacy violations**, unnecessary data access/destruction, and service disruption;
- **Do not** exploit a vulnerability beyond what’s necessary to demonstrate it;
- Follow this policy and coordinate disclosure in good faith.

This policy does **not** grant permissions to act in violation of applicable laws. If you are unsure, contact us first.

---

## Handling of Sensitive Data

We minimize collection of non-public data during triage. Any sensitive artifacts shared are used only to reproduce and fix the issue and are deleted when no longer needed.

---

## Questions

If you have questions about scope, testing environments, or timelines, email **security@animica.dev**. Thank you for helping us protect the Animica community.

