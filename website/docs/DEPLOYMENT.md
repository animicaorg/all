# Website Deployment (Vercel / Netlify / GitHub Pages)

This site is built with **Astro** (TypeScript, optional React “islands”) and supports:
- Fully static export, or
- Serverless/Edge endpoints for `src/pages/api/*` (status, newsletter, chainmeta, healthz).

Choose a target that matches your needs:

- **Vercel** — easiest SSR/Edge setup for `src/pages/api/*`.
- **Netlify** — Functions/Edge are supported via adapter.
- **GitHub Pages** — **static only** (no server endpoints). API routes must be disabled or mocked.

---

## 0) Prerequisites

1. **Node & package manager**
   - Node 18+ (LTS recommended)
   - `pnpm` (preferred) or `npm`/`yarn`

2. **Install deps & run locally**
   ```bash
   cd website
   pnpm install        # or npm install
   pnpm dev            # http://localhost:4321
   pnpm build          # produces ./dist
   pnpm preview        # serve the build locally

	3.	Environment variables (public)
Create .env (or set in your host) using .env.example:

PUBLIC_STUDIO_URL=https://studio.animica.org
PUBLIC_EXPLORER_URL=https://explorer.animica.org
PUBLIC_DOCS_URL=https://docs.animica.org
PUBLIC_RPC_URL=https://rpc.animica.org
PUBLIC_CHAIN_ID=1

Public variables are baked into the client bundle. Never put secrets in PUBLIC_*.

	4.	Adapters (for server endpoints)
If you will deploy API routes (src/pages/api/*) you must use an adapter:
	•	Vercel: @astrojs/vercel
	•	Netlify: @astrojs/netlify
For GitHub Pages, keep the default static output (no adapter).
Install the adapter you need:

pnpm add -D @astrojs/vercel    # or @astrojs/netlify

Then update astro.config.mjs:

import vercel from '@astrojs/vercel/serverless'; // or 'edge' if desired
export default defineConfig({
  site: 'https://your.site',
  output: 'server',                 // server for SSR/functions
  adapter: vercel(),                // or netlify()
  // ...other integrations
});

For static only (GitHub Pages):

export default defineConfig({
  site: 'https://your.github.io/your-repo',
  output: 'static'
});



⸻

1) Deploying to Vercel

One-time setup
	1.	Repo import: In Vercel dashboard → “Add New Project” → Import the repo containing /website.
	2.	Root Directory: Set website/ as the project root.
	3.	Framework Preset: Vercel should detect Astro automatically.
	4.	Build command & Output (auto-detected):
	•	Build: astro build (Vercel runs via adapter)
	•	Output (static mode): dist/ (for SSR, Vercel handles it; no manual output dir)
	5.	Environment Variables: Add all PUBLIC_* values in Project → Settings → Environment Variables.
	6.	Adapter: Ensure @astrojs/vercel is added and astro.config.mjs is set to output: 'server' if using API routes.

Edge & redirects
	•	This repo includes website/vercel.json with example redirects/headers if needed.
	•	API routes in src/pages/api/* will be deployed as Serverless by default.
	•	To force Edge, use the @astrojs/vercel/edge adapter and ensure your code is Edge-compatible.

Deploy
	•	Push to main → Vercel builds and deploys automatically.
	•	Preview deployments are created for PRs by default.

⸻

2) Deploying to Netlify

One-time setup
	1.	Site import: Netlify dashboard → “Add new site” → “Import an existing project”.
	2.	Base directory: website/
	3.	Build command: pnpm build (or npm run build)
	4.	Publish directory:
	•	Static mode: dist
	•	SSR/Functions: use @astrojs/netlify and set output: 'server' in astro.config.mjs. Netlify will create Functions automatically.

Netlify adapter & functions
	•	Install adapter: pnpm add -D @astrojs/netlify
	•	In astro.config.mjs:

import netlify from '@astrojs/netlify/functions';
export default defineConfig({
  output: 'server',
  adapter: netlify(),
});


	•	The included website/netlify.toml shows headers, redirects, and (optionally) Edge Functions mapping.

Environment Variables
	•	Netlify dashboard → Site settings → Build & deploy → Environment.
	•	Add all PUBLIC_* vars.

Deploy
	•	Push to main → Netlify builds and deploys.
	•	Branch deploys (previews) are enabled by default.

⸻

3) Deploying to GitHub Pages (Static Only)

GitHub Pages does not run server code. You must:
	•	Keep output: 'static' in astro.config.mjs.
	•	Remove or disable usage of src/pages/api/* endpoints (or gate them behind environment flags).
	•	Generate assets to /dist and publish that folder.

Steps
	1.	In repo settings → Pages:
	•	Source: GitHub Actions (recommended).
	2.	Add a workflow (e.g., .github/workflows/pages.yml):

name: Deploy Website (Pages)
on:
  push:
    branches: [ main ]
jobs:
  build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: website
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: 'pnpm'
      - run: pnpm install
      - run: pnpm build
      - uses: actions/upload-pages-artifact@v3
        with:
          path: website/dist
  deploy:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: \${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4


	3.	If your site is served under a subpath (e.g. /your-repo), set site in astro.config.mjs accordingly and ensure assets use correct base.

⸻

4) CDN, Caching & Headers
	•	Static hosting: long-cache immutable assets (/_astro/*, /assets/*).
	•	HTML: short TTL with ETag or revalidate on deploy.
	•	Security headers: Use vercel.json or netlify.toml to add CSP, HSTS, X-Frame-Options. Start permissive, then tighten CSP alongside your embedded content (YouTube, etc.).

⸻

5) Sitemaps, Robots & Redirects
	•	astro-sitemap integration generates a sitemap at build time when site is set in astro.config.mjs.
	•	website/public/robots.txt is served as-is.
	•	Redirects:
	•	Vercel: website/vercel.json or Project → Settings → Redirects.
	•	Netlify: website/netlify.toml or a _redirects file.
	•	Scripts:
	•	website/scripts/generate_sitemap.mjs (optional) – builds sitemap from routes.
	•	website/scripts/redirects_from_env.mjs (optional) – produces redirect map for Studio/Explorer/Docs deep links.

⸻

6) Analytics & Cookies
	•	Components:
	•	AnalyticsPlausible.astro
	•	AnalyticsPostHog.astro
	•	CookieBanner.astro
	•	Respect opt-in: components read consent via window.ANIMICA_COOKIE_PREFS and data-analytics attribute.
	•	To enable in dev, set PUBLIC_ANALYTICS_ON_DEV=true. Otherwise analytics are disabled locally.

⸻

7) Status & RPC Connectivity
	•	/status page & /src/components/MetricTicker.tsx ping PUBLIC_RPC_URL.
	•	For CI or preview envs without a real RPC, mock responses or point to a test endpoint.
	•	API route src/pages/api/status.json.ts fetches head/height/TPS from PUBLIC_RPC_URL. Works on Vercel/Netlify with the correct adapter; not available on GitHub Pages.

⸻

8) Troubleshooting
	•	Build succeeds locally but fails on host:
	•	Ensure the adapter matches your platform (Vercel/Netlify) and output: 'server' if using API routes.
	•	Confirm Node version (18+).
	•	All PUBLIC_* env vars set for Production environment (not only Preview).
	•	404s on subpath (GitHub Pages):
	•	Set site in astro.config.mjs to the full Pages URL.
	•	Use <a href={Astro.site?.pathname + '/path'}> or Astro’s built-in prependBase.
	•	CSP blocks scripts:
	•	Adjust script-src to allow your analytics and embedded providers (YouTube/Vimeo).
	•	Newsletter API failing:
	•	On GitHub Pages (static), this endpoint doesn’t exist. Use a third-party form backend or deploy the API on Vercel/Netlify and point the form there.

⸻

9) Deployment Matrix (Quick Reference)

Host	Output Mode	API (/api/*)	Adapter	Notes
Vercel	server/edge	✅	@astrojs/vercel	Easiest Edge/Serverless.
Netlify	server	✅	@astrojs/netlify	Functions/Edge supported.
GitHub Pages	static	❌	none (static export)	Pure static; disable API routes.


⸻

10) Release Checklist
	•	pnpm build succeeds locally.
	•	site set in astro.config.mjs (sitemap/OG URLs).
	•	Env vars set for target environment(s).
	•	Redirects verified (Studio/Explorer/Docs).
	•	Analytics consent banner works; analytics gated behind consent.
	•	Status page shows live data (or is hidden for static-only deployments).
	•	Lighthouse pass (performance/accessibility/SEO).
	•	404 page styled and deployed.

That’s it—ship it! 🚀
