# Hardware Security Keys & Devices — Ledger/Trezor/FIDO/WebAuthn

This document describes **hardware-backed key flows** for Animica accounts and sessions across:
- **Ledger** and **Trezor** (USB hardware wallets)
- **Platform authenticators** and **security keys** via **FIDO2/WebAuthn** (YubiKey, Solo, Passkeys)
- How these integrate with **post-quantum (PQ)** account schemes (Dilithium3, SPHINCS+), the wallet extension, and the SDKs.

> Status: reference design. Real device support depends on vendor firmware & app availability. All flows here keep **on-chain account keys PQ-native** and use hardware where practical for user auth, transport sealing, and optional multisig.

---

## 1) Goals

1. **Protect secrets at rest** with hardware when available.
2. **Never export PQ private keys** once generated/imported.
3. Preserve **determinism & domain separation** for signatures used on chain.
4. Allow **progressive hardening**: start with software-only PQ, add FIDO as second factor, move to Ledger/Trezor when supported, or use multisig with a hardware co-signer.

---

## 2) Support Matrix (high level)

| Capability | Ledger | Trezor | FIDO2/WebAuthn (Passkeys / Security Keys) |
|---|---|---|---|
| On-device **PQ signing** (Dilithium3/SPHINCS+) | **Future/Custom app** | **Future/Custom app** | **No** (WebAuthn is not a generic PQ signer) |
| Display/confirm **address** on device | Yes (with custom app) | Yes (with custom app) | N/A |
| **Transport attestation** / user-presence | Yes | Yes | **Yes** (user presence + attestation) |
| Use as **co-signer** in multisig/permit | Yes | Yes | **Yes** (map WebAuthn assertion → allow/permit) |
| Unlock local PQ vault (2FA) | Indirect | Indirect | **Yes** (challenge-response to decrypt local vault key) |

> Interpretation: Until native PQ apps land on Ledger/Trezor, we recommend **two secure modes**:
> 1) **2FA/Unlock mode:** WebAuthn asserts user-presence to decrypt an *encrypted* PQ signing key that stays on the device (phone/desktop), never leaves the vault.
> 2) **Multisig/Permit mode:** Keep the on-chain account as PQ, add a **WebAuthn or ECDSA/ECDH hardware key** as a policy gate for spending or role upgrades.

---

## 3) Flows

### 3.1 WebAuthn (FIDO2) — “Unlock & Approve” (recommended baseline)
**Use case:** Local PQ key does the chain signature; WebAuthn proves user-presence and optionally releases a session key.

1. **Enroll**
   - User creates a **passkey** (platform or roaming key) for the wallet origin.
   - Wallet stores **credential ID + attestation metadata**.
2. **Unlock vault**
   - When the user initiates a send or deploy, the extension (or Flutter app) calls `navigator.credentials.get()` with a **challenge** (derived from intent hash).
   - On success, the app **unseals the PQ vault key** (AES-GCM) using a KEK that’s derived from the WebAuthn assertion (or it simply gates unlock on assertion presence).
3. **Sign domain bytes (PQ)**
   - The PQ private key signs **domain-separated SignBytes** (Tx/Header/Permit) as usual.
4. **Optional: co-signature record**
   - The app stores a **WebAuthn assertion receipt** alongside the Tx for audit/UI.

**Security notes**
- WebAuthn provides **user-presence** and **anti-phishing** (RPID), not a chain-valid signature.
- No PQ key material leaves the device; passkeys never see chain bytes.

---

### 3.2 Multisig / Policy permit with WebAuthn
**Use case:** Require a second factor that’s enforced **on chain**.

- Deploy a **policy contract** (or use built-in multisig) that accepts:
  - **PQ account signature**, and
  - A **WebAuthn proof record** verified off-chain but wrapped into a **permit** object signed by the PQ key holder (or a federation).
- Alternatively, use a **two-of-N** multisig where one signer key resides in a hardware wallet (ECDSA/Ed25519 today) and the other is PQ. The contract enforces thresholds; clients provide both signatures.

**Tradeoffs**
- Strong policy guarantees, but slightly more UX and gas than single-sig.

---

### 3.3 Ledger/Trezor — Custom App (Native PQ) *(when available)*
**Use case:** Fully hardware-resident PQ keys, device renders **address** and **Tx summary**.

**APDU/Protocol sketch**
1. **Keygen / Import**
   - Device app derives a PQ key from seed (BIP-39-like) or stores imported seed (never exported).
2. **Get Address**
   - Host requests `GET_ADDR(alg_id, path)` → device computes `payload = alg_id || sha3_256(pubkey)`, shows HRP+checksum; user confirms.
3. **Sign**
   - Host sends `SIGN(alg_id, sign_domain, cbor_bytes_hash)`. Device displays summary & fee, user confirms; returns PQ signature bytes.
4. **Attestation (optional)**
   - Device signs a certificate about the pubkey using a **device attestation key** for provenance (off-chain UX/audit only).

**UX**
- Blind signing **disabled** by default. The host must send a **parsed summary** (amount, to, fee) for display.

---

## 4) Domain Separation & Display

- All signatures use **SignBytes** from `core/encoding/canonical.py` (CBOR, deterministic map order).
- UI shows:
  - **Network/ChainId**, **From/To**, **Amount**, **Gas price/limit**, **Nonce**, **Memo** (if any).
  - Hash **prefix** matching the sign domain (e.g., `tx:...`).
- For hardware displays with limited space, send a **signing summary** (structured TLV) alongside the digest for human verification.

---

## 5) Recovery & Migration

- **Mnemonic + passphrase** remain the only path to fully reconstitute the PQ identity.
- If WebAuthn credentials are lost:
  - Recovery via **mnemonic restore** and **re-enroll** new passkeys.
- For Ledger/Trezor:
  - Recover using the device seed, then **re-derive addresses** and verify on device screens.
- **Rotation plan**: when alg-policy changes, hardware apps must support the new alg_id; otherwise use **multisig migration** (old PQ + new PQ) until sunset.

---

## 6) Threat Model & Best Practices

- **Phishing:** WebAuthn resists origin spoofing; confirm the wallet **origin** before touching the security key.
- **Screen-to-sign:** Prefer devices that **display** the destination & amount; avoid blind signing.
- **Session timeouts:** Require WebAuthn re-auth every X minutes or per high-value action.
- **Attestations:** Treat hardware **attestation certificates** as optional UX; do **not** rely on them for consensus safety.
- **Backups:** Hardware seed != PQ mnemonic unless you’re running a **native PQ app** on the device. Keep both if you use both.

---

## 7) Developer Hooks

- **Extension / Web**
  - `wallet-extension/src/background/permissions.ts` — gate sensitive ops on `navigator.credentials.get()`.
  - `src/background/tx/sign.ts` — ensures domain-separated bytes; plug hardware transport guards here.
- **Flutter**
  - Use `package:webauthn` (platform channel) or OS APIs for passkeys.
  - Gate vault unlock on **user-presence**.
- **SDKs**
  - TS/Python/Rust signers accept a `preSignHook(intent)` that can prompt WebAuthn and/or a hardware co-signature.

---

## 8) Test Plan

- **Simulated WebAuthn** in CI for unlock/permit paths.
- **Golden vectors** for SignBytes hashes (ensure device UIs show the same fields).
- **End-to-end**: PQ sign (software), WebAuthn approve → Tx accepted by node.
- **When native PQ on Ledger/Trezor is available**: integrate in nightly hardware bench using vendor HID/U2F bridges.

---

## 9) FAQ

**Can WebAuthn replace the on-chain PQ signature?**  
No. WebAuthn is an origin-scoped assertion, not a chain-verifiable PQ signature.

**Do I need a hardware wallet to be secure?**  
No; using WebAuthn as **unlock 2FA** plus strong OS keychain & auto-lock already raises the bar substantially. Hardware co-signers or native PQ apps add another layer.

**Will Ledger/Trezor support Dilithium3/SPHINCS+?**  
Roadmaps evolve. Our architecture works **without** native PQ by using WebAuthn and/or multisig today, and can adopt native PQ apps when they ship.

---

## 10) References & Pointers

- `docs/pq/WALLET_OPS.md` — seed phrases & hardware flows
- `docs/vm/PATTERNS.md` — multisig/permit contract patterns
- `wallet-extension/*` — passkey integration points
- `sdk/*/wallet/*` — keystore & signer hooks

