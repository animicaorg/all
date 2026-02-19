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
