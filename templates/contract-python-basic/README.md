# contract-python-basic

A minimal, production-ready Python smart-contract starter for the **Animica VM (vm_py)**.
This template scaffolds a deterministic `Counter`-style contract, a manifest, a tiny
local test, and a deploy script wired to the **Python SDK**. It’s intentionally small
but follows the same structure and rules as the stdlib contracts, so you can safely
grow it into a real project.

---

## What you get

When rendered, this template produces a self-contained folder with:

- `contract.py` — a deterministic contract (default: `Counter`) with:
  - Storage get/set via `stdlib.storage`
  - Event emission via `stdlib.events`
  - Two public methods: `get()` and `inc(delta: int = 1)`
  - Clear docstrings → ABI generation compatibility
- `manifest.json` — ABI + metadata in the canonical schema (kept in sync with `spec/abi.schema.json`)
- `tests_local.py` — simple local unit tests that run on **vm_py** (no node required)
- `deploy_and_test.py` — end-to-end demo: build → deploy → call via **sdk/python**

> The filenames above match the outputs configured in `template.json`.

---

## Inputs (template variables)

The template accepts the following variables (defaults shown):

| Variable        | Type   | Default          | Description                                                     |
|----------------|--------|------------------|-----------------------------------------------------------------|
| `project_name` | string | `My Contract`    | Human-readable name (used in readme/manifest metadata).         |
| `project_slug` | string | `my-contract`    | Filesystem-friendly slug; used for output directory names.      |
| `contract_class` | string | `Counter`      | Top-level Python class name of the contract.                    |
| `license`      | string | `Apache-2.0`     | SPDX license identifier set in headers/manifest.                |
| `description`  | string | *short summary*  | Manifest description and README tagline.                        |
| `init_value`   | int    | `0`              | Initial counter value (used by constructor/init pattern).       |

You can provide variables through:
- a JSON file (recommended), e.g. `variables.json`
- command line `--set key=value` overrides

---

## Rendering the template

### Using the template engine CLI

The repo ships a tiny renderer under `templates/engine/cli.py`.

**Dry run (show what would be created):**
```bash
python -m templates.engine.cli render \
  --template templates/contract-python-basic \
  --out ./contracts/examples/my-contract \
  --vars templates/contract-python-basic/variables.json \
  --dry-run

Render to disk:

python -m templates.engine.cli render \
  --template templates/contract-python-basic \
  --out ./contracts/examples/my-contract \
  --vars templates/contract-python-basic/variables.json

Override a couple of values inline:

python -m templates.engine.cli render \
  --template templates/contract-python-basic \
  --out ./contracts/examples/metered-counter \
  --vars templates/contract-python-basic/variables.json \
  --set project_name="Metered Counter" \
  --set project_slug="metered-counter" \
  --set contract_class="MeteredCounter" \
  --set init_value=42

The renderer will validate variables against templates/schemas/variables.schema.json
and the template spec against templates/schemas/template.schema.json.

⸻

Quickstart after rendering

From the output directory (e.g., contracts/examples/my-contract):
	1.	Create a venv and install deps

python -m venv .venv && . .venv/bin/activate
pip install -r ../../../contracts/requirements.txt

	2.	Run the local unit tests (vm_py only)

python tests_local.py

	3.	Build, deploy to devnet, and exercise calls

python deploy_and_test.py \
  --rpc "${RPC_URL:-http://127.0.0.1:8545}" \
  --chain-id "${CHAIN_ID:-1337}"

The deploy script uses the Python SDK and expects a running devnet node
(see tests/devnet or ops/docker/docker-compose.devnet.yml).

⸻

Contract outline (default Counter)
	•	State: stores a single integer value at a fixed storage key.
	•	Methods
	•	get() -> int: returns the current value.
	•	inc(delta: int = 1) -> int: increases the value by delta (≥ 0), emits an event, returns new value.
	•	Determinism: uses the VM stdlib only; no I/O, no clocks, no randomness in contract logic.
	•	Gas: simple operations; suitable as a minimal example for gas accounting and events.

⸻

Linting & typing (recommended)

Use the repository’s standard config:

ruff check .
mypy --config-file ../../../contracts/mypy.ini .

Both the contract and the generated tests are written to pass strict-ish typing
and determinism lints (see contracts/CODESTYLE.md for the allowed subset).

⸻

Customizing the template
	•	Rename the class by setting contract_class.
	•	Add new public methods following the ABI docstring pattern used in stdlib examples.
	•	Expand events using the stdlib.events helper and keep names/types stable to avoid breaking ABI.
	•	If you need access lists or more complex storage, consider the patterns in contracts/stdlib/*.

Pro tip: Start here, then migrate to your own repo while keeping the manifest schema and
deterministic style unchanged. This guarantees that on-chain verification via studio-services works.

⸻

Troubleshooting
	•	Render failed: Check variable names and types; run with --debug to print the resolved model.
	•	Deploy failed: Ensure node RPC is reachable, CHAIN_ID matches devnet genesis, and your account is funded.
	•	ABI mismatch: Rebuild the manifest and confirm function names/signatures match the contract docstrings.

⸻

License

This template content is provided under the license noted in your variables (license, default Apache-2.0).
Generated projects may include additional third-party licenses depending on your choices.

