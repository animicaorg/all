# Templates Engine

A small, deterministic scaffolding layer used to **list**, **validate**, and **render** project templates.  
It powers cookie-cutter–style starter projects and boilerplate inside this repo (contracts, services, SDK demos, etc.).

**Goals**

- Zero-surprise rendering: inputs → outputs are reproducible given the same variables and version.
- Strict validation before writing anything to disk.
- Minimal runtime deps (ships with a safe fallback renderer; richer renderers are optional).
- Works in CI (non-interactive) and locally.

---

## Contents

- [Concepts](#concepts)
- [Directory Layout](#directory-layout)
- [Manifest Schema](#manifest-schema)
- [Variables](#variables)
- [Validation](#validation)
- [Rendering](#rendering)
  - [Fallback Renderer](#fallback-renderer)
  - [Optional Advanced Renderer](#optional-advanced-renderer)
  - [Hooks](#hooks)
  - [Excluding Paths](#excluding-paths)
- [CLI](#cli)
  - [Examples](#examples)
  - [Exit Codes](#exit-codes)
- [Programmatic API](#programmatic-api)
- [Examples: Minimal Template](#examples-minimal-template)
- [Testing & CI](#testing--ci)
- [Troubleshooting](#troubleshooting)
- [Versioning & Compatibility](#versioning--compatibility)

---

## Concepts

- **Template** — a directory containing a `manifest.json` and arbitrary files/folders to be rendered.
- **Templates root** — the repo’s `templates/` directory. The engine auto-discovers templates under it.
- **Variables** — key/value pairs used during rendering (e.g., `NAME`, `ORG`, `PORT`).
- **Validation** — checks the template structure and that required variables are provided and of the right type.
- **Rendering** — writes a materialized copy of the template to an output directory, optionally substituting variables.

The engine is opinionated about validation, but flexible about rendering. If an advanced renderer is present (`templates/engine/render.py`) it will be used; otherwise a deterministic **fallback renderer** kicks in.

---

## Directory Layout

templates/
index.json                  # catalog (optional)
schemas/
template.schema.json      # schema for manifest.json
variables.schema.json     # reusable pieces for variable types
engine/
README.md
cli.py
validate.py
render.py                 # optional richer renderer (Jinja2/filters/etc.)
variables.py              # helpers (env/merging/normalization)
/
/
manifest.json
_hooks.py               # optional (pre/post render hooks)
…                     # files/folders to render

**Notes**

- Anything under `templates/**/manifest.json` is considered a template directory (except `templates/schemas/`).
- Files named `_hooks.py` are optional and loaded only if present (see [Hooks](#hooks)).

---

## Manifest Schema

Each template ships a `manifest.json` describing metadata, variables, and default values. The shape is validated against `templates/schemas/template.schema.json`.

**Example (abridged):**
```json
{
  "name": "ai_agent",
  "version": "1.1.0",
  "description": "Example AI agent scaffold",
  "variables": {
    "NAME":    { "type": "string", "description": "Project name", "required": true },
    "ORG":     { "type": "string", "default": "acme" },
    "PORT":    { "type": "integer", "default": 8080, "min": 1, "max": 65535 },
    "LICENSE": { "type": "enum", "values": ["MIT", "Apache-2.0"], "default": "MIT" }
  },
  "exclude": [
    "**/__pycache__/**",
    "**/.DS_Store"
  ]
}

Supported variable types (enforced by the validator):
	•	string (optional pattern)
	•	integer (optional min, max)
	•	boolean
	•	enum (with values: [...])

⸻

Variables

Variables may be provided via:
	1.	JSON file: --vars vars.json
	2.	CLI key/values: repeatable --var KEY=VALUE
	3.	Environment with prefix: --env-prefix TPL_ → TPL_NAME=demo becomes NAME=demo

Precedence when merging: file ⟶ env ⟶ –var (later wins).

The validator normalizes types (e.g., "8080" → 8080 for an integer variable) and applies defaults.

⸻

Validation

Validation ensures:
	•	Template directory exists and contains manifest.json
	•	manifest.json conforms to schemas/template.schema.json
	•	Required variables are present after merging sources
	•	Values match declared types/constraints
	•	Reports warnings (e.g., unknown variables) and errors (missing/invalid)

Output

A ValidationReport is printed to stderr with errors and warnings. In CLI you can choose to treat warnings as errors via --strict.

⸻

Rendering

Fallback Renderer

If templates/engine/render.py is not present, the engine falls back to a simple, deterministic renderer:
	•	Copies all files and folders from the template directory to the target output directory.
	•	Skips manifest.json and _hooks.py.
	•	Performs path substitution: each path component is processed for {{VAR}} tokens, e.g.
src/{{NAME}}/README.md → src/myproj/README.md.
	•	For text files (UTF-8), performs {{VAR}} token substitution in file contents.
	•	For binary files, copies as-is.
	•	Honors exclude globs from the manifest and additional --exclude patterns.
	•	Refuses to overwrite existing files unless --force is set.
	•	Supports --dry-run to print a plan instead of writing.

Token format: {{ VAR_NAME }} — whitespace around the name is allowed.
Missing variables are left unchanged (literal {{VAR}}) to avoid silent mis-renders.

Optional Advanced Renderer

If render.py exists, the CLI tries (in order):
	1.	plan_template_dir() + apply_plan()
	2.	plan() + apply_plan()
	3.	render() / render_template_dir() / render_dir()

This allows richer features (templating engines, filters, binary patching) without breaking the CLI surface.

Hooks

Templates can provide _hooks.py with optional functions:

# _hooks.py
def pre_render(context):  # context: dict (template_dir, out_dir, vars, manifest)
    pass

def post_render(context, results):  # results: plan or list of written paths
    pass

Hooks are only used by advanced renderers that opt-in to call them. The fallback renderer does not execute hooks.

Excluding Paths
	•	manifest.json can include an exclude array of glob patterns relative to the template root.
	•	The CLI also supports --exclude (repeatable) to add globs at run time.

Examples: "**/__pycache__/**", "**/*.tmp", "docs/draft/**".

⸻

CLI

The CLI lives in templates/engine/cli.py and can be invoked as a module:

python -m templates.engine.cli --help

Commands
	•	list — enumerate templates discovered under templates/.
	•	validate — check a template and (optional) variables.
	•	render — render a template into an output directory.

Examples

List available templates:

python -m templates.engine.cli list

Validate with variables from a file and env:

export TPL_NAME="demo"
python -m templates.engine.cli validate \
  --template templates/examples/ai_agent \
  --vars ./vars.json \
  --env-prefix TPL_ \
  --print

Render with explicit overrides and dry-run:

python -m templates.engine.cli render \
  -t templates/templates/ai_agent \
  -o ./scaffold \
  --var NAME=demo --var LICENSE=Apache-2.0 \
  --dry-run

Force overwrite and exclude some files:

python -m templates.engine.cli render \
  -t templates/templates/ai_agent \
  -o ./scaffold \
  --var NAME=demo \
  --exclude "**/node_modules/**" \
  --exclude ".env" \
  --force

Exit Codes
	•	0 success
	•	1 validation error, render error, or interruption

⸻

Programmatic API

You can use the engine from Python code:

from pathlib import Path
from templates.engine.validate import validate_template
from templates.engine import render as renderer  # optional
from templates.engine.cli import _resolve_template_dir  # helper

root = Path("templates")
tpl = _resolve_template_dir("templates/ai_agent", root)

# Validate
normalized_vars, report = validate_template(tpl, user_vars={"NAME": "demo"})
if not report.ok:
    raise RuntimeError(report)

# Render (advanced renderer)
plan = renderer.plan_template_dir(template_dir=tpl, out_dir=Path("./out"), variables=normalized_vars)
renderer.apply_plan(plan, overwrite=False)

If render.py is not available, import the fallback from the CLI module:

from templates.engine.cli import _fallback_build_plan, _fallback_apply_plan
plan = _fallback_build_plan(tpl, Path("./out"), {"NAME": "demo"})
_fallback_apply_plan(plan, overwrite=False)


⸻

Examples: Minimal Template

templates/
  templates/
    hello/
      manifest.json
      README_{{NAME}}.md
      src/{{NAME}}/main.py

manifest.json:

{
  "name": "hello",
  "version": "0.1.0",
  "variables": {
    "NAME": { "type": "string", "required": true }
  }
}

Render:

python -m templates.engine.cli render -t templates/templates/hello -o ./out --var NAME=world

Results:

out/
  README_world.md
  src/world/main.py


⸻

Testing & CI
	•	Unit tests should exercise validation (required vars, type coercion).
	•	Rendering in CI should use --dry-run for plan diffs where possible.
	•	For golden outputs, render into a temp dir and compare file lists + checksums (avoid timestamp diffs).

⸻

Troubleshooting
	•	“Unknown variable” warnings — you passed a key not listed in manifest.json. Either add it to the manifest or remove it.
	•	File exists — rerun with --force to allow overwrites, or render into an empty directory.
	•	Binary file mangled — the fallback renderer only substitutes text files (UTF-8). If a file is incorrectly detected as text, ensure it’s truly UTF-8 or switch to an advanced renderer that marks MIME types explicitly.
	•	--var KEY=VALUE with = inside — quote your value: --var NOTE="a=b=c".

⸻

Versioning & Compatibility
	•	Manifests are validated against schemas/template.schema.json. Schema changes follow semver; minor additions should be backward compatible.
	•	The fallback renderer is intentionally simple to keep stability and determinism. Use render.py for advanced behaviors (templating engines, filters, code-gen).
	•	Hooks are an opt-in feature for advanced renderers; do not rely on them being executed if the fallback is in use.

⸻

Happy scaffolding!

