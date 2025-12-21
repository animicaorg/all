# Animica — Website

Production marketing + docs hub for Animica (wallet, explorer, SDKs, node ops). Built on **Astro + TypeScript + Tailwind** with a static-first architecture.

## Goals

- **Modern + serious** design, dark-first with optional light mode
- **Fast** static delivery, minimal JS, optimized assets
- **Mobile-perfect** layouts and typography
- **Clear CTAs** for Wallet, Node, Explorer, Docs
- **Easy deploy** via Docker + nginx

## Tech Stack

- **Astro 4 + TypeScript**
- **Tailwind CSS** (utility + design tokens)
- **MDX + Content Collections** for blog/updates
- **Vitest + Playwright**

## Local Development

```bash
cd website
pnpm install
pnpm dev --host 0.0.0.0 --port 4321
```

Preview build:

```bash
pnpm build
pnpm preview --host 0.0.0.0 --port 4321
```

## Environment Variables

Create `website/.env` (or use `.env.local`) with the following:

```bash
ANIMICA_RPC_URL=https://rpc.animica.org
ANIMICA_EXPLORER_URL=https://explorer.animica.org
ANIMICA_EXPLORER2_URL=
ANIMICA_DOCS_URL=https://docs.animica.org
ANIMICA_GITHUB_URL=https://github.com/animicaorg/all
ANIMICA_STUDIO_URL=https://studio.animica.org
ANIMICA_FAUCET_URL=
ANIMICA_POOL_URL=
ANIMICA_DISCORD_URL=https://discord.gg/animica
ANIMICA_TELEGRAM_URL=
ANIMICA_X_URL=https://x.com/animica
ANIMICA_CHAIN_ID=1
```

> `SITE_URL` is optional and is used for sitemap/robots.

## Deployment (Docker + Nginx)

```bash
cd website
cp .env.example .env

docker compose -f docker-compose.website.yml up --build
```

The site is served at **http://localhost:4321** (nginx). For production, put a TLS reverse proxy in front of nginx.

### Files

- `website/docker/Dockerfile`
- `website/docker/nginx.conf`
- `website/docker-compose.website.yml`

## Tests

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```
