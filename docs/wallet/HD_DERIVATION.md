# Animica HD wallet derivation (BIP-39 / BIP-44 / SLIP-0010 → ML-DSA-65)

Status: **normative for third-party wallets** (2026-08-22).
Reference implementation: [`packages/animica-crypto/src/hd.ts`](../../packages/animica-crypto/src/hd.ts)
(tests: [`packages/animica-crypto/tests/hd.test.ts`](../../packages/animica-crypto/tests/hd.test.ts)).

## Why this exists

Animica accounts are **ML-DSA-65** (FIPS 204, scheme id `0x1003`) keypairs. Nothing about
ML-DSA is elliptic-curve based, so BIP-32 public-key derivation cannot apply. But FIPS 204
key generation is a pure function of a 32-byte seed **ξ**:

```
(pk, sk) = ML-DSA-65.KeyGen_internal(ξ)          // ξ = 32 random bytes in FIPS 204 Alg. 1
```

so an HD wallet only needs a deterministic way to turn a mnemonic into one ξ per account.
This document fixes that mapping so every wallet (Animica's own, Edge, enKrypt, hardware
wallets, …) recovers the **same addresses from the same 12/24 words**.

## The scheme

1. **Mnemonic → seed**: BIP-39. `seed = PBKDF2-HMAC-SHA512(mnemonic_NFKD, "mnemonic" + passphrase_NFKD, 2048 rounds, 64 bytes)`.
2. **Seed → node**: SLIP-0010, *ed25519 family* (HMAC-SHA512 with master key `"ed25519 seed"`, hardened-only children, `data = 0x00 || k_par || ser32(i)`). This is exactly the derivation Solana, Sui, Aptos, NEAR and Massa wallets already ship; only the final step differs.
3. **Path**: BIP-44

   ```
   m / 44' / 4279885' / account' / 0' / address_index'
   ```

   * `4279885` = `0x414E4D` = ASCII **"ANM"** — the SLIP-0044 coin type for Animica.
   * Every level is hardened (SLIP-0010 ed25519 has no non-hardened children).
   * Default account is `m/44'/4279885'/0'/0'/0'`.
4. **Node → ξ**: the 32-byte private-key half of the final node *is* ξ. No extra hashing.
5. **ξ → address**:

   ```
   (pk, sk) = ML-DSA-65.KeyGen_internal(ξ)                 // @noble/post-quantum: ml_dsa65.keygen(ξ)
   address  = bech32m("anim", u16be(0x1003) || SHA3-256(pk)) // 34-byte payload, 66-char string, starts "anim1zqp"
   ```

   SHA3-256 is NIST SHA-3 (not Keccak). bech32m (BIP-350, constant `0x2bc830a3`), **not** bech32.

Wallets MAY store ξ (32 bytes) instead of the 4,032-byte secret key and regenerate the
keypair on load; `KeyGen_internal` is deterministic.

## Test vectors

Mnemonic (BIP-39 reference, no passphrase):

```
abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about
seed = 5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc19a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4
```

| Path | ξ (ML-DSA-65 seed) | Address |
|---|---|---|
| `m/44'/4279885'/0'/0'/0'` | `e3bb5b745b1da91201e7b9744038def07dfd02da9a85682d30468b9355c50835` | `anim1zqpn54yt2fz07wg5zz33qplkh7tewv30tm5s9cdwvag6kf6myvd2d5sj9pzp7` |
| `m/44'/4279885'/0'/0'/1'` | `5b7ea6e7ab17f7f78900e57dae759104518bca0e55f7fa69b6d0b9986e130595` | `anim1zqpmznku3ddgyhl27d0p38jq7qyjgsnvafzd8pwh27gednh0x09s2egxyv9ej` |
| `m/44'/4279885'/1'/0'/0'` | `cf68ab2eb4222e81656973cc01769ab28b794f8907e214c1f615a5da6a5c0260` | `anim1zqpn2j43cqempqfke6rzvwf6f4529xwrexgpcw8gfd8dg8agmcqw6qqu83f7t` |

Addresses were produced with `@noble/post-quantum` 0.6.1 `ml_dsa65.keygen(ξ)` and cross-checked
against the mainnet node's Python implementation (`pq/py/address.py::address_from_pubkey`), which
uses the node's own ML-DSA-65 keygen. The SLIP-0010 implementation passes SLIP-0010 test vector 1.

## Signing, for completeness

Transactions are signed with `ML-DSA-65.Sign(sk, M = SHA3-512(sign_bytes), ctx = "")` over the
canonical CBOR preimage; see `spec/tx_format.cddl` and `apps/wallet-extension/src/tx/signing.ts`.
Broadcast via `POST https://rpc.animica.org/rpc` → `tx.sendRawTransaction`.

## Ecosystem references

* Website: <https://animica.org> · Explorer: <https://explorer.animica.org> · RPC: `https://rpc.animica.org/rpc`
* Buy / trade ANM: **NonKYC** <https://nonkyc.io/market/ANM_USDT>
* SLIP-0044 coin type `4279885` (ASCII "ANM"); bech32 HRP `anim` (SLIP-0173); CAIP-2 `animica:1`
