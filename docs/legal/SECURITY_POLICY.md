<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Animica Security Policy

This document mirrors our top-level **SECURITY.md** guidance and adds **cryptographic contact keys** and operational details for coordinated vulnerability disclosure.

For a quick summary of scope and expectations, also see:
- **Responsible Disclosure:** `docs/security/RESPONSIBLE_DISCLOSURE.md`
- **Threat Model:** `docs/security/THREAT_MODEL.md`
- **DoS Defenses:** `docs/security/DOS_DEFENSES.md`
- **Supply Chain:** `docs/security/SUPPLY_CHAIN.md`

Our public `security.txt` is published at:

https://animica.org/.well-known/security.txt

---

## 1) Scope

Reports are welcome for all first-party software and services maintained by the Animica project, including but not limited to:

- Node components: core, consensus, p2p, rpc, da, execution, vm_py, capabilities, randomness.
- Client apps: wallet-extension (MV3), Flutter wallet, explorer (web & desktop), studio (web & services).
- SDKs: Python, TypeScript, Rust.
- Hosted surfaces we operate (if any) at `*.animica.org`.

**Out of scope** examples (still appreciated as FYI, but may not qualify for coordinated timelines): third-party forks, demo environments with fake data, social engineering of maintainers, volumetric DDoS, and findings that require privileged local access without bypassing our defenses.

---

## 2) How to Report a Vulnerability

- **Email (preferred):** `security@animica.org`
- **Encrypt (recommended):** Use the PGP key below (or the latest from `/.well-known/security.txt`)
- **Optional backup:** `security-backup@animica.org` (same keyring)
- **Please include:** affected component/version/commit, reproduction steps, impact assessment, and any PoC.

We acknowledge new reports **within 3 business days**. If you do not receive confirmation, please re-send or ping using a different channel.

---

## 3) Coordinated Disclosure & Timelines

- **Triage:** within **3 business days**
- **Initial assessment & severity (CVSS v3.1):** within **7 days**
- **Fix window:** typically **30–90 days** depending on severity and ecosystem risk
- **Embargo:** we may request a short embargo while patches are rolled out
- **Credit:** with consent, we acknowledge reporters in release notes/Hall of Fame

We strive for transparency. If timelines must change (e.g., complex downstream coordination), we will keep you updated.

---

## 4) Safe Harbor

We support good-faith research and testing that:
- Avoids privacy violations, service degradation, or data exfiltration
- Respects rate limits and legal boundaries
- Uses test networks/accounts where feasible

If you comply with this policy and applicable laws, we will not initiate legal action for your research activities and will work with you to remediate issues.

---

## 5) Severity Guidance (CVSS v3.1)

| Severity | Examples (non-exhaustive) |
|---|---|
| **Critical** | Key exfiltration, arbitrary remote code execution, signature forgery, consensus safety breaks, chain reorg beyond stated limits |
| **High** | Transaction malleability with fund loss, sandbox escape in VM, policy bypass enabling unauthorized state changes |
| **Medium** | Persistent DoS via unauthenticated path, inaccurate economic accounting without loss, information disclosure that aids exploitation |
| **Low** | UI spoofing mitigated by confirmation, minor input validation issues without impact, documentation security gaps |

---

## 6) Cryptographic Contact Keys

**Primary Security Key** (PGP)

- **User ID:** `Animica Security <security@animica.org>`
- **Fingerprint:** `4B59 8C2C 9E2D 1AF5 7D0E  6B9C 3F41 8A77 2D61 B4F2`
- **Key ID:** `0x3F418A772D61B4F2`
- **Algorithm:** Ed25519 / Curve25519 subkeys
- **Expires:** 2027-12-31 (rotated annually; see Rotation below)

—–BEGIN PGP PUBLIC KEY BLOCK—–

mDMEZa1r5xYJKwYBBAHaRw8BAQdAV9uE2K4k8m6hQm6r2h2O2d1Y1rE1H5i9Q0mA
Yb8qzqHn1+5BbmltaWNhIFNlY3VyaXR5IDxzZWN1cml0eUBhbmltaWNhLm9yZz6I
jAQQFgoAoA4WIQRLWWwsmS0a9X0Oa5w/ QYp3LWG08gUCZa1r5wIbAwULCQgHAgYV
CgkICwIEFgIDAQIeAQIXgAAKCRD/ QYp3LWG08o5lAQD0cXqg1h3dCqTzqf4r2Lw
… (truncated example block; use the live key from /.well-known/security.txt) …
=abcd
—–END PGP PUBLIC KEY BLOCK—–

**Backup Key (Operations)**

- **User ID:** `Animica Security Backup <security-backup@animica.org>`
- **Fingerprint:** `9A73 1C5D F2B0 7E8E 0F22  1B37 E4C9 6D10 8EAA 3C91`
- **Purpose:** Only for verification if the primary is unavailable
- **Location:** Also published in `/.well-known/security.txt`

> Always prefer the **primary** key. If you receive a message purportedly from us, verify its signature against one of the fingerprints above.

---

## 7) Key Rotation & Revocation

- We rotate the primary security key at least **annually** or upon suspected compromise.
- Current fingerprints and revocation certificates are published at:
  - `/.well-known/security.txt`
  - `docs/signing/policies.md`
- Old keys will be revoked and cross-signed where feasible.

---

## 8) Supply Chain & Release Integrity

See `docs/security/SUPPLY_CHAIN.md` for SBOM, signature formats, and CI attestations. In brief:
- Release artifacts are signed (platform-appropriate) and, where supported, accompanied by **Sigstore** or **GPG** attestations.
- Verify release notes and checksums from multiple independent mirrors where possible.

---

## 9) Communications & Credit

We will coordinate privately until a fix is available. Public advisories include:
- Affected versions and components
- Mitigations and upgrade paths
- CVE (if assigned) and CVSS
- Acknowledgments (opt-in; handle/pseudonym OK)

For high-impact issues we may publish postmortems focusing on technical remediation and prevention.

---

## 10) Contact Channels (Summary)

- **Email:** `security@animica.org` (PGP preferred)
- **Backup:** `security-backup@animica.org`
- **Security.txt:** `https://animica.org/.well-known/security.txt`
- **Emergency (fallback):** Open a **private** GitHub advisory (if enabled on the repo)

Thank you for helping keep the Animica ecosystem safe.
