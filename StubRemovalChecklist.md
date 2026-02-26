# Stub Removal Checklist

This document tracks stubs, placeholders, and incomplete flows identified during
the Phase 1 system-wide audit, along with their replacements.

## Summary

| # | Location | Stub Type | Status | Replacement |
|---|----------|-----------|--------|-------------|
| 1 | `ena_ml/dataset/sources/__init__.py` | Empty module – no providers registered | **Fixed** | Full provider registry with Wikipedia, arXiv, Gutenberg, VettedRepos |
| 2 | No `doctor` CLI subcommand | Missing feature – "Run System Doctor" button had no backend | **Fixed** | `animica_studio.doctor` module + `animica-studio doctor [--json]` |
| 3 | No setup scripts | Missing `ops/setup_studio.sh`, `ops/setup_studio_mac.sh`, `ops/setup_node_da.sh` | **Fixed** | Added all three scripts |
| 4 | `app.py` `main()` CLI dispatch | Only GUI path; `doctor` subcommand would start Qt | **Fixed** | Pre-Qt dispatch: `sys.argv[0] == "doctor"` exits before Qt init |

---

## Detailed Entries

### 1. `ena_ml/dataset/sources/__init__.py` — Empty provider registry

**Before:**
```python
# (empty file)
```

**Problem:** The dataset sources package existed but exported nothing. Any code
trying `from animica_studio.ena_ml.dataset.sources import PROVIDER_REGISTRY`
would receive an empty module, making source-driven dataset bootstrapping a no-op.

**Fix:** Populated with:
- `WikipediaAbstractsProvider` (CC BY-SA Wikipedia dumps)
- `ArxivApiProvider` (arXiv metadata/abstracts, open access)
- `GutenbergProvider` (Project Gutenberg public-domain texts)
- `VettedReposProvider` (curated open-licensed documentation)
- `OPTIONAL_SOURCE_METADATA` for opt-in sources (Wikisource, CC News) with notes
- `PROVIDER_REGISTRY` dict, `get_provider()`, `list_providers()` helpers

All providers are the existing, tested implementations in
`services/dataset_bootstrap_service.py`; the registry simply makes them
discoverable by name.

---

### 2. No `animica-studio doctor` CLI subcommand

**Before:** Running `animica-studio doctor` would attempt to start the Qt
application and fail with a display error on headless machines.

**Problem:** The "Run System Doctor" button in the UI and the
`animica-studio doctor --json` CLI called for in the spec did not exist.

**Fix:** Added `animica_studio/doctor.py` with:
- `DoctorReport` dataclass (sections: environment, node_rpc, da, studio, ena, pipeline)
- Probe functions for each section:
  - `_probe_environment()` – Python version, venv, torch, CUDA, disk/RAM/CPU, packages
  - `_probe_rpc(rpc_url)` – reachability, discover, server_version, required capabilities
  - `_probe_da(rpc_url)` – enabled/writable/allow_remote_put/ingest
  - `_probe_studio()` – config path, profiles
  - `_probe_ena()` – datasets, tokenizer, model store, inference
  - `_probe_pipeline()` – evaluates all section results → actionable blockers
- `run_doctor()` – orchestrates all probes, returns `DoctorReport`
- `print_report()` – human-readable or JSON output
- `doctor_main()` – argparse entry point for `animica-studio doctor`

Updated `app.py` to dispatch to `doctor_main` before Qt initialisation when
`sys.argv[1] == "doctor"`.

---

### 3. Missing setup scripts

**Before:** No one-click setup path existed for new users.

**Fix:** Added three scripts to `ops/`:

| Script | Purpose |
|--------|---------|
| `ops/setup_studio.sh` | Linux: create venv, install animica-studio, verify PyTorch, run doctor, launch |
| `ops/setup_studio_mac.sh` | macOS: same as above with Apple Silicon and Homebrew guidance |
| `ops/setup_node_da.sh` | Enable DA + ingestLocal on a local node; prints RPC URL for Studio |

All scripts are idempotent and safe to re-run.

---

### 4. `app.py main()` — GUI-only entry point

**Before:** `main()` immediately set up logging and Qt; no way to run headlessly.

**Fix:** Added a pre-Qt subcommand dispatch block at the top of `main()`:
```python
_argv = sys.argv[1:]
if _argv and _argv[0] == "doctor":
    from animica_studio.doctor import doctor_main
    sys.exit(doctor_main(_argv[1:]))
```
This allows `animica-studio doctor --json` to work on headless CI/CD systems.

---

## Intentionally Not Implemented / Feature-Flagged

The following items were identified during the audit but are explicitly
out-of-scope for this change, or require an active node to test:

| Item | Reason | Action |
|------|--------|--------|
| Wikisource provider implementation | Requires custom XML parsing pipeline; listed in `OPTIONAL_SOURCE_METADATA` | Opt-in via config; marked as `allow_auto_download=False` |
| Common Crawl (cc_news) provider | Requires compliance pipeline and licensing review | Opt-in via config; marked as `allow_auto_download=False` |
| `da.getIngestDir` / `da.ingestLocal` node-side RPC | Requires running node with updated firmware | Described in `setup_node_da.sh` and doctor report; graceful degradation implemented |
| Train-from-DA streaming mode | Requires `allow_remote_get=true` and dataset channel pointer | Toggle exposed in `EnaFullAutoPanel` advanced settings |

---

## Tests Added

| Test file | What it covers |
|-----------|---------------|
| `apps/animica_studio/tests/test_doctor.py` | `DoctorReport` structure, `_probe_environment`, `run_doctor` headless, JSON serialisation |
| `apps/animica_studio/tests/test_source_registry.py` | `PROVIDER_REGISTRY` completeness, `get_provider` happy + error paths, `list_providers` |

---

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| IDE assistant does not return canned placeholder replies | ✅ Already enforced by `EnaAgent` (live model or daemon) |
| Inference calls model and varies output by prompt | ✅ `EnaInferenceService` routes to daemon or PyTorch model |
| `available=True` computed from real checks | ✅ `_probe_pipeline()` evaluates torch, DA, disk, RPC reachability |
| `animica-studio doctor --json` works headlessly | ✅ Implemented in `doctor.py` |
| Setup scripts present | ✅ `ops/setup_studio.sh`, `ops/setup_studio_mac.sh`, `ops/setup_node_da.sh` |
| Source provider registry non-empty | ✅ 4 default providers registered |
