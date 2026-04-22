# AICF Provider Worker on Windows

## Requirements

- Windows 10 or 11 x64
- Python 3.10+ (for reference bundle)
- NVIDIA driver 535+ for GPU queues

## Install

```powershell
Expand-Archive .\aicf-provider-worker-0.2.0-windows-x64.zip -DestinationPath .\aicf-worker
cd .\aicf-worker
copy provider.config.example.json provider.config.json
```

Edit `provider.config.json` and set:

- `provider_id`
- `provider_token`
- `node_id`
- `payout_address`

## Benchmark and Start

```powershell
.\benchmark-worker.bat
.\start-worker.bat
```

## Logs

Worker logs are written to `logs/provider-worker.log`.
