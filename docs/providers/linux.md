# AICF Provider Worker on Linux

## Requirements

- Ubuntu 22.04+ (or equivalent)
- Python 3.10+
- NVIDIA driver 535+ for GPU workloads

## Install

```bash
tar -xzf aicf-provider-worker-0.2.0-linux-x64.tar.gz
cd aicf-provider-worker
cp provider.config.example.json provider.config.json
chmod +x benchmark-worker.sh start-worker.sh
```

Set provider identity and payout wallet in `provider.config.json`.

## Benchmark and Start

```bash
./benchmark-worker.sh
./start-worker.sh
```

## Optional systemd service

```bash
sudo ./install-systemd.sh
```

Logs are written to `logs/provider-worker.log`.
