# Keys — Usage, Signatures & Rotation

**Version:** 1.0  
**Status:** Active  
**Scope:** Repository signing (commits/tags/releases), on-chain governance multisigs, and artifact attestation for Animica.  
**Related:** `governance/keys/gpg/maintainers.asc`, `governance/keys/multisig_policies.md`, `governance/policies/TRANSPARENCY.md`, `chains/signatures/*`, `chains/checksums.txt`.

---

## 0) Key Classes (what’s what)

| Class | Purpose | Lives | Examples |
|---|---|---|---|
| **Repo GPG keys** | Sign git commits/tags and policy docs | Developer laptops + hardware token (preferred) | `GPG: 0xABCD...` |
| **Release signing keys** | Detached signatures over JSON/archives/checksums | `chains/signatures/*` (public); private on HSM | `release@animica.dev` |
| **Governance multisig keys** | Execute on-chain governance (votes/activations/escrow moves) | Custody HSM + policy quorum | `governanceMultisig` (bech32m `am1…`) |
| **Service keys (PQ/Beacon/DA)** | AuthN between providers and chain services; telemetry attestation | Provider HSM/TPM | `aicfScheduler`, `randomnessBeacon` |
| **Developer test keys** | Localnet/testnet only, never mainnet | Throwaway | `am1t…` test addrs |

> **Never** reuse the same private key across classes.

---

## 1) Formats & Encodings

- **On-chain addresses:** bech32m, HRP `am`, e.g., `am1...`  
- **Public keys (JSON):**  
  ```json
  { "algorithm":"ed25519|secp256k1|dilithium3|sphincs+", "encoding":"base64", "publicKey":"..." }
Detached signatures: ASCII-armored OpenPGP (.asc) or Minisign (.minisig) against SHA-256’d payloads.

Checksums: chains/checksums.txt (newline-separated sha256 <path>); signed by chains/signatures/registry.sig.

2) What gets signed (and by whom)
Artifact	Canonical path	Signers (min threshold)
Chain registry & checksums	chains/registry.json, chains/checksums.txt	Release key (1/1) + optional maintainer cosign
Chain JSONs	chains/*.json	Release key (1/1)
Governance docs (policy/risk)	governance/policies/*, governance/risk/*	Author’s GPG (1/1)
Proposal & tally snapshots (optional)	governance/examples/*	Steward GPG (≥2/5 recommended)
Activation runbook hashes	Proposal appendix	Release key + Gov multisig tx reference

3) Trust Model (how verifiers check)
Step 1: Import maintainer bundle: governance/keys/gpg/maintainers.asc

Step 2: Verify signatures → map to GitHub handles / CODEOWNERS.

Step 3: For releases, verify chains/signatures/registry.sig against checksums.txt and then verify each file hash.

Verifier quickstart (GnuPG):

bash
Copy code
# Import public keys
gpg --import governance/keys/gpg/maintainers.asc

# Verify registry & checksums
gpg --verify chains/signatures/registry.sig chains/checksums.txt

# Spot-check a file
shasum -a 256 chains/animica.testnet.json | awk '{print $1}'
grep animica.testnet.json chains/checksums.txt
Minisign alternative (optional):

bash
Copy code
minisign -Vm chains/checksums.txt -P "$(cat chains/signatures/maintainers.pub)"
4) Developer setup (commit & tag signing)
Generate a new GPG key (YubiKey/SmartCard preferred):

bash
Copy code
gpg --quick-gen-key "Your Name <you@example.com>" ed25519 sign 2y
gpg --list-secret-keys --keyid-format=long
git config --global user.signingkey <KEYID>
git config --global commit.gpgsign true
git config --global tag.gpgSign true
Export your public key and open a PR:

bash
Copy code
gpg --armor --export <KEYID> > yourname.asc
# Add to governance/keys/gpg/ and update maintainers.asc (concatenate)
Sign tags/releases:

bash
Copy code
git tag -s v0.1.0 -m "Animica governance baseline"
git push origin v0.1.0
5) Release signing workflow (CI/HSM)
Inputs: Updated chains/*.json, regenerated chains/checksums.txt
Outputs: chains/signatures/registry.sig (detached sig), optional per-file .asc

CI outline:

bash
Copy code
# In release job (private key loaded via HSM/agent)
shasum -a 256 chains/*.json > chains/checksums.txt
gpg --local-user RELEASE_KEYID --detach-sign --armor \
  --output chains/signatures/registry.sig chains/checksums.txt
Publish both files in the PR. A maintainer not involved in the build verifies locally and ACKs.

6) Governance multisig (high-level)
Policy: See governance/keys/multisig_policies.md for exact thresholds, rotations, and emergency powers.

Best practice: Separate keys per role (Gov Multisig vs. Foundation Multisig).

Activations: All high-risk activations (VM/PQ/DA) require multisig plus prior on-chain vote per policy.

On-chain addresses: Recorded in governance/ops/addresses/{testnet,mainnet}.json.

7) Rotation policy (when & how)
Triggers

Time-based: yearly for repo keys; 18–24 months for release key.

Event-based: device loss, suspected compromise, maintainer departure, cryptanalytic risk.

Playbook

Generate new keypair on HSM (release) or token (maintainers).

Add new public key to maintainers.asc and update CODEOWNERS if needed.

Cross-sign: old key signs new key’s UID; publish in a PR.

Announce deprecation window (e.g., 30 days).

After window, revoke old key, commit *.rev to repo.

Revocation command

bash
Copy code
gpg --gen-revoke <OLDKEYID> > governance/keys/gpg/revocations/<OLDKEYID>.rev
gpg --import governance/keys/gpg/revocations/<OLDKEYID>.rev
8) Compromise & incident response
Suspected repo key leak:

Revoke key, tag affected commits, require co-maintainer re-sign on critical docs.

Open Transparency incident note; rotate tokens/secrets.

Release key compromise:

Immediate Sev-1: publish revocation, mark latest registry.sig as untrusted, re-sign with emergency backup, cut hotfix release with new chain of trust.

Exchanges/wallets notified via security list.

Multisig key compromise:

Freeze governance actions; migrate funds/roles to new multisig per multisig_policies.md.

Schedule emergency proposal if on-chain rules require.

9) PQ considerations (signatures & KEM)
Repository signing remains OpenPGP (ed25519/ed448) for ecosystem tooling.

On-chain accounts may use dilithium3/sphincs+; keep separate from repo keys.

If/when repo artifact signing adopts PQ, we will publish a parallel signature (.asc + .pq.asc) for a full deprecation window.

10) Local verification recipes (copy-paste)
Verify a policy doc:

bash
Copy code
gpg --verify governance/risk/PARAMS_BOUNDARIES.md.asc governance/risk/PARAMS_BOUNDARIES.md
Create & verify a detached signature (author workflow):

bash
Copy code
gpg --detach-sign --armor governance/policies/TRANSPARENCY.md
gpg --verify governance/policies/TRANSPARENCY.md.asc governance/policies/TRANSPARENCY.md
11) File layout (this folder)
bash
Copy code
governance/keys/
├─ README.md                       # this file
├─ gpg/
│  ├─ maintainers.asc             # bundle of public keys (armor)
│  └─ revocations/                # *.rev files for revoked keys
├─ multisig_policies.md           # thresholds, roles, rotation cadence
12) Checklist for new maintainers
 Create GPG key on hardware token; back up revocation cert.

 PR your *.asc and update maintainers.asc.

 Enable commit/tag signing in git.

 Read multisig_policies.md and join the security mailing list.

 Do a test signature on a non-critical doc and request verification.

13) Changelog
1.0 (2025-10-31): Initial key classes, trust model, rotation, and incident playbooks.
