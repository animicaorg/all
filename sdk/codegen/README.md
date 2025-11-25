# Animica SDK Codegen — Strategy, Targets, Templates, and Naming Rules

This package contains the **language-agnostic model + normalization** plus **language-specific generators** used to emit strongly-typed contract client stubs from an Animica ABI (per `spec/abi.schema.json`). The generated stubs call through each language’s SDK transport (HTTP/WS), encode/decode ABI payloads deterministically, and provide ergonomic method signatures.

> ✅ Goals: **deterministic output**, **reproducible builds**, **safe type mapping**, **zero surprises across languages**.

---

## Directory Layout

sdk/
codegen/
README.md                ← you are here
common/
normalize.py           ← ABI normalization & validation
model.py               ← Lang-agnostic IR (functions, events, errors)
python/
gen.py                 ← Emits Python stubs
templates/
contract_client.j2   ← Jinja template for Python
typescript/
gen.ts                 ← Emits TypeScript stubs
templates/
contract_client.hbs  ← Handlebars template for TypeScript
rust/
gen.rs                 ← Emits Rust stubs
templates/
contract_client.rs.hbs
cli.py                   ← Unified CLI entrypoint

Language SDKs that consume generated code:
- **Python**: `sdk/python/omni_sdk/contracts/codegen.py` (runtime helpers)  
- **TypeScript**: `sdk/typescript/src/contracts/codegen.ts` (runtime helpers)  
- **Rust**: runtime is in `animica-sdk` (contract client module) plus the generated file

---

## Supported Language Targets

### Python
- **Runtime**: `omni_sdk` (requests/httpx + websockets)
- **Output**: a single module `<name>_client.py` exporting a class `<Name>Client`
- **Typing**: `typing.TypedDict`/`pydantic`-compatible dicts for results; `int` for numeric scalars (Python’s bigints)
- **Hex & bytes**: hex strings with `0x` prefix for wire; `bytes` accepted where ABI allows
- **Events**: generated `decode_<EventName>()` helpers + a generic `EventDecoder`

### TypeScript
- **Runtime**: `@animica/sdk` (fetch + WS), ES modules
- **Output**: `<name>Client.ts` with default export `class <Name>Client`
- **Typing**: `bigint` for numeric scalars in user API; wire remains hex strings
- **Events**: type-safe event payloads, `decodeLogs()` utility; tree-shakable

### Rust
- **Runtime**: `animica-sdk` crate
- **Output**: `mod <name>_client;` (or a file) exporting `struct <Name>Client`
- **Typing**: `u128`/`i128` for bounded values; feature-gated bigints when needed; wire as hex strings
- **Events**: strongly-typed structs; decoder returns `Result<Option<Event>, Error>`

---

## ABI Normalization & IR

All generators **depend on the same canonical IR** produced by `common/normalize.py`:

1. **Schema validation** against `sdk/common/schemas/abi.schema.json`.
2. **Canonical ordering** of functions/events/errors:
   - Sort by `(name, inputs_signature)` to be stable across inputs.
3. **Name sanitization** and **collision avoidance**:
   - Disallow empty names; trim whitespace.
   - Map reserved words → suffixed forms (see *Naming Rules*).
   - Disambiguate overloads using a **stable discriminator** derived from the normalized signature.
4. **Type normalization**:
   - Collapses equivalent forms (e.g., `"bytes"` vs `{"type":"bytes"}`) into a single IR.
   - Annotates numeric bounds (bit widths) when present.
5. **Stable IDs**:
   - For events and errors the IR carries a **signature string** and **topic/hash id** computed per spec domain rules (see `spec/domains.yaml`); the concrete hash function (e.g., SHA3-256) is not hardcoded in README—generators use the SDK’s hashing utility to match chain policy.

> The IR is intentionally small and lossy only where the wire format does not need additional context.

---

## Naming Rules (Applied Uniformly)

**General**
- Input names: snake_case in IR, source-order preserved.
- Empty or invalid identifiers are rejected.
- Unicode is preserved but non-identifier chars are transliterated or replaced with `_`.

**Reserved words mapping**
- Python: `from → from_`, `class → class_`, `def → def_`, etc.
- TypeScript: `default → default_`, `function → function_`, `class → class_`, etc.
- Rust: `type → r#type` (raw identifiers), otherwise `_` suffix.

**Overloaded functions** (same name, different inputs)
- Public API keeps the base name; **language-specific overload strategy**:
  - Python: single method with **runtime selector** (by arg count & types) + `@overload` stubs for typing.
  - TypeScript: **type overload signatures** where supported.
  - Rust: suffixed methods `<name>`, `<name>_v2`, `<name>_v3` using a **stable suffix** derived from the normalized selector (e.g., `_a1b2` short hash) to avoid breaking changes when adding overloads later.

**Event type names**
- `Event` structs/classes in PascalCase: `<Name>Event`.
- If two events collide post-sanitization, append a stable short suffix as above.

**Module/Class names**
- Contract client classes are PascalCase: `<Name>Client`.
- File/module names are kebab/snake case depending on the target.

---

## Type Mapping (User API vs Wire)

| ABI Type     | Python (API)     | TypeScript (API) | Rust (API)     | Wire (all)                  |
|--------------|------------------|------------------|----------------|-----------------------------|
| `bool`       | `bool`           | `boolean`        | `bool`         | JSON `true/false`           |
| `int/uintN`  | `int` (arbitrary)| `bigint`         | `i128/u128`*   | hex string `"0x..."`        |
| `bytes`      | `bytes`/hex str  | `Uint8Array`/hex | `Vec<u8>`      | hex string `"0x..."`        |
| `address`    | `str`            | `string`         | `String`       | bech32m string `anim1...`   |
| arrays/tuples| lists/tuples     | arrays/tuples    | `Vec<T>`/tuples| JSON arrays (hex where needed)|

\* For larger numeric widths, Rust falls back to feature-gated bigints or `String` wrappers—generator selects based on `bitWidth`.

---

## Emission Strategy

1. **Load ABI JSON** → `normalize.py` → IR.
2. **Render** using the target’s template (Jinja/Handlebars/minijinja/askama-like).
3. **Embed generator metadata** (version banner, source ABI hash).
4. **Write** to the requested output path; optionally format with language toolchain:
   - Python: `ruff/black` (opt-in).
   - TypeScript: `prettier` (opt-in).
   - Rust: `rustfmt` (opt-in).

**Determinism**: same ABI + same generator version ⇒ byte-for-byte identical output.

---

## CLI Usage

```bash
# Python entrypoint
python -m sdk.codegen.cli \
  --lang py|ts|rs \
  --abi path/to/abi.json \
  --out ./generated \
  --name Counter \
  [--class-name CounterClient] \
  [--module-name counter_client] \
  [--format] \
  [--banner] \
  [--no-events]

	•	--lang selects generator.
	•	--name is the contract display name used for class/type naming.
	•	--format runs optional formatter if found on PATH.
	•	--no-events skips event decoder emission (rarely useful).

Node/TS wrapper is provided via sdk/codegen/package.json so you can:

# TypeScript-friendly npx runner (wraps the same CLI)
npx animica-codegen --lang ts --abi abi.json --out src/contracts --name Counter


⸻

Integration with SDKs

Generated stubs do not reimplement transport or encoding. They import:
	•	Python: omni_sdk.tx.encode, omni_sdk.tx.send, omni_sdk.contracts.client.BaseContractClient utilities.
	•	TypeScript: @animica/sdk contracts/client helpers and codec utils.
	•	Rust: animica-sdk contract client primitives and hex/bytes utilities.

This keeps the surface small and ensures stubs upgrade when runtime libraries improve.

⸻

Events & Topics
	•	The event signature string is built per ABI spec (name + canonical input types).
	•	The topic id is hashed using the chain’s configured hash domain (see spec/domains.yaml).
	•	Decoders:
	•	Accept raw logs { address, topics, data, index }.
	•	Match by primary topic id; decode data payload via ABI types.
	•	Are forward-compatible: unknown topics return None (or Result::Ok(None) in Rust).

⸻

Errors
	•	ABI “errors” are represented as structured types and may be emitted as:
	•	Python exceptions with typed payload.
	•	TypeScript Error subclasses with metadata.
	•	Rust error enums.
	•	Generators only include error types present in the ABI.

⸻

Versioning & Reproducibility
	•	Every emission embeds:
	•	Generator version (from sdk/codegen/README.md or __version__).
	•	ABI content hash (sha3_256 of the normalized JSON).
	•	CI can diff generated outputs to detect drift.
Use sdk/common/test_vectors/abi_examples.json to assert stable emission.

⸻

Testing
	•	Unit tests validate:
	•	ABI parsing & normalization (common/normalize.py).
	•	Template rendering on the counter_abi.json.
	•	SDK tests (per language) exercise the generated client against a devnet:
	•	Python: sdk/python/tests/test_contract_codegen.py
	•	TypeScript: sdk/typescript/test/contract_codegen.test.ts
	•	Rust: sdk/rust/tests/contract_codegen.rs

⸻

Extensibility
	•	Add new targets by implementing:
	1.	A renderer module (<lang>/gen.*) that consumes the IR.
	2.	Minimal templates in <lang>/templates/.
	3.	A target registration in cli.py.
	•	Extend type mapping by updating common/model.py and per-target translators only.
Never bypass the IR; all backends must stay in lockstep.

⸻

Example (Python stub sketch)

class CounterClient(BaseContractClient):
    async def inc(self, *, sender: Signer, gas: int | None = None) -> TxHash:
        data = abi.encode_call("inc", [])
        tx = build_call_tx(sender.address, self.address, data, gas=gas)
        submit = await send(self.http, sender, tx, chain_id=self.chain_id)
        return submit.tx_hash

    async def get(self) -> int:
        # read path if node supports simulation
        return await self.simulate("get", [])

The actual emitted code includes full typing, docstrings, and error handling.

⸻

Gotchas
	•	Overloads: Avoid them when possible; while supported, they reduce clarity.
	•	Numeric bounds: Rust may require feature flags for >128-bit integers.
	•	Domain hashing: Generators defer to the SDK hashing utils to respect chain policy.

⸻

License
	•	Matches repository license; third-party template engines are noted in their subfolders.

