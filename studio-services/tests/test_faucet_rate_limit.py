from __future__ import annotations

import asyncio
import typing as t

import pytest
from httpx import AsyncClient, Response

FAUCET_PATH = "/faucet/drip"


def _has_faucet(resp: Response) -> bool:
    # If the route is mounted, 405/400/401/403 are all possible for a POST without proper auth/body.
    # A 404 strongly suggests the route is not present in this build.
    return resp.status_code != 404


async def _post_drip(
    client: AsyncClient,
    *,
    api_key: str | None = None,
    address: str | None = None,
    amount: int | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    headers: dict[str, str] = {}
    if api_key:
        # The service supports API key via Authorization: Bearer ... and/or X-API-Key header.
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    if extra_headers:
        headers.update(extra_headers)

    # Keep body permissive: some implementations require {address, amount}, others accept defaults.
    body: dict[str, t.Any] = {}
    if address:
        body["address"] = address
    if amount is not None:
        body["amount"] = amount

    return await client.post(
        FAUCET_PATH,
        json=body or {"address": "anim1testaddressplaceholder"},
        headers=headers,
    )


def _get_fixture(request: pytest.FixtureRequest, name: str) -> t.Any | None:
    try:
        return request.getfixturevalue(name)
    except Exception:
        return None


@pytest.mark.asyncio
async def test_faucet_requires_api_key(aclient: AsyncClient):
    # Probe route existence first
    probe = await aclient.post(FAUCET_PATH, json={})
    if not _has_faucet(probe):
        pytest.skip("Faucet route not mounted in this configuration")

    # Without API key we should be rejected up front
    resp = await _post_drip(aclient)
    assert resp.status_code in (
        401,
        403,
    ), f"Expected 401/403 without API key, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_faucet_rate_limited_per_key(
    request: pytest.FixtureRequest, aclient: AsyncClient
):
    # Skip gracefully if faucet is not enabled
    probe = await aclient.post(FAUCET_PATH, json={})
    if not _has_faucet(probe):
        pytest.skip("Faucet route not mounted in this configuration")

    # Try to obtain an API key from the test environment (conftest) if available.
    api_key: str | None = _get_fixture(request, "api_key")
    if not api_key:
        # Fall back to a default key commonly used in fixtures; if the service enforces key presence against a DB,
        # subsequent calls will return 401/403 and we will skip.
        api_key = "test-api-key"

    # Fire a small burst and observe responses.
    statuses: list[int] = []
    for _ in range(10):
        r = await _post_drip(aclient, api_key=api_key, amount=1)
        statuses.append(r.status_code)
        # tiny delay to avoid event-loop starvation; keeps calls inside a single rate window
        await asyncio.sleep(0.01)

    if all(s in (401, 403) for s in statuses):
        pytest.skip(
            "API key not accepted in this test environment; cannot validate rate limiting"
        )

    # We accept a variety of success codes (200, 201, 202); rate limiter should eventually emit 429 (or 403 in strict modes).
    got_2xx = any(200 <= s < 300 for s in statuses)
    got_limited = any(s in (429, 403) for s in statuses)

    if not got_limited:
        # Try another quick burst to cross any threshold.
        for _ in range(10):
            r = await _post_drip(aclient, api_key=api_key, amount=1)
            statuses.append(r.status_code)
            await asyncio.sleep(0.005)
        got_limited = any(s in (429, 403) for s in statuses)

    assert (
        got_2xx
    ), f"Expected at least one successful faucet response, got statuses={statuses}"
    assert (
        got_limited
    ), f"Expected rate limiting (429/403) after a burst, got statuses={statuses}"


@pytest.mark.asyncio
async def test_faucet_buckets_are_per_key(
    request: pytest.FixtureRequest, aclient: AsyncClient
):
    probe = await aclient.post(FAUCET_PATH, json={})
    if not _has_faucet(probe):
        pytest.skip("Faucet route not mounted in this configuration")

    key1: str | None = _get_fixture(request, "api_key") or "test-api-key"
    key2: str | None = _get_fixture(request, "api_key_2") or "test-api-key-2"

    # Exhaust (or at least heat up) key1
    for _ in range(8):
        await _post_drip(aclient, api_key=key1, amount=1)

    # Now try with a different key which should have a fresh bucket.
    resp = await _post_drip(aclient, api_key=key2, amount=1)
    if resp.status_code in (401, 403):
        pytest.skip(
            "Second API key not accepted in this test environment; cannot assert per-key isolation"
        )

    assert (
        200 <= resp.status_code < 300
    ), f"Different API key should not inherit key1's limiter, got {resp.status_code}: {resp.text}"
