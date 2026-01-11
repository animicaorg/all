# Animica Qt Wallet (Python)

Minimal PySide6 + qasync scaffold for the Animica wallet.

## Features

- **Account Management**: Create and import accounts with PQ-secure Dilithium3 signatures
- **Send Transactions**: Build, sign, and send ANM token transfers with advanced fee controls
- **Receive Tokens**: View addresses and QR codes for receiving payments
- **Node Integration**: Embedded node management with automatic sync
- **Chain Overview**: View balance, chain status, and peer connections

## Requirements

- Python 3.11+

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,qr]"  # Include qr for QR code support
./run.sh
```

## Optional Features

### QR Code Generation

To enable QR code generation in the Receive tab:

```bash
pip install -e ".[qr]"
```

Or install manually:

```bash
pip install qrcode[pil]
```

## Lint / format / test

```bash
ruff check .
black --check .
pytest
python test_send_receive.py  # Validate send/receive implementation
```

## Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,qr]"
.\run.ps1
```
