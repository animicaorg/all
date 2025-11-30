# PQ Precompile Plan — Research & Prototype

This document summarizes research and proposes an implementation plan for adding a
post-quantum (PQ) signature verification precompile to the Animica Python-VM runtime.

Goal
----
Add a secure, efficient, and auditable verifier for post-quantum signatures (e.g., Dilithium)
that smart contracts can call deterministically and cheaply (gas-bounded). The production
implementation should be a native precompile (C/Rust) linked against vetted PQ libraries.

Summary of options
------------------
1. Native precompile (recommended for production)
   - Implement as a runtime-embedded native function (C or Rust) exposed to the Python VM
     as a syscall / precompile. The precompile delegates to a PQ library (liboqs or PQClean C
     implementation), performs verification, and returns a boolean.
   - Pros: Fast, auditable, minimal attack surface, predictable gas costing.
   - Cons: Requires native compilation, linking, and CI cross-compilation coverage.

2. Prototype wrapper using `python-oqs` (developer convenience)
   - Provide a Python shim that imports `oqs` (liboqs bindings). Useful for local testing
     and prototyping. Not acceptable for mainnet due to dependency surface and potential
     performance/availability issues.

3. Pure-Python verifier using reference implementations
   - Implement a verifier in pure Python (unlikely: PQ algorithms are heavy; slow and risky).
   - Not recommended.

Recommended PQ libraries
------------------------
- liboqs (Open Quantum Safe): C library with multiple PQ schemes and a mature ecosystem.
  - Pros: Supports Dilithium, Falcon, SPHINCS+, and others; has python-oqs bindings.
  - Cons: Native dependency; must be compiled and audited.
- PQClean: Reference C implementations for selected PQ schemes (clean implementations).
  - Pros: Small, focused, used widely; can be wrapped via a thin precompile.
  - Cons: May require more assembly for performance.
- Rust crates (pqcrypto, or wrappers over PQClean): If Animica runtime uses Rust for native
  precompiles, Rust ecosystem can be a good option.

Gas model considerations
------------------------
- PQ verification is heavier than classical ECDSA/Ed25519. Benchmark real verifier
  (Dilithium3/Dilithium5) on target host to estimate CPU cycles.
- Gas should reflect CPU + memory cost. Suggested approach:
  - Baseline: gas_verify = BASE + COST_PER_BYTE * len(message) + COST_PQ_VERIFY
  - Determine COST_PQ_VERIFY via benchmarking on representative hardware.
  - Apply conservative upper-bounds to avoid DoS.

Precompile API (contract-facing)
--------------------------------
Expose a simple deterministic API:

    result: bool = precompile_pq_verify(pubkey: bytes, message: bytes, signature: bytes, scheme: str)

The precompile must:
- Validate argument types/lengths.
- Charge gas according to configured schedule.
- Return True/False.
- Never perform network or filesystem I/O.

Compatibility & migration
-------------------------
- Add a VM-side stub `vm_py.precompiles.pq_precompile` that contracts can call.
- For development, this stub can call `python-oqs` if present.
- For mainnet, replace the stub with the native precompile implementation.
- Provide a feature flag (runtime config) to enable/disable PQ verification precompile.

Security considerations
-----------------------
- Use audited PQ libraries (liboqs, PQClean).
- Pin library versions and use reproducible builds.
- Limit allowed schemes (Dilithium preferred initially) to reduce attack surface.
- Carefully design gas limits and maximum message/signature sizes.

Testing & CI
------------
- Unit tests: verify correct behavior with known vectors.
- Integration tests: sign messages with a chosen PQ library and verify in the VM.
- Fuzzing: randomized signature/message pairs to ensure no crashes.
- Benchmarking: measure verification latency on CI runners; use results to set gas.

Prototype implementation plan (short)
------------------------------------
1. Add a Python prototype wrapper `vm_py/precompiles/pq_precompile.py` that imports `oqs` if available (done).
2. Update `vm_py/stdlib/pq_verify.py` to call the precompile first and fall back to a dev shim (HMAC) when not present (done).
3. Add tests that exercise the real path when `python-oqs` is installed and the fallback otherwise.
4. Build a native precompile implementation in Rust (preferred) or C:
   - Create a small crate that links PQClean or liboqs and exposes a C API function `pq_verify`.
   - Compile it as a shared library and link into the runtime (or call via FFI from the VM host).
   - Expose the precompile to the VM syscalls table.
5. Benchmark and tune gas.

Deliverables for production rollout
----------------------------------
- Native precompile implementation with pinned dependency.
- Benchmarks & gas formula in protocol repo/spec.
- Migration guide & tests.
- Security audit of native code and build pipeline.


Appendix: quick dev commands
---------------------------
# Install liboqs + python bindings (Ubuntu example)
sudo apt-get install liboqs-dev liboqs0
python -m pip install oqs

# Run unit tests that use oqs
RUN_INTEGRATION_TESTS=1 python -m pytest tests/unit/test_pq_verify.py


