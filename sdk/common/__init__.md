# `sdk/common/`

Shared, language-agnostic assets used by all SDKs (Python, TypeScript, Rust).

## What lives here
- `schemas/` — canonical copies of spec artifacts used by codegen and validators:
  - `abi.schema.json`
  - `openrpc.json`
- `test_vectors/` — cross-language fixtures for ABI, txs, headers, proofs, etc.
- `examples/` — tiny ABIs/manifests used by codegen demos.

## Notes
- Files here are **data only**; no runtime code. Consumers should treat these as
  read-only inputs.
- Schemas are copied from the repo’s `spec/` directory and stamped with a
  `"$origin"` field for traceability (source path + commit).
- Keep changes synchronized across languages; run `make e2e` in `sdk/` after any
  schema or vector updates.

See `../spec_pointers.md` for the refresh workflow.
