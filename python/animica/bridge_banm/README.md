# BANM Bridge Backend

Custodial two-way bridge service for Animica `<->` EVM (BNB Chain first).

## Run (local)

```bash
export PYTHONPATH=/root/animica/python
cp python/animica/bridge_banm/.env.example .env.banm
set -a; source .env.banm; set +a

python -m animica.bridge_banm.migrate upgrade head
python -m animica.bridge_banm.scripts.seed_admin --username admin --password changeme --role admin
python -m animica.bridge_banm
```

## Test

```bash
pytest -q python/animica/bridge_banm/tests
```

