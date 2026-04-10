# Third-Party Notices for `pq/`

This module may **dynamically link** to vendor libraries and/or include small,
clean-room wrappers around well-known post-quantum primitives. Below are
attributions and license summaries for components that may be used at build or
runtime when the corresponding feature flags are enabled.

> **Note:** This file is informational. Canonical license texts live at the
> repository root in `THIRD_PARTY_NOTICES.md` and/or within vendored subtrees
> when applicable. Where a project is dual-licensed, either license may be used.

---

## Contents

| Component | Purpose | License (SPDX) |
|---|---|---|
| Open Quantum Safe **liboqs** | High-performance PQ signatures/KEMs (Dilithium, ML-KEM/Kyber, etc.) | Apache-2.0 |
| **PQClean** | Cleaned, portable refs for PQC (used for tests/fallbacks) | CC0-1.0 and/or MIT (per algorithm) |
| **CRYSTALS-Dilithium** reference | PQ signature scheme reference vectors | Public-Domain / CC0-1.0 (via PQClean) |
| **ML-KEM / Kyber** reference | PQ KEM reference vectors | Public-Domain / CC0-1.0 (via PQClean) |
| **SPHINCS+** reference | Stateless hash-based signature reference | CC0-1.0 |
| **Keccak Code Package** | SHA3 / SHAKE sponge permutations | CC0-1.0 |
| **BLAKE3** (optional) | Fast hash for non-consensus utilities | Apache-2.0 OR MIT |
| **Bech32/Bech32m** (BIP-0173/350) | Address encoding | MIT |
| **msgspec** / **cbor2** | CBOR encoding/decoding for tests | BSD-3-Clause / MIT |
| **pyca/cryptography** (optional) | AEADs for P2P where available | Apache-2.0 OR BSD-3-Clause |

---

## Notices

### Open Quantum Safe (liboqs) — Apache-2.0

Copyright © The Open Quantum Safe project contributors.

This product may dynamically link against **liboqs** to provide hardware-optimized
implementations of **Dilithium** (signatures) and **ML-KEM / Kyber** (KEM) where
available. You must comply with **Apache-2.0** when distributing binaries that
bundle liboqs.

- Source: https://openquantumsafe.org / https://github.com/open-quantum-safe/liboqs
- SPDX: Apache-2.0
- NOTICE: This distribution includes software developed by the Open Quantum Safe project.

### PQClean — CC0-1.0 and/or MIT

The **PQClean** project provides cleaned, portable C implementations and test
vectors for many PQC candidates. Individual algorithms carry **CC0-1.0** or **MIT**
licenses; consult the per-algorithm `LICENSE` files in the PQClean tree.

- Source: https://github.com/PQClean/PQClean
- SPDX: CC0-1.0 and/or MIT (per directory)
- NOTICE: Some test vectors and optional fallback code paths originate from PQClean.

### CRYSTALS-Dilithium & ML-KEM / Kyber (via PQClean)

References and known-answer tests for **Dilithium** (signature) and **Kyber**
(KEM). Integrated through PQClean; see PQClean licensing above.

- SPDX: Public-Domain / CC0-1.0 (as provided in PQClean)

### SPHINCS+ — CC0-1.0

Stateless hash-based signatures (SHAKE-128s variant). Reference code and vectors
are made available under **CC0-1.0**.

- Source: https://sphincs.org / https://github.com/sphincs/sphincsplus
- SPDX: CC0-1.0

### Keccak Code Package — CC0-1.0

Permutation functions used by SHA3/SHAKE. Where used directly or via Python
bindings, the Keccak team provides the code under **CC0-1.0**.

- Source: https://keccak.team
- SPDX: CC0-1.0

### BLAKE3 — Apache-2.0 OR MIT (optional)

If enabled for **non-consensus** utilities (e.g., fast tooling), this project
may link to BLAKE3.

- Source: https://github.com/BLAKE3-team/BLAKE3
- SPDX: Apache-2.0 OR MIT

### Bech32 / Bech32m — MIT

Address encoding based on BIP-0173 and BIP-0350. Portions adapted or re-implemented
with attribution.

- Source: https://github.com/bitcoin/bips
- SPDX: MIT

### msgspec / cbor2 — BSD-3-Clause / MIT

Used in testing utilities for CBOR round-trip and vector generation (not consensus).

- msgspec: BSD-3-Clause — https://github.com/jcrist/msgspec
- cbor2: MIT — https://github.com/agronholm/cbor2

### pyca/cryptography — Apache-2.0 OR BSD-3-Clause (optional)

May provide AEADs for the P2P secure channel where system libraries are preferred.

- Source: https://github.com/pyca/cryptography
- SPDX: Apache-2.0 OR BSD-3-Clause

---

## Redistribution Checklist

If you distribute a binary or a Docker image that includes **pq/** with any of
the optional native backends enabled:

1. Include this file and the root `THIRD_PARTY_NOTICES.md`.
2. Include the **full text** of Apache-2.0, MIT, BSD-3-Clause, and/or CC0-1.0
   licenses that apply to the bundled components.
3. Preserve **copyright** and **NOTICE** files from upstream.
4. Document which features are enabled (e.g., `--with-liboqs`, `--with-blake3`).

---

## No Cryptographic Warranty

This software is provided “AS IS”, without warranty of any kind, express or
implied, including but not limited to the warranties of merchantability, fitness
for a particular purpose and non-infringement. You are responsible for ensuring
that your usage complies with local laws and export controls.

