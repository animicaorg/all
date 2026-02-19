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

## Console Page

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
