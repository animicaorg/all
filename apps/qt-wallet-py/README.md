# Animica Qt Wallet (Python)

Minimal PySide6 + qasync scaffold for the Animica wallet.

## Features

- **Account Management**: Create and import accounts with PQ-secure Dilithium3 signatures
- **Send Transactions**: Build, sign, and send ANM token transfers with advanced fee controls
- **Receive Tokens**: View addresses and QR codes for receiving payments
- **Node Integration**: Embedded node management with automatic sync
- **Chain Overview**: View balance, chain status, and peer connections
- **External RPC Interface**: Secure API for dApps and tools to request wallet actions
  - User approval required for all signing/sending operations
  - Localhost-only with token authentication
  - Rate limiting and app allowlist support
  - See [External RPC API docs](docs/EXTERNAL_RPC_API.md) for details

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

## External RPC Interface

The wallet provides a secure external RPC interface for dApps and tools. See [External RPC API docs](docs/EXTERNAL_RPC_API.md) for full documentation.

### Quick Example

```bash
# Ensure wallet is running
./run.sh

# In another terminal, run the example client
python example_external_rpc.py
```

The example demonstrates:
1. Getting chain ID (no approval required)
2. Requesting wallet accounts (requires approval)
3. Sending a transaction (requires approval)

All signing/sending operations will trigger an approval dialog in the wallet UI.

## Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,qr]"
.\run.ps1
```
