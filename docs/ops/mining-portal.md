# Animica Mining Portal

This guide covers the production mining webpage, live Stratum endpoint
resolution, starter bundle generation, and reverse-proxy deployment.

## What ships

- `website/src/pages/mine.astro`
  The public mining page with live status, download cards, generated commands,
  copy buttons, and troubleshooting.
- `python/animica/stratum_pool/api.py`
  Adds `/api/mining/config`, `/api/mining/status`, `/api/mining/downloads`,
  `/api/mining/downloads/{platform}`, and `/api/mining/generate`.
- `python/animica/stratum_pool/portal.py`
  Canonical public mining resolver and command/config generation helpers.
- `python/animica/stratum_pool/package_builder.py`
  Builds per-platform starter bundles.
- `python/animica/stratum_pool/reference_cpu_miner.py`
  Standalone, dependency-free CPU Stratum miner bundled into the downloads.
- `scripts/build_miner_packages.sh`
  Operator helper that builds the bundles outside the API path.

## Public host detection

The mining API resolves the public Stratum endpoint in this order:

1. `ANIMICA_PUBLIC_STRATUM_URL`
2. `ANIMICA_PUBLIC_STRATUM_HOST` and `ANIMICA_PUBLIC_STRATUM_PORT`
3. `ANIMICA_PUBLIC_DOMAIN`
4. The current request host (`Host` / `X-Forwarded-Host`)
5. The Stratum bind host from `ANIMICA_STRATUM_BIND`
6. The RPC host fallback

TLS hints:

- `ANIMICA_PUBLIC_STRATUM_TLS_ENABLED=true`
- `ANIMICA_PUBLIC_STRATUM_SCHEME=stratum+tls`

Pool metadata:

- `ANIMICA_POOL_ENABLED=true|false`
- `ANIMICA_POOL_FEE_PERCENT=1.25`
- `ANIMICA_POOL_PAYOUT_MINIMUM=10 ANM`

## Required services

Run the Stratum pool and API first:

```bash
export ANIMICA_STRATUM_BIND=0.0.0.0:3333
export ANIMICA_POOL_API_BIND=0.0.0.0:8550
animica stratum up --daemon
```

The mining webpage expects `/api/mining/*` to resolve to that API.

## Build miner packages manually

The API builds bundles lazily on first request. To build them in advance:

```bash
./scripts/build_miner_packages.sh
```

Optional overrides:

```bash
./scripts/build_miner_packages.sh \
  --host pool.animica.example \
  --port 4444 \
  --address anim1... \
  --worker rig-01 \
  --threads 6
```

Artifacts are written to:

- `ANIMICA_MINING_DOWNLOAD_DIR`
- default: `artifacts/miners/`

## Website wiring

The website uses same-origin `/api/mining/*` by default. For split deployments:

- `website/.env`
- `ANIMICA_MINING_API_BASE_URL=https://pool.animica.example`

That keeps the page static while the API remains live and deployment-specific.

## Local verification

1. Start the pool API.
2. Run the website dev server:

```bash
cd website
pnpm dev
```

3. Open `http://127.0.0.1:4321/mine`.
4. Verify:
   - the host and port match the pool environment
   - per-platform download links return archives
   - generated commands include the detected host and entered payout address

## Reverse proxy example (nginx)

Serve the website and mining API from the same public domain:

```nginx
server {
    listen 443 ssl http2;
    server_name animica.example;

    location / {
        proxy_pass http://127.0.0.1:4321;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }

    location /api/mining/ {
        proxy_pass http://127.0.0.1:8550/api/mining/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }
}
```

If Stratum itself is exposed through a different domain or port, set:

```bash
export ANIMICA_PUBLIC_STRATUM_HOST=pool.animica.example
export ANIMICA_PUBLIC_STRATUM_PORT=3333
```

## Updating bundle versions

Set:

```bash
export ANIMICA_MINER_BUNDLE_VERSION=0.1.1
```

The package builder uses that version string in archive names and manifests.

## Tests

Run the portal-specific tests:

```bash
pytest -q python/animica/stratum_pool/tests/test_config.py \
  python/animica/stratum_pool/tests/test_portal.py
```
