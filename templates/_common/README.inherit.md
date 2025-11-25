# {{ project_title | default("Your Project Name") }}

> _This README is **inherited** from a shared Animica template. It is meant to be **merged** into your repo’s `README.md` by the template engine, not edited manually. Local, project-specific content should live outside the auto-managed blocks described below._

<!--
READ THIS FIRST
---------------
This document is a *source* for README generation. The renderer will:
  • Substitute variables like {{ project_name }}, {{ repo_url }}, {{ license }} …
  • Keep, update, or re-create managed blocks delimited by BEGIN/END markers.
  • Respect any content you place OUTSIDE managed blocks.

If you must customize a managed block, copy its content into a new section
outside the markers and disable that block via variables (see "Toggles" below).
-->

<!-- BEGIN:BADGES (managed) -->
<p align="left">
  <a href="{{ repo_url | default('#') }}"><img alt="Repo" src="https://img.shields.io/badge/repo-{{ project_name | default('project') }}-blue"></a>
  <a href="{{ ci_url | default('#') }}"><img alt="CI" src="https://img.shields.io/badge/CI-{{ ci_provider | default('GitHub Actions') }}-success"></a>
  <a href="{{ license_url | default('#') }}"><img alt="License" src="https://img.shields.io/badge/license-{{ license | default('Apache--2.0') }}-informational"></a>
  <a href="{{ docs_url | default('#') }}"><img alt="Docs" src="https://img.shields.io/badge/docs-{{ docs_badge | default('Read the Docs') }}-blueviolet"></a>
</p>
<!-- END:BADGES -->

{{ description | default("Concise one-liner about the project. Who it’s for, and what it enables.") }}

---

## Table of contents

- [At a glance](#at-a-glance)
- [Quickstart](#quickstart)
  - [Prerequisites](#prerequisites)
  - [Clone & bootstrap](#clone--bootstrap)
  - [Run a local devnet](#run-a-local-devnet)
  - [Run tests](#run-tests)
  - [Lint & format](#lint--format)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [Build, release & versioning](#build-release--versioning)
- [Security & reporting](#security--reporting)
- [License](#license)
- [Template inheritance](#template-inheritance)
  - [Managed blocks](#managed-blocks)
  - [Variables](#variables)
  - [Toggles](#toggles)
  - [Re-rendering safely](#re-rendering-safely)
- [FAQ](#faq)

---

## At a glance

- **Language(s):** {{ languages | default("Python + TypeScript (optional)") }}
- **Runtime targets:** {{ runtimes | default("CPython 3.11+, Node 20+") }}
- **Purpose:** {{ purpose | default("Reference node & tooling around the Animica stack.") }}
- **Key modules (may vary by template):**
  - `core/` (types, encoding, DB)
  - `rpc/` (JSON-RPC/WS services)
  - `consensus/` (PoIES scoring/retarget)
  - `proofs/` (HashShare/AI/Quantum/Storage/VDF)
  - `mempool/`, `p2p/`, `da/`, `execution/`, `vm_py/`
  - `capabilities/`, `aicf/`, `randomness/`
  - `tests/` (unit, integration, fuzz, e2e, bench)
  - `ops/` (Docker/K8s/Helm/Ansible)
- **Batteries included:** pre-commit, Ruff, yamllint, codespell, Semgrep (light), Make targets.

---

## Quickstart

### Prerequisites

- **Python** {{ python_version | default("3.11+") }}
- **Node.js** {{ node_version | default(">=20, npm >=10") }} (if using TS packages)
- **Make** (or run the equivalent `pip`/`npm` commands manually)
- **Docker** {{ docker_required | default("(optional for devnet/e2e)") }}

> Tip: this repo ships a shared pre-commit config. It’s fast, cross-platform, and prevents many review nits.

### Clone & bootstrap

```bash
git clone {{ repo_url | default('https://example.com/your/repo.git') }}
cd {{ project_dir | default('repo') }}

# Python (uv or venv)
python -m venv .venv && source .venv/bin/activate
pip install -U pip wheel
pip install -r requirements.txt 2>/dev/null || true  # if present

# Node (optional)
npm ci 2>/dev/null || true

# Install git hooks
pre-commit install

Run a local devnet

Works if your template includes ops/docker or tests/devnet.

make devnet-up            # or: docker compose -f ops/docker/docker-compose.devnet.yml up -d
./tests/devnet/wait_for_services.sh
make smoke-devnet         # quick health checks (RPC, explorer, metrics)

Stop & clean:

make devnet-down          # or: docker compose ... down -v

Run tests

pytest -q                 # Python unit/integration
pytest -m "not e2e"       # skip e2e smoke if short on time
npm test                  # TypeScript unit tests (if present)

Coverage & reports typically land in tests/reports/.

Lint & format

ruff check . --fix
ruff format .
yamllint .
codespell
pre-commit run --all-files


⸻

Project layout

Your actual tree may differ; remove sections that don’t apply.

{{ project_name | default('repo') }}/
├─ core/           # canonical types, CBOR/JSON codecs, DB adaptors
├─ rpc/            # FastAPI app, JSON-RPC dispatcher, WS streams
├─ consensus/      # PoIES scoring, retarget, fork choice
├─ proofs/         # proof verifiers + schema
├─ mempool/        # admission, fee market, eviction
├─ p2p/            # transports, gossip, sync
├─ da/             # data availability (NMT, RS, retrieval API)
├─ execution/      # state machine (transfers/events)
├─ vm_py/          # deterministic Python VM
├─ capabilities/   # AI/Quantum/DA/Randomness host syscalls
├─ aicf/           # AI Compute Fund (registry, queue, settlement)
├─ randomness/     # commit→reveal→VDF beacon
├─ wallet-extension/, sdk/, studio-*   # optional UX/SDK components
├─ tests/          # unit/property/integration/fuzz/e2e/bench/load
└─ ops/            # Docker, K8s, Helm, observability, runbooks


⸻

Configuration
	•	Copy the sample env:
cp .env.example .env and fill RPC_URL, CHAIN_ID, optional keys.
	•	Module-local configs live under */config.py or ops/docker/config/*.toml.
	•	Chain parameters come from spec/params.yaml and genesis JSON.

⚠️ Never commit real credentials. The CI and ops/ tooling assume throwaway or test keys.

⸻

Build, release & versioning
	•	Versioning: SemVer (MAJOR.MINOR.PATCH) with git describe helpers in each module (*/version.py).
	•	Releases: Tag the repo (vX.Y.Z), push images if applicable (ops/scripts/push_images.sh), and update Helm chart appVersion.
	•	Changelog: For SDK/templates, keep CHANGELOG.md up to date and reference test vectors updated.

Typical flow:

make test                 # green tests
make bench                # compare against baselines
git tag vX.Y.Z && git push --tags


⸻

Security & reporting
	•	Review the checklists in contracts/SECURITY.md and vm_py/audit/.
	•	Report security issues privately to {{ security_contact | default(“security@yourdomain.example”) }} with repro steps and affected versions.

⸻

License

{{ license | default(“Apache-2.0”) }} © {{ copyright_owner | default(“Animica Contributors”) }}, {{ year | default(“2025”) }}.
See LICENSE for details.

⸻

Template inheritance

This README is built from managed blocks and variables so you can safely re-render the template as it evolves without stomping local edits.

Managed blocks

Blocks are delimited by HTML comments:

<!-- BEGIN:SECTION_ID -->
… content controlled by the template …
<!-- END:SECTION_ID -->

	•	Do not edit inside managed blocks; changes will be overwritten.
	•	To customize, copy the block outside the markers, then set a toggle to disable re-rendering of that block (see below).

Managed blocks commonly used here:
	•	BADGES
	•	FEATURES (optional)
	•	QUICKSTART (optional)

Variables

The renderer substitutes Jinja-style variables. Common ones:

Variable	Meaning	Example
project_name	slug / directory name	animica-node
project_title	human title	Animica Node
description	one-line summary	Reference node & tooling
repo_url	canonical Git URL	https://github.com/org/repo
docs_url	external docs	https://docs.example.com
ci_url	CI badge link	GitHub Actions badge URL
license / license_url	license name & link	Apache-2.0
languages / runtimes	headline tech stack	Python, TypeScript
python_version	minimum supported Python	3.11+
node_version	minimum supported Node	20+
security_contact	security disclosure email	security@…
year	copyright year	2025

Toggles

Feature flags (booleans) let you include/exclude sections:

readme:
  show_badges: true
  show_quickstart: true
  show_devnet: {{ include_devnet | default(true) }}
  show_tests: true
  show_ops: {{ include_ops | default(true) }}

When a toggle is false, its corresponding managed block is omitted.

Re-rendering safely
	•	One-way edits: put your custom content outside managed blocks.
	•	Idempotence: re-running the renderer won’t duplicate sections; it updates in place.
	•	Dry-run: use templates-engine with --dry-run to preview changes.

Example:

python -m templates.engine.cli render \
  --template {{ template_id | default('node') }} \
  --dest . \
  --vars path/to/vars.yaml \
  --mode merge \
  --dry-run


⸻

FAQ

Q: Can I rename section IDs?
A: Avoid it. The renderer tracks block identity by the SECTION_ID string.

Q: I need to change badges.
A: Override repo_url, ci_url, license_url in variables, or disable the BADGES block and add your custom badges outside.

Q: Pre-commit is too strict.
A: Tweak the repo-level .pre-commit-config.yaml. The shared one is a baseline; you can raise/lower checks per project.

Q: Do I need Docker?
A: Only for devnet/e2e. Pure unit tests and SDK work without it.

⸻

This README is part of the shared template content under templates/_common/. To improve the baseline for everyone, propose changes there rather than editing generated files.
