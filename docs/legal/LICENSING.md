# Licensing, Exceptions & Third-Party Notices

This document summarizes the licensing model for the Animica project, lists exceptions (e.g., brand assets), and points to third-party attributions. It complements per-package `LICENSE`/`LICENSE-THIRD-PARTY.md` files.

> TL;DR  
> - **Code is dual-licensed**: **Apache-2.0 OR MIT** (you may choose either), unless a submodule explicitly states otherwise.  
> - **Logos/wordmarks are not open-licensed** (see Brand & Trademarks).  
> - Third-party components keep their **original licenses**; we ship notices and provide scripts to regenerate them.

---

## 1) Project Licenses

### 1.1 Default (Dual License)
Unless noted below, all original source code in this repository is:
- **Apache License, Version 2.0 (Apache-2.0)** **OR**
- **MIT License**

Choose either license at your option. SPDX expression:  
`SPDX-License-Identifier: Apache-2.0 OR MIT`

Why dual? The Rust/Python/TypeScript ecosystems commonly use Apache-2.0 OR MIT to maximize compatibility. Apache-2.0 includes an express **patent grant**; MIT is simpler but **does not** include a patent grant.

### 1.2 Module-level Notes

| Path / Package                               | License (unless file says otherwise)        | Notes |
|---------------------------------------------|---------------------------------------------|-------|
| `core/`, `consensus/`, `proofs/`, `mempool/`, `p2p/`, `execution/`, `vm_py/`, `capabilities/`, `aicf/`, `randomness/`, `rpc/`, `da/`, `mining/` | Apache-2.0 OR MIT | Default project license. |
| `zk/` (verifiers, adapters, registry, integration, tests, bench) | Apache-2.0 OR MIT | ZK native crate follows Rust norms (see below). |
| `zk/native/` (Rust crate: `animica_zk_native`) | Apache-2.0 OR MIT | Depends on Apache-2.0 OR MIT upstream (arkworks, pyo3). |
| `sdk/` (Python, TypeScript, Rust)            | Apache-2.0 OR MIT | `sdk/LICENSE` mirrors repo root. |
| `wallet-extension/` (MV3)                    | Apache-2.0 OR MIT | Browser extension code and tests. |
| `studio-wasm/`                               | **Mixed**: project code Apache-2.0 OR MIT; **Pyodide and shipped Python wheels retain upstream licenses (incl. MPL-2.0)** | See `studio-wasm/LICENSE-THIRD-PARTY.md`. |
| `studio-services/` (FastAPI)                 | Apache-2.0 OR MIT | See `studio-services/LICENCE-THIRD-PARTY.md`. |
| `website/`                                   | MIT (site content & Astro theme code)       | See `website/package.json`. |
| `installers/`                                | Apache-2.0 OR MIT                           | Per-OS packaging scripts may embed vendor-specific EULAs. |
| `docs/` (this directory)                     | **CC BY-SA 4.0** unless a file declares otherwise | Documentation and diagrams are share-alike. Code snippets are under the repo’s default license. |

> If any file includes an **explicit SPDX header**, that header governs that file.

---

## 2) Third-Party Components (Non-Exhaustive Map)

The project uses a number of upstream libraries. They remain under their original licenses:

- **Cryptography / PQ / ZK**
  - **arkworks** (`ark-ff`, `ark-bn254`, `ark-ec`, …): *Apache-2.0 OR MIT*
  - **pyo3** (Python bindings): *Apache-2.0 OR MIT*
  - **liboqs** / OQS bindings (optional): *Apache-2.0*
  - **py_ecc** (Python BN254/BLS helpers): *MIT*
  - **snarkjs**, **circomlib**: *MIT*
- **Web & Services**
  - **FastAPI** / **Pydantic**: *MIT*
  - **Uvicorn**: *BSD-3-Clause*
  - **msgspec**: *BSD-3-Clause*
  - **Websockets**, **aioquic**: *BSD / MIT*
- **Frontend**
  - **Astro**, **React**, **Vite**, **TailwindCSS**: *MIT*
  - **Tauri**: *Apache-2.0 OR MIT*
- **WASM**
  - **Pyodide**: *MPL-2.0* (and embedded Py packages each with their own license)
- **Tooling**
  - **Prometheus clients**: *Apache-2.0 / MIT* (per language)
  - **Flutter** (for installers’ wallet builds): *BSD-3-Clause*

For each package that redistributes or links to third-party code, see the accompanying notice file:
- `studio-wasm/LICENSE-THIRD-PARTY.md`
- `installers/LICENSE-THIRD-PARTY.md`
- Any `LICENSE-THIRD-PARTY.md` colocated with a package

---

## 3) Brand & Trademarks (Exception)

- The **Animica** name, logo, and wordmark (e.g., files under `docs/assets/brand/` and `website/public/icons/`) are **trademarks** of their respective holders and **not** licensed under Apache-2.0/MIT.
- You may not use the marks in a way that suggests endorsement without prior written permission.
- For forks/derivatives, replace marks and update app identifiers.

---

## 4) Binaries, Installers & App Stores

- Platform bundles (DMG/PKG/MSIX/AppImage/Flatpak/DEB/RPM) may contain **platform runtimes** and **system libraries** under their own licenses.
- The **EULA** for wallet installers lives at `installers/wallet/EULA.txt`. Packagers must include it where required by the platform.
- macOS notarization and Sparkle feeds are **distribution mechanisms**; they do not change code licenses.

---

## 5) Patents

- Under the **Apache-2.0** option, contributors grant a **patent license** per §3 of the license.
- Under the **MIT** option, there is **no express patent grant**. For patent-sensitive deployments, prefer the Apache-2.0 option.

---

## 6) Cryptography & Export

This project includes cryptographic software (PQ signatures/KEMs, AEAD, ZK verifiers). You are responsible for compliance with local laws and export regulations.

---

## 7) SPDX & Headers

Please include SPDX headers in new files:

- **Apache-2.0 OR MIT (dual)**  
  `// SPDX-License-Identifier: Apache-2.0 OR MIT`
- **Docs (CC BY-SA 4.0)**  
  `<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->`

---

## 8) Regenerating Third-Party Notices (SBOM & Licenses)

We provide a typical toolchain to (re)generate license inventories:

```bash
# Rust (licenses)
cargo install cargo-license
cargo license --json > THIRD_PARTY_rust.json

# Python (licenses)
pip install pip-licenses
pip-licenses --format=json --with-system --with-license-file > THIRD_PARTY_python.json

# Node/TS (licenses)
npx license-checker --json > THIRD_PARTY_node.json

# SBOM (all)
curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b . v1.0.0
./syft packages dir:. -o spdx-json=SBOM.spdx.json

Package-level scripts may wrap these and commit aggregated results into LICENSE-THIRD-PARTY.md files.

⸻

9) Contributions
	•	By contributing, you agree your contributions are licensed under Apache-2.0 OR MIT (dual).
	•	Please add SPDX headers and keep third-party attributions intact.

⸻

10) Contact
	•	Licensing questions: <legal@animica.org> (example)
	•	Security / responsible disclosure: see docs/security/RESPONSIBLE_DISCLOSURE.md

⸻

Appendix A — Canonical License Texts
	•	Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0
	•	MIT License: https://opensource.org/licenses/MIT
	•	CC BY-SA 4.0 (docs): https://creativecommons.org/licenses/by-sa/4.0/
	•	MPL-2.0 (Pyodide): https://www.mozilla.org/en-US/MPL/2.0/

