# Website tests

This folder documents how we test the **Animica website** (Astro + TypeScript).  
We use a small, pragmatic stack:

- **Unit & component tests:** [Vitest](https://vitest.dev/) + [@testing-library](https://testing-library.com/) for TS/React islands and utilities.
- **E2E tests:** [Playwright](https://playwright.dev/) against a local `astro preview` server.
- **Coverage:** [c8](https://github.com/bcoe/c8) (via Vitest).
- **Static checks:** ESLint, TypeScript `--noEmit`, dead-link checker, and sitemap/chain metadata build checks.

Most of the runner scripts live in `website/package.json` and are wired to CI.

---

## Prerequisites

- **Node.js** 18+ (LTS recommended)
- **pnpm** 8+ (or npm/yarn; examples use pnpm)
- One-time Playwright deps (for E2E):
  ```bash
  npx playwright install --with-deps


⸻

Environment

Copy the example env and adjust as needed:

cp website/.env.example website/.env
# Edit PUBLIC_RPC_URL, PUBLIC_* if you want live status/TPS on pages.
# For tests, defaults are safe; API routes fall back to local JSON generation.

The site is careful to never require secrets; all variables are PUBLIC_*.

⸻

Commands

From the website/ directory:

Install

pnpm install

Type checks & lint

pnpm run typecheck     # strict TS across src/**
pnpm run lint          # eslint (Astro/TS/React)
pnpm run format        # prettier write
pnpm run format:check  # prettier check only

Unit tests (Vitest)

pnpm run test:unit     # runs vitest in CI mode with coverage

	•	Place tests next to code (*.test.ts(x)), e.g.
	•	src/utils/format.test.ts
	•	src/components/StatusBadge.test.tsx

E2E tests (Playwright)

pnpm run build         # build the Astro site
pnpm run preview &     # start preview on a random port
PREVIEW_PID=$!

pnpm run test:e2e      # runs playwright tests (expects preview server)

kill $PREVIEW_PID

The test:e2e script automatically discovers the preview URL from Astro’s console output. If you run preview on a fixed port, export SITE_BASE_URL to override:

SITE_BASE_URL="http://localhost:4321" pnpm run test:e2e



All tests

pnpm run test          # convenient aggregate (unit first, then e2e if preview is up)

Dead link checker

pnpm run check:links   # crawls built site or preview, reports 4xx/5xx

Build aux (used by CI)

pnpm run chains:build  # merge/validate /chains/*.json → runtime bundle
pnpm run sitemap       # generate sitemap.xml
pnpm run redirects     # produce dynamic redirects from env


⸻

Test layout & conventions
	•	Unit tests live alongside source:

src/
  utils/
    format.ts
    format.test.ts
  components/
    StatusBadge.tsx
    StatusBadge.test.tsx


	•	E2E tests typically live under tests/e2e/ (create as needed). Keep them focused:
	•	landing.spec.ts — loads /, checks hero, metrics ticker renders
	•	status.spec.ts — verifies live RPC status badge transitions
	•	Prefer data-testid attributes over brittle text queries for dynamic elements.
	•	Keep snapshots small and stable; prefer explicit assertions.

⸻

Running locally

Fast loop for UI work:

pnpm run dev
# In another terminal:
pnpm run test:unit -- --watch

For full-stack checks:

pnpm run build && pnpm run preview
pnpm run test:e2e


⸻

CI notes
	•	CI calls the same scripts listed here.
	•	E2E installs Playwright browsers via npx playwright install --with-deps.
	•	Artifacts (screenshots/traces on failure) can be enabled by setting:

export PWDEBUG=1

or configuring Playwright’s use: { trace: 'on-first-retry' } in playwright.config.ts.

⸻

Accessibility & performance (optional)

You can add lightweight checks to E2E:
	•	axe-core for basic a11y:

// inside a Playwright test
await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
await page.evaluate(async () => await (window as any).axe.run());


	•	Lighthouse CI can be wired to run against preview in a separate job.

⸻

Troubleshooting
	•	E2E cannot find server — ensure astro preview is running and reachable; set SITE_BASE_URL.
	•	RPC-dependent pages flaky — the site degrades gracefully; for deterministic tests, stub network calls or rely on the built-in /api/* routes that synthesize status.
	•	Types failing under Astro — verify tsconfig.json is the project root, and your editor uses workspace TypeScript.

⸻

FAQ

Why Playwright over Cypress?
Small, fast, first-party browser engines, and great trace artifacts.

Why Vitest?
Unit tests run fast and share Vite config with Astro.

Do I need Docker?
No. Everything runs locally with Node and the preview server.

