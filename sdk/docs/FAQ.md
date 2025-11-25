# Animica SDK — FAQ & Common Pitfalls

A grab-bag of issues teams hit while wiring apps, tests, and devnets with the Animica SDKs (Python, TypeScript, Rust). If something here doesn’t match behavior you see, it’s a bug—please file an issue.

---

## 0) TL;DR Checklist

- ✅ Use **bech32m** addresses with HRP `anim` and validate with `is_valid(...)`.
- ✅ Always include the **correct `chainId`** and sign the **canonical CBOR SignBytes**.
- ✅ Pick an **algId** (`dilithium3` default, or `sphincs_shake_128s`) and keep it consistent for keys & addresses.
- ✅ Amounts are **integers in the smallest unit**; do not send decimals.
- ✅ Poll or subscribe for receipts; don’t assume immediate inclusion.
- ✅ For AICF jobs, results are **consumed next block**, not instantly.
- ✅ For randomness, follow **commit → reveal → VDF** windows; reveals outside the window are rejected.
- ✅ Use the **codegen** tool for contract stubs; don’t hand-roll ABI encoders.
- ✅ Pin dependencies; use the same CBOR lib family across components to avoid canonicalization drift.

---

## 1) Wallets & Addresses

**Q:** Why does `is_valid(address)` fail on a string I pasted from a blog?
- **A:** Animica addresses are **bech32m** with HRP `anim` (e.g., `anim1...`). Many examples on the web use bech32 (not “m”). Re-derive or re-encode via SDK helpers:
  - Python: `from omni_sdk import address as addr; addr.is_valid(s)`
  - TS: `address.isValid(s)`
  - Rust: `address::is_valid(&s)`

**Q:** My address doesn’t match after importing a mnemonic.
- **A:** Ensure **algId** and **account index** match what produced the original address. Payload = `alg_id || sha3_256(pubkey)`. Using `sphincs_shake_128s` for a key originally created with `dilithium3` yields a different address.

---

## 2) Chain ID & Signing Domains

**Q:** I get `ChainIdMismatch` or `InvalidTx` when submitting.
- **A:** Your SignBytes must embed the target `chainId` and **domain tag**. Don’t hex-serialize JSON—sign the **canonical CBOR** bytes from `tx/encode` helpers. Also verify the node’s chain:
  - Python: `client.chain_get_chain_id()`
  - TS: `rpc.http` client → `chain.getChainId`
  - Rust: `rpc::http::HttpClient` call → `chain.getChainId`

**Q:** Can I replay a signed tx to another network?
- **A:** No—domain separation + `chainId` prevents it by design.

---

## 3) Amounts, Gas, Nonce

**Q:** Transfer fails with `InsufficientBalance` but balance looks fine.
- **A:** Include fees (base + tip) in your mental math. Builders estimate **intrinsic gas**, but complex calls may need **headroom**. For write calls, query:
  - `state.getBalance(address)`
  - `state.getNonce(address)`
  Then build using that `nonce` or let the builder fetch (if your helper supports it).

**Q:** Decimal amounts in JSON?
- **A:** **Don’t.** Use integer smallest units. If you must display decimals, convert in UI only.

---

## 4) CBOR & Canonicalization

**Q:** Node rejects my raw transaction—“non-canonical map order”.
- **A:** You didn’t use the SDK’s **deterministic CBOR** encoder. Always use:
  - Python: `omni_sdk.tx.encode.sign_bytes_for_tx(...)`
  - TS: `tx.encode.signBytesForTx(...)`
  - Rust: `tx::encode::sign_bytes_for_tx(...)`

**Q:** Why do my hashes differ across languages?
- **A:** Mixed encoders or different float/BigInt treatment. Animica formats avoid floats; keep all numeric fields **integers** and let SDK types handle serialization.

---

## 5) Receipts & Inclusion

**Q:** `sendRawTransaction` returns a hash, but nothing shows up.
- **A:** That means the tx was **accepted into the pending pool**, not mined yet. Use:
  - Poll: `wait_for_receipt(...)`
  - Subscribe: WS `pendingTxs` / `newHeads` then fetch
Set a reasonable timeout and show user feedback.

**Q:** My test times out sometimes.
- **A:** Configure the devnet **difficulty/Θ** and miner; your block interval might be long. For e2e tests, reduce difficulty or run the built-in CPU miner.

---

## 6) Contracts, ABI, and Codegen

**Q:** Calls revert with “bad ABI” / “decode error”.
- **A:** ABI must match the deployed manifest. Use SDK **codegen**:
  - Python: `sdk/codegen/python/gen.py`
  - TS: `sdk/codegen/typescript/gen.ts`
  - Rust: `sdk/codegen/rust/gen.rs`
Don’t hand-craft encodings. Ensure your function name, arg order, and types match.

**Q:** Event decoding fails or returns empty arrays.
- **A:** Make sure:
  - You passed the **same ABI** used for deploy.
  - Topics and data lengths match.
  - You’re parsing the **receipt** from the node, not a local simulation result.

---

## 7) Data Availability (DA)

**Q:** Can I store secrets in DA?
- **A:** No—DA is designed for public retrieval (with proofs). Encrypt before posting if you must store sensitive data.

**Q:** Proof verification fails with “namespace range error”.
- **A:** Your verifier and commitment must agree on the **namespace id** and **leaf layout**. Use the SDK DA client’s `postBlob`/`getProof` helpers; do not re-encode leaves by hand.

---

## 8) AICF (AI/Quantum)

**Q:** `getResult(taskId)` returns “NoResultYet”.
- **A:** That’s expected in-flight. AICF settles **next block** (determinism). Either:
  - Wait for the next head, then fetch; or
  - Use `wait=True` / `{ wait: true }` with a **timeout**.

**Q:** Provider “SLA fail” or “lease lost” errors in devnet.
- **A:** Your local provider stub or queue timing may be misconfigured. Lower SLAs and timeouts, or use the included fixtures to simulate completion.

---

## 9) Randomness (Commit–Reveal–VDF)

**Q:** My reveal is rejected.
- **A:** Check the **round window**. Reveals outside the reveal window or not matching a prior commitment are invalid. Use the client’s `getRound()` and `getParams()` before committing.

**Q:** Beacon doesn’t change between rounds in tests.
- **A:** Either the devnet isn’t finalizing blocks or you’re reading a cached object. Subscribe to the randomness WS events or fetch after a new head.

---

## 10) Light Client & Proofs

**Q:** Light verification returns `False`.
- **A:** Ensure:
  - The header’s **DA root** matches the proof’s root.
  - All **sample indices** are in range for the blob matrix.
  - You’re using the **same hashing domains** as the network (see `spec/domains.yaml`).
Use the SDK light client helpers; don’t manually stitch Merkle branches.

---

## 11) Cross-Language Gotchas

- **Python**: BigInt is unbounded; when crossing FFI, convert to bytes/hex explicitly.
- **TypeScript**: Use `bigint` or `string` for large integers; avoid JS `number` for on-chain values.
- **Rust**: Prefer `u64`/`u128` and `serde_bytes` for opaque byte arrays; enable the right feature flags (e.g., `pq`).

---

## 12) Performance & Timeouts

- **HTTP timeouts**: Set sane connect/read timeouts; back off on `-32000` (server busy) errors.
- **WS reconnect**: Auto-reconnect with jitter; resubscribe on connect.
- **Batching**: JSON-RPC batch requests (`jsonrpc.py` supports batch) improve round trips.

---

## 13) Devnet vs Testnet/Mainnet

- **Chain IDs** differ: `1337` (devnet) vs registry (`spec/chains.json`) for others.
- Policies (gas tables, PQ alg-policy root) may differ per network; fetch via RPC:
  - `chain.getParams`
  - `chain.getChainId`

---

## 14) Codegen & Manifests

**Q:** Verify says “code hash mismatch”.
- **A:** Re-compile with the same compiler/toolchain versions and canonical JSON sorting. The studio services re-compute the code hash from your source+manifest; any whitespace or dependency drift may change it.

---

## 15) Troubleshooting Quick Steps

1. **Confirm chain**: `chain.getChainId` equals what you sign.
2. **Validate address**: `is_valid()`; HRP must be `anim`.
3. **Dump sign bytes** (hex) from the SDK and compare across languages.
4. **Check nonce/balance** via `state.getNonce` / `state.getBalance`.
5. **Reduce**: Try a minimal transfer before contract calls.
6. **Logs**: Enable SDK debug logs and node `rpc` logs.
7. **Diff CBOR**: Re-encode with SDK and compare byte-for-byte.

---

## 16) Examples & References

- See `sdk/docs/USAGE.md` for end-to-end examples.
- E2E harness under `sdk/test-harness` demonstrates deploy/call across Python, TS, Rust.
- Security practices: `sdk/docs/SECURITY.md`.

If you’re stuck, open an issue with:
- RPC URL & chainId
- Full error text
- Hex of your **SignBytes** and the **raw tx**
- SDK language/version, OS, and node version
- Minimal repro (preferably one of the example scripts adjusted to your setup)

Happy building! 🚀
