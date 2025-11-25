# Studio Web — Deployment Guide

This document describes how to ship **Studio Web** to production safely:
- deterministic builds with Vite
- environment configuration
- CDN/static hosting patterns
- security & performance headers
- cache strategies and invalidation

> **Prereqs**
> - Node 18+ (or 20+), pnpm 8+ (or npm/yarn)
> - CI that can run unit/E2E tests
> - An HTTPS-capable host/CDN (e.g., Cloudflare, CloudFront, Vercel, Netlify, NGINX, S3+CF)

---

## 1) Build

Install deps and produce a production bundle:

```bash
# pick one package manager
pnpm install
pnpm build
# or: npm ci && npm run build

The build outputs to dist/ with content-hashed assets (Vite):

dist/
  index.html
  assets/app.2c4a1d7a.js
  assets/chunk-*.js
  assets/style.71b2c8e3.css
  ...

Artifacts are immutable (content hashed) and safe to cache for 1 year. Only index.html should be short-cached.

⸻

2) Environment Configuration

Studio Web reads runtime settings at build time via Vite’s import.meta.env using VITE_* variables.

Create an env file (see .env.example):

VITE_RPC_URL=https://rpc.animica.dev
VITE_CHAIN_ID=1
VITE_SERVICES_URL=https://services.animica.dev

Notes
	•	These are baked into the bundle. If you need environment-specific bundles (e.g., staging vs prod), build a bundle per environment.
	•	Keep RPC and Services on HTTPS with valid certificates.

⸻

3) Hosting Patterns

Option A — S3 + CloudFront (or GCS + Cloud CDN)
	•	Upload dist/ to a private bucket.
	•	Serve via CDN with:
	•	index.html → no-store
	•	assets/* → immutable 1y
	•	Configure custom error → index.html (SPA router fallback).

Option B — Cloudflare Pages / Vercel / Netlify
	•	Framework preset: Vite / SPA.
	•	Redirects:
	•	/*  /index.html  200
	•	Headers: set via project config (see examples below).

Option C — NGINX (bare metal)
	•	Serve dist/ from a read-only directory.
	•	Add headers & cache rules (snippet below).

⸻

4) Caching & Invalidation

Recommended rules
	•	index.html: Cache-Control: no-store
	•	assets/*: Cache-Control: public, max-age=31536000, immutable
	•	WASM/worker assets (if any): same as assets/*

Invalidation
	•	Vite emits content hashed filenames → deploys are atomic.
	•	Purge CDN cache for index.html only; assets rollover automatically.

⸻

5) Security & Performance Headers

Apply these at CDN/edge or origin:

Minimum set

Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
Content-Security-Policy: default-src 'self';
                         script-src 'self';
                         style-src 'self' 'unsafe-inline';
                         img-src 'self' data:;
                         connect-src 'self' https://services.animica.dev https://rpc.animica.dev wss://rpc.animica.dev;
                         frame-ancestors 'none';
                         base-uri 'self';
                         object-src 'none';
                         worker-src 'self';
                         child-src 'self';
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin

If you load third-party fonts, analytics, or Pyodide/WASM from CDNs, whitelist their origins in CSP and consider SRI (integrity="") for static assets.

MIME types

Content-Type: text/html; charset=utf-8        # index.html
Content-Type: text/javascript                 # .js
Content-Type: text/css                        # .css
Content-Type: application/wasm                # .wasm (if any in dependencies)

CORS

The Studio Web app itself is static and typically does not need CORS.
CORS must be configured on Studio Services and RPC endpoints to allow your app origin(s). Keep allowlists tight in production.

⸻

6) Example Configurations

NGINX

server {
  listen 443 ssl http2;
  server_name studio.example.com;

  root /var/www/studio/dist;
  index index.html;

  # Security headers
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header Referrer-Policy "no-referrer" always;
  add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;
  add_header Cross-Origin-Opener-Policy "same-origin" always;
  add_header Cross-Origin-Embedder-Policy "require-corp" always;
  add_header Cross-Origin-Resource-Policy "same-origin" always;

  # CSP (adjust connect-src to your RPC/Services)
  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://services.animica.dev https://rpc.animica.dev wss://rpc.animica.dev; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; worker-src 'self'; child-src 'self'" always;

  # Cache rules
  location = /index.html {
    add_header Cache-Control "no-store";
    try_files $uri /index.html;
  }

  location /assets/ {
    add_header Cache-Control "public, max-age=31536000, immutable";
    try_files $uri =404;
  }

  # SPA fallback
  location / {
    try_files $uri /index.html;
  }

  # Correct WASM type
  types { application/wasm wasm; }
}

CloudFront Behaviors
	•	Behavior 1: Default (*) → Origin: bucket/site
	•	Cache policy: Disable caching for index.html
	•	Function/Lambda@Edge: add security headers
	•	Behavior 2: /assets/*
	•	Cache policy: max-age=31536000, immutable
	•	Compression: Brotli + Gzip

Netlify _headers (optional)

/index.html
  Cache-Control: no-store
  Content-Security-Policy: default-src 'self'; ...

/assets/*
  Cache-Control: public, max-age=31536000, immutable

/* 
  Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Embedder-Policy: require-corp
  Cross-Origin-Resource-Policy: same-origin


⸻

7) CI Pipeline (suggested)
	1.	Install: pnpm install --frozen-lockfile
	2.	Lint & Unit: pnpm test
	3.	E2E (optional): run Playwright against a preview URL
	4.	Build: pnpm build
	5.	Upload: push dist/ to storage (S3 bucket, Pages, etc.)
	6.	Invalidate: purge CDN cache for index.html only

Expose build metadata (git commit, version) as headers or a JSON file in dist/ for traceability.

⸻

8) Observability
	•	Serve /version.json (optional) with { "version": "<git>", "builtAt": "<iso>" }.
	•	Monitor:
	•	CDN cache hit ratio
	•	4xx/5xx rates
	•	TLS errors
	•	Core Web Vitals (TTFB, LCP, CLS, INP)

⸻

9) Troubleshooting
	•	Blank page after deploy: Likely stale index.html cached by CDN. Ensure no-store.
	•	WASM/worker errors: Set correct MIME types; enable COOP/COEP; verify CSP for worker-src.
	•	CORS errors to RPC/Services: Add your app origin to their allowlist; avoid * in production.

⸻

10) Upgrade Policy
	•	Use semantic versions; bump VITE_* envs per environment and rebuild.
	•	Because assets are content-hashed, you can deploy frequently without worrying about cache poisoning.
	•	Rotate CSP/headers cautiously—test on staging first.

⸻

Studio Web ships as a static SPA. Security for Studio Services and RPC
endpoints (CORS, rate limits, API keys) must be enforced on those backends as
documented in their respective READMEs.
