# Animica GUI Miner

Production-quality Qt (PySide6) desktop GUI miner for Animica blockchain with extreme configurability, robust device detection, and high UX quality.

## Features

- **First-Run Wizard**: Guided setup for network, RPC, wallet, and device selection
- **Dashboard**: Real-time mining status, hashrate, shares, and blocks
- **Device Management**: CPU/GPU/ASIC configuration with auto-detection
- **Pool Support**: Solo mining (default) with Stratum pool configuration stub
- **Configuration**: JSON editor with schema validation and profiles
- **Logs**: Real-time log stream with filtering, search, and export
- **Stats/Graphs**: Hashrate and shares visualization with matplotlib
- **Dark Theme**: Professional dark theme by default
- **System Tray**: Minimize to tray with notifications
- **Auto-start**: Optional auto-start mining on launch
- **Diagnostics**: Copy diagnostics button for troubleshooting

## Installation

### Prerequisites

- Python 3.10 or higher
- PySide6 (Qt for Python)
- Pydantic for configuration
- matplotlib for graphs (optional)
- pyopencl for GPU support (optional)

### Install from Source

```bash
cd apps/miner-gui
pip install -e .
```

### Install with GPU Support

```bash
pip install -e ".[gpu]"
```

### Development Installation

```bash
pip install -e ".[dev]"
```

## Usage

### Command Line

```bash
# Primary command
animica gui miner

# Or use the alias
animica-miner-gui
```

### Development Mode

```bash
cd apps/miner-gui
./scripts/run_dev.sh
```

## Configuration

Configuration is stored in `~/.animica/gui-miner/config.json` with secure permissions (0600).

### Configuration Structure

- **network**: Network type, RPC URL, chain ID
- **miner**: Mining mode, payout address, auto-start
- **cpu**: CPU threads, affinity, hugepages, priority
- **gpus**: List of GPU devices with intensity and worksize
- **asic**: ASIC worker configuration (stub)
- **pool**: Stratum pool configuration
- **ui**: Theme, system tray, notifications
- **safe_mode**: Resource-constrained operation

### Payout Address

The miner requires a valid payout address for mining rewards. You can:

1. Enter manually in the wizard
2. Import from `~/.animica/wallets.json` (reads only public info)
3. Edit in Configuration tab

Format: `anim1...` (42+ characters, bech32-like)

## Device Detection

The miner automatically detects available mining hardware:

### CPU
- Always detected
- Auto-recommends thread count (leaves cores for system)
- Detects container CPU limits
- Hugepages support detection (Linux)

### GPU (OpenCL)
- Requires `pyopencl` package
- Detects all OpenCL-capable GPUs
- Shows compute units, memory, driver version
- Recommends suitable devices (>2GB memory, >4 CUs)

### ASIC (Stub)
- Placeholder for external hash workers
- Environment templates in root: `hash_worker.asic.env.example`

## Troubleshooting

### RPC Connection Issues

1. Verify RPC URL in Configuration tab
2. Check node is running: `animica node status`
3. Test connection in Dashboard wizard
4. Check firewall rules

### GPU Not Detected

1. Install pyopencl: `pip install pyopencl`
2. Verify GPU drivers are installed
3. Test OpenCL: `python3 -c "import pyopencl as cl; print(cl.get_platforms())"`
4. Check vendor-specific drivers (NVIDIA CUDA, AMD ROCm)

### Permission Issues

1. Config directory: `~/.animica/gui-miner/` should be writable
2. Config file: Automatically secured with 0600 permissions
3. Wallet import: Only reads public info from `~/.animica/wallets.json`

### Performance Issues

1. Enable Safe Mode in Configuration tab
2. Reduce CPU threads
3. Lower GPU intensity
4. Check for container CPU limits (warnings in device detection)
5. Ensure sufficient RAM (4GB+ recommended)

### Logs Not Showing

1. Check log level in Configuration tab (default: INFO)
2. Increase to DEBUG for more detail
3. Use Export button to save logs for analysis

## Architecture

### Backend (`backend/`)

- **config.py**: Pydantic models and JSON schema for all settings
- **device_detection.py**: Auto-detection for CPU/GPU with recommendations
- **miner_runner.py**: Process/thread management and event streaming
- **rpc_client.py**: Simple RPC client for chain queries

### UI (`ui/`)

- **main_window.py**: Main window with tabs and system tray
- **wizard.py**: First-run setup wizard
- **tabs/**: Individual tab implementations
  - `dashboard.py`: Status and controls
  - `devices.py`: Device configuration
  - `pools.py`: Pool/mode configuration
  - `configuration.py`: JSON editor
  - `logs.py`: Log viewer
  - `stats.py`: Graphs and statistics

### Event Bus

The miner runner emits structured events:
- `STATUS_CHANGE`: Mining status updates
- `HASHRATE_UPDATE`: Periodic hashrate reports
- `SHARE_FOUND`: Share submissions
- `BLOCK_FOUND`: Block discoveries
- `TEMPLATE_UPDATE`: New block templates
- `ERROR`: Error conditions
- `LOG`: Log messages

## Testing

### Run Tests

```bash
cd apps/miner-gui
pytest
```

### Headless CI Test

The tests are designed to run in headless environments:

```bash
pytest --import-mode=importlib
```

### Test Coverage

- Configuration schema roundtrip
- Device detection (mocked)
- Miner runner lifecycle
- Event emission and callbacks

## Security

- **No secrets logged**: Payout address validated, but private keys never used
- **Config security**: Files stored with 0600 permissions
- **Wallet import**: Reads only public info (address, label)
- **Safe defaults**: Conservative resource usage by default
- **Defensive errors**: Graceful degradation on missing dependencies

## Development

### Code Structure

```
apps/miner-gui/
├── animica_miner_gui/
│   ├── backend/          # Business logic
│   ├── ui/               # Qt UI components
│   │   └── tabs/         # Tab widgets
│   ├── resources/        # Icons, themes
│   ├── tests/            # Unit tests
│   └── main.py           # Entry point
├── scripts/
│   └── run_dev.sh        # Development runner
├── pyproject.toml        # Package configuration
└── README.md             # This file
```

### Adding Features

1. Backend changes: Update `backend/` modules
2. UI changes: Update `ui/` modules
3. Config changes: Update `backend/config.py` and schema
4. Tests: Add to `tests/` with pytest
5. Documentation: Update this README

### Contributing

See main repository CONTRIBUTING.md for guidelines.

## License

See LICENSE.txt in the repository root.

## Support

- GitHub Issues: https://github.com/animicaorg/all/issues
- Documentation: https://docs.animica.org
- Community: https://discord.gg/animica
