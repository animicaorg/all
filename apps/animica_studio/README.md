# Animica Studio

Desktop application for the Animica blockchain — built with Python 3.11+ and PySide6.

## Requirements

- Python 3.11 or newer
- PySide6 (installed automatically below)

## Setup

```bash
# From the apps/animica_studio directory
python3 -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -e ".[dev]"
```

## Run

```bash
# From the repo root
python -m animica_studio

# Or from apps/animica_studio/ directly
cd apps/animica_studio
python -m animica_studio
```

## Project layout

```
animica_studio/
├── __init__.py         # package version
├── __main__.py         # entry point (python -m animica_studio)
├── app.py              # QApplication bootstrap, global exception handler
├── ui/
│   ├── main_window.py  # MainWindow (sidebar + header + stacked pages)
│   └── pages/          # Dashboard, Wallet, Node, Console, Settings
├── services/
│   └── workers.py      # QThread worker skeleton
├── models/             # typed data-models (dataclasses)
├── storage/
│   └── config.py       # JSON config read/write with OS app-data dir
└── util/
    ├── paths.py        # per-OS app-data dir helpers
    └── logging.py      # rotating file + console logging setup
```

## Configuration

A JSON config file is created automatically on first run:

| OS      | Location |
|---------|----------|
| Linux   | `~/.local/share/animica-studio/config.json` |
| macOS   | `~/Library/Application Support/Animica Studio/config.json` |
| Windows | `%APPDATA%\Animica Studio\config.json` |

## Logs

Log files (rotating, max 5 × 2 MB) are stored in the same app-data directory under `logs/`.

## Dev extras

```bash
# Lint
ruff check animica_studio

# Type-check
mypy animica_studio

# Tests
pytest
```

## Features

Animica Studio provides a complete desktop interface to all Animica CLI operations:

| Page | Description |
|------|-------------|
| **Dashboard** | Node health, chain info, quick actions |
| **Wallet** | Multi-account balances, send transactions, history, explorer links |
| **Node** | Start/stop/restart local node, status, log tail |
| **Mining** | Mine blocks (CPU), automine toggle, live mining log stream |
| **AICF** | Status, miner credits, claim, jobs list/submit/watch |
| **DA** | Blob put/get/proof with chunked upload and namespace support |
| **Quantum** | Quantum job status, credits, submit, and stream watch |
| **Console** | Raw CLI runner with presets, history, and streaming output |
| **IDE** | Monaco editor with run-script placeholder |
| **ENA** | ENA chat/agent, local daemon controls, profile-scoped endpoints, training push wizard |
| **Settings** | Profiles, RPC config, timeouts |

### Bug fixes included

- **AICF 405 Method Not Allowed**: All AICF/DA/Quantum services normalise the RPC
  URL to ensure it ends with `/rpc` (fixes bare-URL 405 errors).
- **Wallet `[object Object]`**: All errors are formatted through `format_rpc_error`
  and `format_exception` before display; never raw dict/object dumps.
- **BigInt serialization**: `RpcClient` now uses `safe_json_dumps` (with custom
  `int` encoder) instead of `json.dumps` for RPC request bodies — handles
  arbitrarily large Python integers safely.
- **Balance cache**: `WalletService.clear_balance_cache()` is called on profile
  switch to prevent stale balances from a previous profile appearing.

The **Console** page provides a full-featured CLI runner with:

- **Presets panel** — grouped one-click buttons for common `animica` commands
  (Node, Chain/RPC, Wallet, AICF). Presets are persisted in `config.json` under
  `console_presets`.
- **Command input** — raw command entry with Up/Down history navigation. Type
  sub-commands without the `animica` prefix (it is prepended automatically).
- **Streaming output** — real-time stdout/stderr display with filter, copy,
  save, and stop controls.
- **Node control panel** — Start / Stop / Restart / Refresh buttons with
  auto-refresh every 15 s.

Command history is persisted in `config.json` under `console_history`.

## IDE Page

The **IDE** page embeds a Monaco editor (when `PySide6-WebEngine` is installed)
or falls back to a plain `QPlainTextEdit`.

### Setup Monaco assets

```bash
python scripts/setup_monaco.py          # downloads Monaco 0.46.0
python scripts/setup_monaco.py --version 0.47.0 --force
```

Assets are unpacked to `animica_studio/ui/web/monaco/vs/`.

### Features

- Project tree with context-menu create / rename / delete
- Tabbed editing with dirty-state indicators
- Ctrl+S save (both Monaco and fallback)
- **Run Script** — syntax-check via `python -m py_compile` (placeholder;
  swap in the Animica VM runner in `services/deterministic_runner.py`)
- Workspace root persisted in `config.json` under `ide_workspace_root`


### Token Templates

Studio IDE now includes **File → New → Token…** (and a toolbar **New Token…** button)
for scaffolding deterministic Python-VM token contracts.

Included templates:
- Animica NFT
- Animica FT
- Animica MultiToken
- Membership Pass (soulbound toggle)
- Factory/Registry stub

Generated output includes `contract.py`, `manifest.json`, and `README.md`, written
into a folder inside your active workspace. Existing files are not overwritten
unless explicitly confirmed.

To add more templates, create a new folder under
`animica_studio/templates/tokens/<template_id>/` with `*.tmpl` files and register
the template metadata/params in
`animica_studio/services/token_template_service.py`.

### Install PySide6-WebEngine

```bash
pip install PySide6-WebEngine
```

Without it the IDE falls back to a plain text editor.

## Packaging

Build standalone executables with [PyInstaller](https://pyinstaller.org):

```bash
# Linux
bash scripts/package_linux.sh

# macOS
bash scripts/package_macos.sh

# Windows (PowerShell)
pwsh -File scripts/package_windows.ps1
```

Artifacts appear in `dist/AnimicaStudio/`. Include Monaco assets before
packaging by running `scripts/setup_monaco.py` first.


## Manual Verification Checklist

After starting the app (`python -m animica_studio`):

1. **Profile setup**: Open Setup Wizard, configure RPC URL (e.g. `http://127.0.0.1:8545/rpc`), verify green health dot appears.
2. **Node page**: Click Start → check status shows "running=True" in log, click Stop.
3. **Mining page**: Set count=1, click Mine Blocks → live output streams; automine checkbox → Apply.
4. **AICF page**: Status tab → Refresh Status (expect JSON or clear error, not `[object Object]`); Credits tab → enter address → Fetch.
5. **DA page**: Put Blob → type text → Upload → verify commitment returned; Get Blob → paste commitment → Download → see text.
6. **Quantum page**: Status tab → Refresh; Jobs → List; Submit with `{"circuit":"test"}`.
7. **Wallet page**: Add account, see balance (or "Unavailable" with clear error); Send with large value (verify no BigInt error).
8. **Profile switch**: Switch profile in header → wallet balances clear and re-fetch (no stale cross-profile values).
9. **Console page**: Run `node status` → streaming output appears; history with Up arrow.
10. **Settings page**: Update profile, save.


## ENA integration (local / remote / network)

Studio now supports three ENA modes under the **ENA** page:

1. **Local daemon (CPU)** — click **Start ENA (CPU)** to launch the bundled local server.
2. **Remote HTTP/WS** — configure endpoint + auth token in ENA settings fields.
3. **Network RPC** — uses node JSON-RPC feature detection (`rpc.discover`) for `ena.*` methods.

### Running local ENA daemon manually

```bash
python -m animica_studio.services.ena_daemon_server --host 127.0.0.1 --port 8765
```

### Push training bundle to chain

From ENA page:

1. Select training files.
2. Click **Push to Chain**.
3. Studio validates file types, computes sha3-256 per file + bundle merkle root,
   creates deterministic `bundle.tar` manifest package, uploads to DA (or fallback),
   then submits transaction reference via RPC.
4. Resume state is persisted in app data under `training_push/state.json`.

### Troubleshooting

- If ping fails repeatedly, ENA client opens a short circuit-breaker cooldown.
- If DA methods are unavailable, upload falls back to `local://export-only` URI.
- All ENA/network errors are JSON-stringified to avoid `[object Object]` messages.

## ENA ML local pipeline

A new local PyTorch pipeline is available in `animica_studio/ena_ml` for dataset bootstrap, Transformer training, and inference.

```bash
cd apps/animica_studio
pytest tests/test_ena_ml_pipeline.py
```

Key modules:
- `ena_ml/dataset/build.py` + `manifest.py` (shard + provenance manifest)
- `ena_ml/model/transformer.py` (decoder-only LM)
- `ena_ml/train/trainer.py` (exact-step trainer with JSONL metrics and checkpoints)
- `ena_ml/infer/generate.py` + `chat.py` (prompt assembly and generation)

For DA node-side ingest workflows, use `services/da_ingest.py`; it resolves node ingest paths and avoids writing directly to `/data` on host environments.
