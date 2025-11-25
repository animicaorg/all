# Third-Party Notices — @animica/studio-wasm

This package redistributes a small set of upstream artifacts to run the Animica Python VM in the
browser via **Pyodide/WASM**. This document lists third-party components, copyrights, and licenses
that are shipped at **runtime** by this package. (Build-time dev dependencies such as Vite/Vitest,
TypeScript, Playwright, etc. are not redistributed.)

If you enable optional wheels or add additional Python packages beyond the defaults, you **must**
review those packages’ licenses and update this notice file for your distribution.

---

## Components Included at Runtime

| Component | What we ship | License | Copyright |
|---|---|---|---|
| **Pyodide** | `pyodide.js`, `pyodide.wasm`, `pyodide.data` (version pinned in `pyodide.lock.json`) | **MPL-2.0** | © 2018-present Pyodide contributors |
| **CPython** (as part of Pyodide) | CPython standard library compiled to WASM (subset transitively included by Pyodide) | **PSF License 2.0** | © Python Software Foundation |
| **Emscripten** (toolchain used by Pyodide) | Runtime portions included in Pyodide bundles | **MIT** | © 2010-present Emscripten authors |
| **WebAssembly / WASI support files** (as provided by Pyodide) | Minimal shims included in Pyodide bundles | Upstream license as provided by Pyodide (MPL-2.0 or MIT depending on file) | Respective authors |

> **Note:** By default, this package does **not** bundle third-party Python wheels (e.g., NumPy,
pandas). If you later add them (via `src/pyodide/packages.ts`), consult the Pyodide package catalog
and include their licenses and attributions here (for example: NumPy — BSD-3-Clause).

---

## Source Locations

- Vendored binaries and related files are located under: `studio-wasm/vendor/`
- Version and checksums are pinned in: `studio-wasm/pyodide.lock.json`

---

## License Texts & Notices

### Mozilla Public License 2.0 (MPL-2.0) — Notice

> This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.  
> If a copy of the MPL was not distributed with this file, You can obtain one at  
> https://mozilla.org/MPL/2.0/.

Pyodide and select files included in its distribution are licensed under MPL-2.0. Where required,
file-level headers are preserved within the vendored artifacts. You must comply with MPL-2.0 when
modifying or redistributing covered files.

---

### Python Software Foundation License 2.0 (PSF-2.0) — Notice

CPython and the Python standard library are distributed under the **Python Software Foundation
License** (version 2). See https://docs.python.org/3/license.html

---

### MIT License — Emscripten (Runtime Portions)

MIT License

Copyright (c) 2010-present Emscripten authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights…
[full MIT license text applies]

The full MIT text is widely available; if not present in your distribution, see:
https://opensource.org/licenses/MIT

---

## Compliance Guidance

- Keep `pyodide.lock.json` in sync with the artifacts in `vendor/`.  
- If you patch or replace any MPL-2.0 files, retain notices and provide the license text.  
- When adding Python wheels (e.g., via `micropip` or prebundling in `vendor/`), list each package,
  its version, license, and copyright.

---

## Contact

For questions about license compliance in this package, open an issue in the repository and include:
- The exact versions in `pyodide.lock.json`
- Any additional Python wheels you bundled
- How/where the artifacts are served in your build

