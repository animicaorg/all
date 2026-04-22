# AICF Provider Worker (Python Source)

Use this path when you want to customize the worker or integrate local instrumentation.

## Setup

```bash
tar -xzf aicf-provider-worker-0.2.0-python.tar.gz
cd aicf-provider-worker
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python worker.py init-config --config provider.config.json
```

Configure provider credentials and payout wallet in `provider.config.json`.

## Benchmark and Start

```bash
python worker.py benchmark --config provider.config.json
python worker.py start --config provider.config.json
```

## Health Check

```bash
python worker.py health --config provider.config.json
```
