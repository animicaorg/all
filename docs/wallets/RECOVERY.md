# Wallet Recovery — Social Recovery & Splitting Secrets

This guide describes **robust, human-friendly recovery** for Animica wallets using:
- **Social recovery:** guardians approve rotating your wallet keys to a new device.
- **Secret splitting:** a threshold of shares reconstructs the seed (or vault key).

Use both together for defense-in-depth: social recovery for *account access*, splitting for *seed custody*.

---

## 0) Vocabulary

- **Seed phrase (mnemonic):** Human words → master seed used to derive PQ keys (e.g., Dilithium3/SPHINCS+).  
- **Vault key:** Symmetric key that encrypts the local keyring (e.g., AES-GCM).  
- **Guardians:** People/devices who cannot spend your funds but can **approve recovery** of your account to a new key.  
- **M-of-N threshold:** Minimum number of shares/approvals (**M**) needed from **N** total.

> Animica note: bech32m `anim1…` addresses encode **alg_id||sha3(pubkey)**. After recovery, you’ll typically **rotate to a new address** and move funds, unless you use a smart/account-abstraction layer that supports key rotation.

---

## 1) Social Recovery

### 1.1 Model
- You designate **N guardians** (trusted humans, a hardware key you own, a corporate safe, etc.).
- To recover, you **prove loss** (new device, new pubkey) and obtain **M approvals**.
- A **timelock** (e.g., 48–120h) and **notif to old devices** reduce hijack risk.

### 1.2 What Guardians Can/Cannot Do
- **Can:** Approve *recovery* (key rotation) to a new public key, within a window.
- **Cannot:** Spend your funds or change fees, unless explicitly permitted.
- **Rotation:** You can replace guardians periodically (e.g., annually) with a 2-phase update.

### 1.3 Recommended Policy
- Personal wallets: **M=2, N=5** (you + 4 others) or **M=2, N=3**.  
- Organizational: **M=3, N=5** or **M=4, N=7**, spanning legal/ops/security across sites.
- Require **WebAuthn/hardware signatures** from guardians when possible.
- **Out-of-band verification:** phone + video or in-person check before signing.

### 1.4 Suggested Approval Message (Domain-Separated)
Guardians sign a structured message (displayed by the wallet):

ANIMICA_RECOVER_V1
chain_id: 
current_address: anim1…
new_pubkey: 
requested_by: <email/handle/commitment>
issued_at: 
expires_at: 
nonce: 

Wallet verifies:
- **ChainId** matches configured network.
- **Window** (`issued_at/expires_at`) and **nonce** freshness.
- **Guardian set** membership and M-of-N threshold.

> Keep a **paper card** with your guardian policy (no secrets), including *who the guardians are*, *how to contact them*, and *recovery steps*.

---

## 2) Splitting Secrets (Threshold Backups)

### 2.1 What to Split
Prefer splitting the **vault key** (that encrypts your seed file) rather than the raw seed itself:
- If a single share leaks, your encrypted vault remains safe.
- You can rotate the vault key and re-split without touching addresses.

**Options**
- **SSS/SSKR/SLIP-39:** Threshold secret sharing for humans (mnemonic-style shares).
- **Passphrase + SSS:** Encrypt the seed (or vault) with a passphrase, then split the ciphertext key.

### 2.2 Parameters
- Individuals: **2-of-3** or **3-of-5**
- Teams: **3-of-5** or **4-of-7**
- Avoid overly large N (operational drag) and too small M (weak security).

### 2.3 Encoding & Integrity
- Include metadata with each share:
  - **Share ID / index / total**, **threshold**, and a **commitment hash** of the master secret (or KDF tag) to verify correct reconstruction.
  - **Version tag** (e.g., `RECOV-1`) and **created_at**.
- Print on archival paper (or **steel** plate for fire/water resistance). Optional QR for speed.
- Store **chainId, derivation scheme, alg_id** separately (non-secret) for future compatibility.

### 2.4 Distribution
- **Geography:** Distinct sites (home safe, office safe, safe deposit box).
- **People:** Mix family, close friends, counsel, and a professional custodian.
- **No single person** should hold enough shares to reconstruct alone.
- Use **tamper-evident** envelopes; seal numbers and inventory log.

### 2.5 Refresh & Rotation
- **Annual rotation** or upon any exposure risk (move to a new master/vault key and re-split).
- Verify old shares reconstruct before destroying them.

---

## 3) Designing Your Recovery Plan (Checklist)

1. **Pick model(s):** Social recovery, secret splitting, or both.  
2. **Choose thresholds:** M-of-N for guardians and shares.  
3. **Select guardians & locations:** Diversity and availability.  
4. **Decide what to split:** vault key vs seed; add passphrase?  
5. **Create a runbook:** Step-by-step recovery procedures.  
6. **Drill:** Perform a **dry-run recovery** on testnet.  
7. **Document:** Non-secret metadata, contacts, timelines, and responsibilities.

---

## 4) Recovery Runbooks

### 4.1 Device Lost / Stolen
1. Use another device to **freeze** the wallet session if available.
2. Start **social recovery**: collect **M guardian approvals** to rotate to a fresh key.
3. On success, **move funds** (or update key in an AA account if supported).
4. **Re-issue** threshold shares for the new vault/seed.

### 4.2 Seed (or Vault) Suspected Compromised
1. Immediately **transfer funds** to a **fresh wallet** (new seed).
2. **Rotate guardians** if compromise involves a guardian.
3. **Re-split** the vault/seed, distribute new shares, and revoke old ones.

### 4.3 Guardian Unavailable or Malicious
- Maintain **backup guardians** or larger N.  
- Use **court/corporate safety valve** for organizational wallets.  
- If M cannot be met, fall back to **secret shares** (if you use both models).

---

## 5) Operational Hardening

- **Passphrases:** Use an extra passphrase (stored separately) even with shares.
- **Labels:** Never print the full mnemonic or passphrase on envelopes. Use codes.
- **Device security:** Enable **auto-lock**, **WebAuthn**, and **system disk encryption**.
- **CI/CD:** For corporate treasuries, require **multi-party** sign with hardware keys and policy servers.
- **Travel mode:** Carry **zero secrets** when traveling; rely on guardians for emergency re-provisioning.

---

## 6) Templates

### 6.1 Guardian Card (Non-Secret)
- Wallet owner: __________  
- Guardian name: __________  
- Contact(s): __________  
- Guardian pubkey (short): __________  
- Policy: M=__ of N=__ ; Recovery window: __ h  
- Out-of-band verification steps: __________

### 6.2 Share Label (Non-Secret)
- Share ID: __ / __   (threshold M=__)  
- Version: RECOV-1  
- Created: YYYY-MM-DD  
- Master commitment (first 8 chars): ________  
- Storage location code: ________

---

## 7) FAQ

**Q: Should I split the seed or the vault key?**  
Prefer **vault key** splitting + passphrase. It limits blast radius if one share leaks.

**Q: Can I put a share in the cloud?**  
If and only if it’s **encrypted** client-side with a **strong passphrase** and you ensure a **separate factor** (another share or hardware).

**Q: How often should I rotate?**  
At least **annually**, after major life/work changes, or any suspected exposure.

---

## 8) Quickstart: Personal Plan (Opinionated Defaults)

- Social recovery: **2-of-3 guardians** (trusted friend, sibling, hardware key in home safe).  
- Secret splitting: **3-of-5** shares of the **vault key**, distributed across home safe, office safe, bank box, trusted friend, and attorney.  
- Annual drill on **testnet**; rotate shares every 12–18 months.

---

## 9) References

- `docs/wallets/SECURITY.md` — phishing & blind-signing defenses  
- `docs/wallets/HARDWARE.md` — WebAuthn/hardware co-signing  
- `docs/dev/CONTRACTS_VERIFY.md` — verifying source & code hash  
- `website/chains/*` — canonical networks & ChainId metadata

