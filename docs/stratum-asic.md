# Stratum v1 (SHA-256 ASIC) mining

Animica ships an ASIC-compatible Stratum v1 endpoint alongside the native
HashShare pool. Use the `asic_sha256` profile to expose a classic
`mining.subscribe` / `mining.authorize` / `mining.notify` interface that works
with Antminer/Whatsminer style devices.

## Configuration

Environment variables (or CLI flags via `python -m animica.stratum_pool.cli`):

- `ANIMICA_POOL_PROFILE=asic_sha256` – enable the ASIC listener
- `ANIMICA_STRATUM_BIND=0.0.0.0:3333` – bind address
- `ANIMICA_RPC_URL` – Animica node RPC endpoint
- `ANIMICA_POOL_ADDRESS` – payout address
- `ANIMICA_STRATUM_EXTRANONCE2_SIZE` – extranonce2 size (default `4`)
- `ANIMICA_STRATUM_MIN_DIFFICULTY` – starting difficulty for new workers

Run the pool:

```bash
python -m animica.stratum_pool.cli --profile asic_sha256
```

Point ASICs at:

```
stratum+tcp://<server-ip>:3333
```

Worker/password are free-form today (the pool accepts all `mining.authorize`
requests).

## Debugging connections

Use the built-in debug client to observe the handshake and job stream without
physical hardware:

```bash
python -m mining.cli.stratum_debug --host 127.0.0.1 --port 3333 --worker test.worker
```

The tool prints subscribe/authorize responses and any `mining.notify` or
`mining.set_difficulty` messages delivered by the server.
