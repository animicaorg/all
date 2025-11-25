# Animica — Website

Marketing & docs hub for the Animica ecosystem (wallet, explorer, SDKs, DEX, whitepapers).  
Fast, accessible, and easy to deploy to **Vercel**, **Netlify**, or **GitHub Pages**.

---

## Goals

- **Tell the story**: what Animica is, why it exists, who it’s for.
- **Clear CTAs**: download wallet, open explorer, read docs, join community.
- **Performant**: lighthouse ≥ 95 on desktop, ≥ 90 on mobile; TTI < 3s on 4G.
- **Accessible**: WCAG 2.1 AA; keyboard nav & screen-reader friendly.
- **Operationally simple**: zero server state; static export friendly.

> Non-goals: complex server rendering, authenticated flows, or dynamic dashboards (those live in explorer / app UIs).

---

## Tech Stack

- **Next.js (App Router) + TypeScript**
- **MDX** for long-form docs/announcements
- **Tailwind (optional)** for utility styling
- **ESLint + Prettier** for consistency

The site is designed to run as a static export (`out/`) for simple hosting. If you prefer SSR on Vercel/Netlify Functions, you can omit the static export step.

---

## Prerequisites

- **Node.js ≥ 20 LTS**
- **pnpm ≥ 9** (recommended) — or npm/yarn if you prefer
- Git

```sh
corepack enable                # enables pnpm if not already
pnpm -v
node -v


⸻

Quickstart (Local Dev)

pnpm install
pnpm dev

	•	Dev server: http://localhost:3000
	•	Edits hot-reload instantly.

Common scripts (package.json):

pnpm dev         # start dev server
pnpm build       # build for production (static export)
pnpm preview     # serve the built site locally
pnpm lint        # eslint + typecheck

Static export: ensure your next.config.js contains:

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',        // produces ./out for static hosting
  images: { unoptimized: true }, // GH Pages/Netlify static compatibility
};
export default nextConfig;



⸻

Environment Variables

Create website/.env.local (all optional):

NEXT_PUBLIC_APP_URL=https://animica.org
NEXT_PUBLIC_CHAIN_ID=1
NEXT_PUBLIC_RPC_URL=https://rpc.animica.org
NEXT_PUBLIC_ANALYTICS_KEY=

These are read at build time; never put secrets in NEXT_PUBLIC_*.

⸻

Content Authoring
	•	Pages live under website/src/app/** (App Router).
	•	MDX supported in website/src/content/** (if configured).
	•	Use descriptive titles & meta tags; provide social images (OG/Twitter).

SEO checklist:
	•	<title> and <meta name="description"> per page
	•	OpenGraph / Twitter Card tags
	•	Canonical URLs
	•	Sitemap & robots.txt

⸻

Deployment

You can deploy either as static export (recommended) or as server-rendered.

1) Vercel (recommended)

Static export route
	1.	Set output: 'export' in next.config.js.
	2.	Build output dir is out/.
	3.	In Vercel project settings:
	•	Framework: Next.js
	•	Build command: pnpm build
	•	Output directory: out
	•	Environment vars: add any NEXT_PUBLIC_*
	4.	Deploy.

SSR route
	•	Remove output: 'export' and deploy normally; Vercel will handle SSR edges.

2) Netlify

Static export
	1.	next.config.js with output: 'export'.
	2.	Netlify:
	•	Build command: pnpm build
	•	Publish directory: out
	3.	(Optional) Add a _redirects file for pretty URLs if needed.

SSR
	•	Omit static export and use the official @netlify/plugin-nextjs.

3) GitHub Pages

Static export only (no SSR):
	1.	next.config.js:
	•	For a project page (e.g., https://org.github.io/website), set:

const repoName = 'website';
const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  basePath: `/${repoName}`,
  assetPrefix: `/${repoName}/`,
};
export default nextConfig;


	•	For a user/organization page (https://org.github.io), you can skip basePath.

	2.	Add GitHub Action .github/workflows/pages.yml:

name: Deploy Website
on:
  push:
    branches: [ main ]
permissions:
  contents: read
  pages: write
  id-token: write
jobs:
  build-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: corepack enable && corepack prepare pnpm@9 --activate
      - run: pnpm install --frozen-lockfile
      - run: pnpm build
      - uses: actions/upload-pages-artifact@v3
        with: { path: out }
      - uses: actions/deploy-pages@v4


	3.	In repo settings:
	•	Pages → Source: GitHub Actions.
	•	Wait for the action to publish.

⸻

Performance & Accessibility
	•	Run checks locally:

pnpm build && pnpm preview
npx lighthouse http://localhost:4173 --view


	•	Include alt text, label interactive controls, ensure focus outlines, and test keyboard nav.

⸻

Troubleshooting
	•	Blank page on GH Pages: base path not set — ensure basePath and assetPrefix match the repo.
	•	Broken images on static hosts: set images.unoptimized = true.
	•	404 on refresh (non-index routes): configure SPA fallback (Netlify) or ensure exported routes exist.

⸻

Release Process
	1.	Bump version and changelog.
	2.	pnpm lint && pnpm build
	3.	Merge to main.
	4.	CI/CD publishes to selected target (Vercel/Netlify/GitHub Pages).

⸻

License & Credits
	•	Animica © Contributors.
	•	See LICENSE-THIRD-PARTY.md for upstream licenses.

