# @animica/pool-web

Dedicated mining-pool site for **pool.animica.org**. Separate from the main
marketing site at animica.org so the pool surface can iterate on its own
deploy cadence.

## Pages

| Path | What it does |
|---|---|
| `/`          | Hero + 3-step connect flow + hardware-aware tier sell |
| `/aicf`      | AICF compute explainer (register → serve → settle) |
| `/tiers`     | Model catalog with tier eligibility |
| `/stats`     | Live pool stats (PoW + AICF) — reads from `/v1/pool/stats` and `/v1/stats/summary` |
| `/downloads` | Standalone CPU miner builds + `python3 -m pip install --upgrade animica` callout |

## Local dev

```bash
cd pool-web
pnpm install
pnpm dev    # http://localhost:4322
```

## Build

```bash
pnpm build  # ./dist/
```

Configurable at build time via:

- `POOL_URL` — Stratum URL (default `stratum+tcp://pool.animica.org:3333`)
- `MINING_API_BASE_URL` — pool-stats JSON API (default `https://pool.animica.org`)
- `AICF_API_BASE_URL` — AICF-stats JSON API

## Relationship to the main site

The main `animica-website` keeps building unchanged. A redirect in
`website/_redirects` and the production Nginx snippet send `/mine` -> `https://pool.animica.org/` so existing
miner bookmarks / CLI prompts / docs that reference the old URL keep
working.

The mining-pool deploy is **independent** of the main site deploy:

- This project deploys to `pool.animica.org`.
- The main site deploys to `animica.org`.
- Neither blocks the other.
