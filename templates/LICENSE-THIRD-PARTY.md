# Third-Party Notices (templates/)

This document lists third-party works that may be **bundled** or **referenced** from the `templates/` directory (starter templates, schemas, examples, docs). It is intended to help downstream users satisfy attribution and license-compliance obligations when copying or redistributing these files.

> **Scope:** This file covers only the `templates/` subtree. Other folders in this repository may ship their own third-party notices.

---

## 1) Current inventory

As of this version, the `templates/` directory contains **original works** authored for this project (template JSON, schemas, README files, and docs). No third-party source files are *bundled verbatim* here.

If you later copy in upstream schemas/snippets (for example, a JSON Schema meta-schema or an OpenRPC example), you **must** update the inventory tables below and include full license text if the upstream license requires it.

---

## 2) Referenced specifications (no code bundled)

The templates are designed to interoperate with the following publicly available specifications. Merely **referencing** a specification or implementing it from scratch does **not** make it a derivative work, but it’s good practice to acknowledge the sources:

- **JSON Schema** (IETF drafts; json-schema.org) — used conceptually by our `*.schema.json` files for template validation.
- **OpenRPC** (open-rpc.org) — sometimes referenced by template metadata describing RPC surfaces.
- **CDDL** / Concise Data Definition Language (RFC 8610) — occasionally referenced for CBOR shape descriptions.

These references do not, by themselves, impose additional license obligations on the template files; if you copy **verbatim** text or meta-schema files from those sources, treat them as third-party artifacts and record them in the inventory.

---

## 3) How to add (and document) a third-party artifact

If you add any upstream file into `templates/` (even a lightly modified one), do the following:

1. **Keep the upstream license header** in the file if present.
2. **Record the artifact** in the inventory table below (Name, Version/Commit, Origin URL, SPDX license, Path, Modifications).
3. If the license requires including the full text (e.g., Apache-2.0, BSD-2-Clause/3-Clause, MIT), add it under **Appendix A: License texts** and link it from the table.
4. If you vendor multiple licenses, ensure they’re compatible with this repository’s license and your distribution channel.

### Inventory of bundled third-party files (to be maintained)

| Name | Version / Commit | Origin | License (SPDX) | Local Path | Modifications |
|------|-------------------|--------|-----------------|------------|---------------|
| *(none at this time)* | — | — | — | — | — |

> Tip: If you copy a file like a JSON Schema meta-schema, list the exact revision/tag and URL, and paste the upstream license into Appendix A.

---

## 4) Policy notes

- **Prefer permissive licenses** (MIT, BSD-2/3-Clause, Apache-2.0, CC-BY where appropriate for docs/spec excerpts).
- **Avoid copyleft** (GPL/LGPL/AGPL) in templates unless there is a clear, accepted reason and downstream usage is considered.
- **IETF RFC text** is generally distributed under the IETF Trust’s legal provisions; quoting small excerpts for documentation with attribution is usually acceptable. If you include substantial portions, follow the Trust’s guidance and include the proper legend.
- **Trademarks and logos** remain property of their respective owners. Do not copy vendor logos into templates unless permitted.

---

## 5) Suggested compliance workflow

Although this directory is mostly hand-authored, here’s a lightweight process to keep it clean:

1. **Before adding** an upstream file: confirm the license and compatibility.
2. **On addition**: update the Inventory table and include the license text (Appendix A) if required.
3. **On release**: re-scan the directory to ensure inventory and actual files match.

You can optionally use these tools at the repo root:
- [`reuse`](https://reuse.software/) to annotate files with SPDX headers.
- A simple script (e.g., `scripts/check_licenses.py`) that lists non-authored files and verifies they appear in the inventory.

---

## 6) FAQ

**Q: I used an upstream document as inspiration but wrote the schema myself. Must I list it?**  
A: Not typically—acknowledgment is nice, but the inventory is for **bundled** or **derived** files that include upstream content.

**Q: A template includes a short excerpt (≤ a few lines) from an RFC for clarity. Is that OK?**  
A: Small quotations with attribution are generally acceptable. If you include larger sections, treat it as a bundled third-party text and document it.

**Q: I copied an example schema from a blog under CC-BY-4.0. What do I do?**  
A: CC-BY requires attribution. Keep any attribution notices, add the blog URL, author, and license to the inventory, and include the CC-BY notice in Appendix A.

---

## 7) Contact

If you believe your work is used in `templates/` and is not attributed correctly, please open an issue with:
- The file path,
- The upstream source (URL, author, license),
- The requested attribution or corrective action.

---

## Changelog (for this notice)

- **v1.0** — Initial notice; no third-party artifacts bundled.

---

## Appendix A: License texts

*(Add full license texts here for any third-party files that require inclusion. Keep each block clearly labeled, for example: “MIT License – Upstream Project XYZ (commit abcdef)”.)*

