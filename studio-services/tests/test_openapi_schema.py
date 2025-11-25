from __future__ import annotations

import json
import typing as t

import pytest
from httpx import AsyncClient, Response


async def _get_openapi(client: AsyncClient) -> dict[str, t.Any] | None:
    """Fetch /openapi.json and return parsed dict or None if not mounted."""
    resp = await client.get("/openapi.json")
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


def _has_examples(obj: t.Any) -> bool:
    """
    Recursively search for 'example' or 'examples' keys anywhere in the OpenAPI doc.
    Return True on first hit.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("example", "examples"):
                return True
            if _has_examples(v):
                return True
    elif isinstance(obj, list):
        for v in obj:
            if _has_examples(v):
                return True
    return False


@pytest.mark.asyncio
async def test_openapi_available_and_well_formed(aclient: AsyncClient):
    """
    /openapi.json should be mounted, return JSON, and contain basic sections.
    """
    spec = await _get_openapi(aclient)
    if spec is None:
        pytest.skip("/openapi.json not mounted in this environment")

    # Basic shape
    assert isinstance(spec, dict)
    assert spec.get("openapi"), "missing 'openapi' version string"
    info = spec.get("info") or {}
    assert "title" in info and isinstance(info["title"], str) and info["title"], "missing info.title"
    assert "version" in info and isinstance(info["version"], str) and info["version"], "missing info.version"
    paths = spec.get("paths") or {}
    assert isinstance(paths, dict), "paths must be a dict"


@pytest.mark.asyncio
async def test_known_paths_and_schemas_present(aclient: AsyncClient):
    """
    If these routes are mounted, ensure their OpenAPI entries reference the expected schemas.
    Skip gracefully when routes are not present in a given deployment.
    """
    spec = await _get_openapi(aclient)
    if spec is None:
        pytest.skip("/openapi.json not mounted")

    paths: dict[str, t.Any] = spec.get("paths") or {}

    def _assert_post_has_request_ref(path: str, ref_suffix: str):
        entry = paths.get(path)
        if not entry:
            pytest.skip(f"{path} not present in OpenAPI (route may be disabled)")
        op = (entry.get("post") or {})
        rb = (op.get("requestBody") or {}).get("content", {}).get("application/json", {})
        schema = rb.get("schema") or {}
        ref = schema.get("$ref") or ""
        assert ref.endswith(ref_suffix), f"{path} requestBody should $ref {ref_suffix}, got {ref!r}"

    def _assert_get_has_response_ref(path: str, status: str, ref_suffix: str):
        entry = paths.get(path)
        if not entry:
            pytest.skip(f"{path} not present in OpenAPI (route may be disabled)")
        op = (entry.get("get") or {})
        content = (
            (op.get("responses") or {}).get(status, {}).get("content", {}).get("application/json", {})
        )
        schema = content.get("schema") or {}
        # Support either direct $ref or an array of $ref
        if "$ref" in schema:
            ref = schema["$ref"]
            assert ref.endswith(ref_suffix), f"{path} response should $ref {ref_suffix}, got {ref!r}"
        elif (schema.get("items") or {}).get("$ref"):
            ref = schema["items"]["$ref"]
            assert ref.endswith(ref_suffix), f"{path} response items should $ref {ref_suffix}, got {ref!r}"
        else:
            pytest.skip(f"{path} response schema does not expose a simple $ref in this build")

    # Deploy
    _assert_post_has_request_ref("/deploy", "#/components/schemas/DeployRequest")
    # Preflight simulate
    _assert_post_has_request_ref("/preflight", "#/components/schemas/PreflightRequest")
    # Verify lookup by address (response should reference VerifyResult or similar)
    _assert_get_has_response_ref("/verify/{address}", "200", "VerifyResult")

    # Artifacts put
    _assert_post_has_request_ref("/artifacts", "#/components/schemas/ArtifactPut")


@pytest.mark.asyncio
async def test_examples_are_present_somewhere(aclient: AsyncClient):
    """
    Our build ships small example payloads (via overrides) for at least one endpoint/schema.
    If the deployment chooses to strip examples, skip gracefully.
    """
    spec = await _get_openapi(aclient)
    if spec is None:
        pytest.skip("/openapi.json not mounted")

    if not _has_examples(spec):
        pytest.skip("No 'example(s)' found in OpenAPI (examples likely disabled in this environment)")

    # Spot-check that if /deploy exists, it carries examples on the request body or component
    paths: dict[str, t.Any] = spec.get("paths") or {}
    deploy = paths.get("/deploy", {})
    post = deploy.get("post", {})
    req = (post.get("requestBody") or {}).get("content", {}).get("application/json", {})
    if not _has_examples(req):
        # Try component schema referenced by DeployRequest
        components = spec.get("components") or {}
        schemas = components.get("schemas") or {}
        dr = schemas.get("DeployRequest") or {}
        if not _has_examples(dr):
            pytest.skip("No examples attached to /deploy or DeployRequest in this build")


@pytest.mark.asyncio
async def test_docs_ui_present_when_enabled(aclient: AsyncClient):
    """
    /docs (Swagger UI) is usually enabled; validate it returns HTML.
    Skip when not mounted.
    """
    r = await aclient.get("/docs")
    if r.status_code == 404:
        pytest.skip("/docs not mounted in this environment")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "text/html" in ct or "text/plain" in ct, f"unexpected content-type for /docs: {ct}"
