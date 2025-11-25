# Key Management — Mnemonics, Passphrases, and PQ Variants

This guide explains how Animica wallets create, store, and rotate **post-quantum (PQ)** keys, how **mnemonics** and **passphrases** work, and what to know when using **Dilithium3** and **SPHINCS+ (SHAKE-128s)**.

> See also:
> - `docs/pq/WALLET_OPS.md` (seed phrases & hardware flows)
> - `pq/py/utils/*` and wallet code in `wallet-extension/src/background/keyring/*`
> - SDK keystores: `sdk/python/omni_sdk/wallet/*`, `sdk/typescript/src/wallet/*`

---

## 1) Mnemonics (seed phrases)

- **Format:** BIP-39–like word lists (128–256 bits entropy + checksum), but **seed derivation uses SHA3**.
- **Seed derivation:**  

seed = PBKDF2-HMAC-SHA3-256(mnemonic_utf8, salt=“mnemonic”+passphrase, iter=2048, dkLen=64)
master = HKDF-SHA3-256(ikm=seed, info=“animica/master”, L=64)

- **Language:** English wordlist by default (others optional).
- **Security:** Write once; never paste in websites. Prefer **offline generation**. Always **test a restore**.

### Optional passphrase (a.k.a. “25th word”)
- Extends BIP-39 behavior: same salt pattern, **different seed**.
- Without the passphrase you **cannot** recover the same wallets.
- Use a **memorable but high-entropy** passphrase; store separately from the mnemonic.

---

## 2) Deterministic subkeys & derivation paths

Animica uses a simple, explicit derivation that avoids legacy ECDSA trees:

- **Per-algorithm namespace via HKDF info:**

node = HKDF-SHA3-256(ikm=master, info=“animica/pq/dilithium3” | “animica/pq/sphincs-shake-128s” | “animica/p2p/kyber768”, L=64)

- **Account/index derivation:**

child = HKDF-SHA3-256(ikm=node, info=f”account:{a}/change:{c}/index:{i}”, L=64)

- **Keygen:** `child` seeds the PQ primitive (Dilithium3 or SPHINCS+); Kyber is used for P2P/session only.

> Tip: Keep **accounts** for personas/environments (main, test, ops). Use `change=0` for external receive, `1` for internal.

---

## 3) Addresses & alg IDs

- **Address payload:** `alg_id || sha3_256(pubkey)` (see `pq/py/address.py`).
- **Bech32m** human-readable prefix: `anim1…` (main/test nets vary by HRP).
- **Why alg_id?** It pins the **signature scheme** used by that address, enabling verifiers to choose the right algorithm and allowing **safe rotation** later.

**Important:** The same mnemonic yields **different addresses** per algorithm namespace. Don’t assume cross-alg equivalence.

---

## 4) Supported PQ variants

| Purpose | Default | Alternative | Notes |
| --- | --- | --- | --- |
| Account signing | **Dilithium3** | SPHINCS+ (SHAKE-128s) | Dilithium3 ≈ fast/small; SPHINCS+ = hash-based fallback (slower, stateless). |
| P2P handshake | **Kyber-768** (KEM) | — | Establishes session keys for node/network; **not** for account addresses. |

**Rotation policy:** Networks may deprecate/introduce algorithms. Wallets surface **alg-policy roots** (see `pq/POLICY.md`) and recommend migration when scheduled.

---

## 5) Vaults, export, and backups

- **In-browser (extension):** Encrypted vault (**AES-GCM**, 256-bit) with **session PIN/lock**. Files in extension storage; export **encrypted** backups only.
- **Desktop/Mobile (Flutter):** Use platform keystores where available; otherwise AES-GCM file vault.
- **CLI/SDK keystore:** Password-protected AES-GCM JSON. Keep strong passwords; enable **OS full-disk encryption**.

**Backups checklist**
- [ ] Write mnemonic on paper/steel; verify checksum.
- [ ] Record passphrase separately (if used).
- [ ] Export encrypted vault (optional) and store offline.
- [ ] Test a restore on an **air-gapped** device.

---

## 6) Passphrase & spending passcodes

- **Passphrase** affects only seed derivation; it’s part of identity.
- **Local spending passcodes** are **UI locks** for wallets; they do **not** change keys on chain.
- Never confuse the two.

---

## 7) Multisig & permissions

- Recommended for treasuries: **multi-alg multisig**, e.g., M-of-N where members may use Dilithium3 or SPHINCS+. See `docs/pq/KEYS.md` and contract patterns in `docs/vm/PATTERNS.md`.
- For dapps, prefer **permit/role** models with **domain-separated** signatures (`core/encoding/canonical.py`).

---

## 8) Migration & rotation

When alg-policy announces a rotation:
1. **Derive** new addresses for the target algorithm from the same mnemonic.
2. **Move funds** (on-chain) or update **multisig sets**.
3. Update API keys/webhooks to the new address.
4. Keep the old keys until policy end-of-life, then archive.

---

## 9) Threat model & best practices

- **Phishing:** Never enter mnemonics into web pages; use the extension or hardware flows.
- **Side-channels:** Keep hot keys on end-user devices; production signing should prefer **cold** or **hardware** keys.
- **Compartmentalization:** Separate mnemonics per role; avoid reusing seeds across chains/environments.
- **Rate-limits:** Enable wallet lockouts and set auto-lock timers.
- **Recovery drills:** Practice restores quarterly.

---

## 10) FAQs

**Can I use the same mnemonic for both Dilithium3 and SPHINCS+?**  
Yes; derivation namespaces ensure **distinct** key material per algorithm.

**If I change my passphrase later, do I get the same accounts?**  
No. Passphrase changes the seed → you get **different** accounts.

**Are exports safe?**  
Only if the **export is encrypted** and stored offline with a strong password. Prefer mnemonics + passphrase.

---

## References

- NIST PQC (Dilithium, Kyber) — final standards.
- SPHINCS+ (SHAKE-128s) spec.
- Bech32m encoding (BIP-350); BIP-39 (mnemonics) — adapted with SHA3 for Animica.

