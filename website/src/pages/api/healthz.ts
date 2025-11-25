import type { APIRoute } from "astro";

/**
 * /api/healthz
 * Ultra-light endpoint for uptime monitors.
 * Always returns 200 on GET/HEAD. Optional ?format=json for richer checks.
 */

const STARTED_AT = Date.now();

export const GET: APIRoute = async ({ request }) => {
  const url = new URL(request.url);
  if (url.searchParams.get("format") === "json") {
    const body = JSON.stringify({
      ok: true as const,
      uptimeSec: Math.floor((Date.now() - STARTED_AT) / 1000),
      timeUTC: new Date().toISOString(),
    });
    return new Response(body, {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  }

  return new Response("ok\n", {
    status: 200,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
    },
  });
};

export const HEAD: APIRoute = async () =>
  new Response(null, {
    status: 200,
    headers: { "cache-control": "no-store" },
  });
