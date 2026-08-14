# License fix: root LICENSE.txt is the wrong license file

## Problem

The file `LICENSE.txt` at the repository root is **not the project license** — it is the
SIL Open Font License (OFL) 1.1 for the **Inter font** ("Copyright (c) 2016 The Inter
Project Authors (https://github.com/rsms/inter)"). Inter is merely a webfont used by the
website (`website/public/fonts/inter-variable.woff2`; a stray `Inter.zip` also sits at the
repo root).

Because GitHub's license detection (licensee) reads the root license file, the
`animicaorg/all` repository is misdetected as OFL-licensed instead of Apache-2.0. That
misleads humans, package scanners, SBOM tooling, and coding agents that check the license
before reusing code.

The codebase's actual license is **Apache-2.0**, as declared in `python/pyproject.toml`:

```toml
license = { text = "Apache-2.0" }
...
"License :: OSI Approved :: Apache Software License",
```

(and as published on PyPI for the `animica` package).

## Fix

1. Add the full Apache-2.0 text as `LICENSE` at the repository root (file provided
   alongside this note). This matches the `python/pyproject.toml` declaration and makes
   GitHub detect Apache-2.0.
2. Move the Inter OFL text out of the root and next to the font it covers, e.g.:
   `git mv LICENSE.txt website/public/fonts/LICENSE-Inter-OFL.txt`
   (keep it — the OFL requires the license to accompany the font — just not at the root).
3. Optionally list Inter/OFL in `THIRD_PARTY_NOTICES.md` (which already exists at the
   root) so the attribution stays discoverable.
4. Optionally remove `Inter.zip` from the repo root (build artifact, not source).

After the change, verify: the GitHub repo page should show "Apache-2.0 license" in the
sidebar, and `CITATION.cff` (`license: Apache-2.0`) stays consistent.
