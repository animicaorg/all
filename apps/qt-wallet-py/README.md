# Animica Qt Wallet (Python)

Minimal PySide6 + qasync scaffold for the Animica wallet.

## Requirements

- Python 3.11+

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
./run.sh
```

## Lint / format / test

```bash
ruff check .
black --check .
pytest
```

## Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
.\run.ps1
```
