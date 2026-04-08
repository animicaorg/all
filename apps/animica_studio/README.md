# Animica Studio

Desktop application for the Animica blockchain — built with Python 3.11+ and PySide6.

Studio is now organized around a beginner-first desktop flow:

- `Home`: overall status, balance summary, sync summary, warnings, quick actions
- `Wallet`: create/import/select wallet, receive, send, history, contacts
- `Node`: start/stop/reset, sync progress, peers, diagnostics, logs
- `Mining`, `ENA`, `AICF`, `DA`: optional advanced workflows once basics are working
- `Settings`, `Logs`: configuration, diagnostics, and support bundle export

For a user-focused walkthrough, see [STUDIO_USER_GUIDE.md](./STUDIO_USER_GUIDE.md).

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

## First Run

On first launch, Studio opens a guided setup wizard:

1. Choose the network.
2. Create, import, or reuse a wallet.
3. Configure a managed local node or connect to an external RPC.
4. Verify wallet + RPC reachability + sync status.
5. Land on `Home`.

The wizard can be rerun later from `Settings -> Developer -> Rerun Onboarding`.

## Basic User Flow

If you are new to Animica, use Studio in this order:

1. Open `Home` and check the warning cards.
2. Open `Wallet` and confirm your selected wallet and receive address.
3. Open `Node` and wait for RPC reachability and sync progress to look healthy.
4. Return to `Home` and confirm balance, peers, and recent activity.
5. Use `Wallet -> Send` only after the node or RPC is reachable.

## Key Screens

### Home

- Shows wallet summary, balance summary, node/sync status, mining/ENA/DA/AICF status, recent activity, and quick actions.
- Use it as the default “what should I do next?” screen.

### Wallet

- Create or import wallets without leaving the main page.
- Send validates recipient address and amount before submitting.
- Receive shows the selected address clearly with copy helpers.
- History shows pending vs refreshed transaction state.

### Node

- Shows whether the process is running, whether RPC is reachable, sync progress, peer count, chain height, and recent log lines.
- Includes `Start Node`, `Stop Node`, `Restart Node`, `Force Sync`, `Bootstrap Peers`, `Discover Snapshot`, and `Open Logs`.

### Settings and Logs

- `Settings` is grouped into `Basic`, `Advanced`, and `Developer`.
- `Logs` shows live issues, recent log lines, build/runtime metadata, and lets you export a diagnostics bundle.

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

Animica Studio still exposes the full toolset, but the core product path is now centered on:

| Section | Description |
|------|-------------|
| **Home** | Status overview, warnings, recent activity, quick actions |
| **Wallet** | Create/import/select wallet, balances, send/receive, history, contacts |
| **Node** | Managed node controls, sync health, diagnostics, logs |
| **Mining** | Mining controls and payout defaults |
| **ENA** | Consolidated ENA hub for contribution, checkpoints, training, publish, and inference |
| **AICF** | Credits, claims, jobs, and miner-linked workflows |
| **DA** | Storage/contribution tooling and status |
| **Settings** | Beginner/basic settings, advanced RPC/node/ENA/DA settings, developer diagnostics |
| **Logs** | Structured issues, live logs, diagnostics export |

Advanced tools remain available under `Tools`:

- `Console`
- `IDE`
- `Quantum`

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

Install the packaging dependency set first:

```bash
.venv/bin/pip install -e 'apps/animica_studio[dev,package]'
```

Build platform-specific release artifacts:

```bash
# Linux
bash scripts/package_linux.sh

# macOS
bash scripts/package_macos.sh

# Windows (PowerShell)
pwsh -File scripts/package_windows.ps1
```

Artifacts appear as:

- Linux: `dist/animica-studio_<version>_<arch>.deb`
- macOS: `dist/AnimicaStudio.app`
- Windows: `dist/AnimicaStudio.exe`

Use `--skip-build` to re-wrap an existing PyInstaller output without rebuilding it first.

The packaged build now explicitly bundles:

- `animica_studio/ui/web`
- `animica_studio/ui/resources`
- `animica_studio/templates`
- `animica_studio/resources/templates`

If you use the Monaco-based IDE view, include Monaco assets before packaging by
running `scripts/setup_monaco.py` first.

## Troubleshooting

- If `Home` or `Node` shows RPC offline, open `Node` first and use `Refresh Status` or `Start Node`.
- If sync is stalled with zero peers, use `Bootstrap Peers` on the `Node` page.
- If a wallet balance is unavailable, open `Wallet` and confirm the current RPC/explorer settings in `Settings`.
- If ENA remote features are unavailable, open `Settings -> Advanced -> ENA` and fill in the provider endpoint/model.
- If you need support data, open `Logs` and use `Copy Bundle` or `Export Bundle`.


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
