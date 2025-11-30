from __future__ import annotations

import asyncio
import typing as t

import pytest
from httpx import AsyncClient, Response

# Cheap routes that should always exist
HEALTH = "/healthz"
READY = "/readyz"

# Routes that exercise CORS (preflight against POST endpoints)
CORS_PROBE_PATHS = (
    "/deploy",  # POST
    "/simulate",  # POST
    "/artifacts",  # POST
)


# ---------------------------
# Helpers
# ---------------------------


async def _burst(
    client: AsyncClient,
    path: str,
    *,
    method: str = "GET",
    n: int = 40,
    delay: float = 0.0,
) -> list[Response]:
    """Send a small burst of requests to try to tick the per-route bucket."""
    out: list[Response] = []
    for _ in range(n):
        if method.upper() == "GET":
            r = await client.get(path)
        else:
            r = await client.post(path, json={})
        out.append(r)
        if delay:
            await asyncio.sleep(delay)
    return out


async def _options_preflight(
    client: AsyncClient,
    path: str,
    origin: str,
    method: str = "POST",
    req_headers: str = "content-type",
) -> Response:
    return await client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": req_headers,
        },
    )


def _is_present(resp: Response) -> bool:
    # 404 => route is not mounted; anything else means it exists (even 405/415 etc)
    return resp.status_code != 404


# ---------------------------
# Rate limiting (per-route buckets)
# ---------------------------


@pytest.mark.asyncio
async def test_per_route_bucket_isolated(aclient: AsyncClient):
    """
    Heat up /healthz until we observe a limit, then verify /readyz is *not*
    simultaneously limited (separate buckets).
    If the deployment does not enforce rate limits on health routes, skip gracefully.
    """
    # Make sure routes are there
    r0 = await aclient.get(HEALTH)
    r1 = await aclient.get(READY)
    if not (_is_present(r0) and _is_present(r1)):
        pytest.skip("Health/ready endpoints not mounted")

    # Try a decent burst to trigger limiter (if configured)
    burst = await _burst(aclient, HEALTH, n=60, delay=0.0)
    statuses = [r.status_code for r in burst]

    # If no limiter, skip (environment may have high/disabled limits)
    if not any(s in (429, 403) for s in statuses):
        pytest.skip("No observable rate limit on /healthz in this environment")

    # Immediately probe /readyz — should not be rate-limited if buckets are per-route
    r_ready = await aclient.get(READY)
    assert (
        200 <= r_ready.status_code < 300
    ), f"/readyz should not share /healthz bucket; got {r_ready.status_code}: {r_ready.text}"


@pytest.mark.asyncio
async def test_per_route_bucket_independent_post_route(aclient: AsyncClient):
    """
    If a POST route limiter is configured (e.g., /simulate), hitting it should not
    affect GET /healthz. Skip when POST route not mounted or not limited.
    """
    # Pick the first CORS-probe path that's actually mounted
    chosen: str | None = None
    for p in CORS_PROBE_PATHS:
        probe = await aclient.post(p, json={})
        if _is_present(probe):
            chosen = p
            break
    if not chosen:
        pytest.skip("No POST routes available for rate-limit test")

    # Heat up the POST route
    burst = await _burst(aclient, chosen, method="POST", n=40)
    if not any(r.status_code in (429, 403) for r in burst):
        pytest.skip(f"No observable rate limit on {chosen} in this environment")

    # GET on /healthz should still pass
    ok = await aclient.get(HEALTH)
    assert (
        200 <= ok.status_code < 300
    ), f"/healthz should not be limited by {chosen} bucket"


# ---------------------------
# CORS (allowed origins)
# ---------------------------


@pytest.mark.asyncio
async def test_cors_allows_configured_origin(aclient: AsyncClient):
    """
    Send an OPTIONS preflight from a likely-allowed origin (localhost).
    If CORS is not enabled, skip. Otherwise expect ACAO header to echo the
    origin or be wildcard.
    """
    # Find a POST endpoint to preflight
    target: str | None = None
    for p in CORS_PROBE_PATHS:
        probe = await aclient.options(p)
        if _is_present(probe):
            target = p
            break
    if not target:
        pytest.skip("No POST routes mounted for CORS preflight")

    origin = "http://localhost:5173"
    resp = await _options_preflight(aclient, target, origin)
    # CORS-enabled servers answer 200/204 and include ACAO
    acao = resp.headers.get("access-control-allow-origin")
    if not acao:
        pytest.skip("CORS not enabled in this environment (no ACAO header)")

    assert resp.status_code in (
        200,
        204,
    ), f"Unexpected preflight status: {resp.status_code}"
    assert acao in ("*", origin), f"ACAO should be '*' or echo origin; got {acao!r}"

    # Also check ACAM (methods) looks sane when present
    acam = resp.headers.get("access-control-allow-methods")
    if acam:
        assert (
            "POST" in acam or "post" in acam.lower()
        ), f"ACAM should allow POST; got {acam!r}"


@pytest.mark.asyncio
async def test_cors_blocks_disallowed_origin(aclient: AsyncClient):
    """
    Preflight from an obviously-bad origin. Expect 403 or no ACAO header.
    If the server is configured with wildcard CORS, skip.
    """
    # Choose a POST route again
    target: str | None = None
    for p in CORS_PROBE_PATHS:
        probe = await aclient.options(p)
        if _is_present(probe):
            target = p
            break
    if not target:
        pytest.skip("No POST routes mounted for CORS preflight")

    bad_origin = "https://evil.example.com"
    resp = await _options_preflight(aclient, target, bad_origin)

    acao = resp.headers.get("access-control-allow-origin")
    # If wildcard is used globally, nothing to assert here
    if acao == "*":
        pytest.skip("CORS is wildcard in this environment")

    # Two acceptable outcomes for a blocked origin: 403/401 (explicit block) OR 200 without ACAO
    blocked_status = resp.status_code in (401, 403)
    missing_header = acao is None

    assert (
        blocked_status or missing_header
    ), f"Expected CORS to block {bad_origin}. Got status={resp.status_code}, ACAO={acao!r}"
