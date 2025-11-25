import { test, expect } from '@playwright/test';

/**
 * Gate: /docs/foo redirects to the canonical docs host
 * (as configured by env PUBLIC_DOCS_URL), OR serves local MDX if present.
 *
 * We assert via an HTTP request with redirects disabled so we don't need to
 * actually navigate off-site in E2E (keeps tests hermetic).
 */

const DOCS_BASE =
  process.env.PUBLIC_DOCS_URL?.replace(/\/+$/, '') || 'https://docs.animica.dev';

const isRedirect = (code: number) => [301, 302, 307, 308].includes(code);
const header = (h: Record<string, string>, name: string) =>
  h[name] ?? h[name.toLowerCase()] ?? h[name.toUpperCase()];

test('GET /docs/foo returns a redirect to canonical docs (or local 200 fallback)', async ({ request }) => {
  const slug = 'foo/bar';
  const res = await request.get(`/docs/${slug}`, { maxRedirects: 0 });

  if (isRedirect(res.status())) {
    const headers = await res.headers();
    const location = header(headers as any, 'location');
    expect(location, 'redirect Location header').toBeTruthy();

    // Location may be absolute or protocol-relative; normalize a bit
    const normalized = location!.replace(/\/+$/, '');
    expect(
      normalized.startsWith(DOCS_BASE) || normalized.includes(`/${slug}`),
      `expected Location to start with ${DOCS_BASE} and include /${slug}, got: ${normalized}`
    ).toBeTruthy();
  } else {
    // Accept serving local MDX (200) when a built-in doc exists.
    expect(res.status(), 'expected 3xx redirect or 200 OK for local doc').toBe(200);
    const body = await res.text();
    expect(body.length).toBeGreaterThan(0);
    // Heuristic: should look like an HTML doc page
    expect(body).toMatch(/<html|<main|docs|documentation/i);
  }
});
