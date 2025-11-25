# Changelog
All notable changes to this project will be documented in this file.

The format is based on **[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)**,
and this project adheres to **[Semantic Versioning](https://semver.org/spec/v2.0.0.html)**.

---

## [0.1.0] - 2025-10-09
### Added
- Initial **Astro + TypeScript** scaffolding for Animica website.
- Tooling & quality:
  - ESLint (`eslint`, `@typescript-eslint`, `eslint-plugin-astro`)
  - Prettier + `prettier-plugin-astro`
  - Strict `tsconfig.json` (Astro base, React islands ready)
  - Husky + lint-staged hooks (format on commit)
- Production configs:
  - `astro.config.mjs` with **MDX**, **Sitemap**, **Robots**, **Tailwind**, and **image optimizer**.
  - Hosting configs: **Netlify** (`netlify.toml`) and **Vercel** (`vercel.json`).
- Project hygiene:
  - `.editorconfig`, `.gitignore`, `.eslintrc.cjs`, `.prettierrc`
  - `.env.example` with `PUBLIC_*` variables (Studio/Explorer/Docs/RPC/ChainId)
- Scripts in `package.json`:
  - `dev`, `build`, `preview`, `check`, `typecheck`, `lint`, `format`
  - `test`, `test:ui`, `test:e2e` (Vitest + Playwright stubs)

### Notes
- Site is configured for **static export** (`out/`) by default.
- Update `SITE_URL` (env) for correct sitemap and robots output.
- Tailwind is included as optional; remove `@astrojs/tailwind` if not needed.

---

## [Unreleased]
### Planned
- Core pages: Home, Wallet, Explorer, SDKs, Docs landing, Blog/News.
- i18n scaffolding (en/es).
- Lighthouse CI workflow and accessibility checks.
- OG image generation pipeline.

