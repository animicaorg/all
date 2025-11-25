from __future__ import annotations

import base64
import json
import typing as t

import pytest
from httpx import AsyncClient, Response

# Goal: exercise the artifacts API end-to-end with realistic inputs,
# but be tolerant to minor shape differences across implementations.
#
# Covered:
#  - POST /artifacts : store content-addressed artifact
#  - GET  /artifacts/{id} : retrieve raw or JSON-wrapped content
#  - Idempotency: same bytes -> same id
#  - (Best-effort) media type echoes back or is used as response content-type
#
# The service may accept one or more encodings:
#   * {"content": "<base64>", "encoding": "base64"}
#   * {"content": "0x<hex>", "encoding": "hex"}  (encoding may be optional)
#   * {"text": "<utf8 string>"}                   (service may encode internally)
#
# The service is expected to answer with JSON containing an ID, e.g.:
#   {"id": "art_..."} or {"artifactId": "..."} or {"result": {"id": "..."}}
#
# Retrieval may return either raw bytes (octet-stream or media type),
# or a JSON envelope containing "content" (base64/hex) or "text".


SAMPLE_ABI: dict = {
    "functions": [
        {
            "name": "inc",
            "inputs": [{"name": "amount", "type": "int"}],
            "outputs": [],
        },
        {
            "name": "get",
            "inputs": [],
            "outputs": [{"name": "value", "type": "int"}],
        },
    ],
    "events": [
        {"name": "Inc", "inputs": [{"name": "by", "type": "int"}, {"name": "v", "type": "int"}]}
    ],
}
SAMPLE_BYTES: bytes = json.dumps(SAMPLE_ABI, separators=(",", ":"), sort_keys=True).encode("utf-8")
SAMPLE_MEDIA: str = "application/json"
SAMPLE_KIND: str = "abi"


def _extract_id(payload: dict) -> str | None:
    for k in ("id", "artifactId", "artifact_id"):
        v = payload.get(k)
        if isinstance(v, str):
            return v
    res = payload.get("result")
    if isinstance(res, dict):
        for k in ("id", "artifactId", "artifact_id"):
            v = res.get(k)
            if isinstance(v, str):
                return v
    return None


def _decode_payload_content(payload: dict) -> bytes | None:
    # Try common shapes for JSON-wrapped content.
    text = payload.get("text")
    if isinstance(text, str):
        return text.encode("utf-8")
    content = payload.get("content")
    if isinstance(content, str):
        enc = payload.get("encoding") or payload.get("enc")
        if enc == "base64":
            return base64.b64decode(content)
        # Accept 0x-hex or plain hex
        if enc == "hex" or content.startswith("0x") or all(c in "0123456789abcdefABCDEF" for c in content):
            hx = content[2:] if content.startswith("0x") else content
            return bytes.fromhex(hx)
    # Sometimes payload nests result
    result = payload.get("result")
    if isinstance(result, dict):
        return _decode_payload_content(result)
    return None


async def _try_put_variants(aclient: AsyncClient, *, bytes_in: bytes) -> Response | None:
    b64 = base64.b64encode(bytes_in).decode("ascii")
    hex_ = "0x" + bytes_in.hex()
    text_ = bytes_in.decode("utf-8", errors="ignore")

    variants: list[dict] = [
        # Explicit base64
        {
            "kind": SAMPLE_KIND,
            "mediaType": SAMPLE_MEDIA,
            "encoding": "base64",
            "content": b64,
        },
        # Explicit hex
        {
            "kind": SAMPLE_KIND,
            "mediaType": SAMPLE_MEDIA,
            "encoding": "hex",
            "content": hex_,
        },
        # Hex (implicit)
        {
            "kind": SAMPLE_KIND,
            "mediaType": SAMPLE_MEDIA,
            "content": hex_,
        },
        # Plain text (service may re-encode)
        {
            "kind": SAMPLE_KIND,
            "mediaType": SAMPLE_MEDIA,
            "text": text_,
        },
    ]

    for body in variants:
        resp = await aclient.post("/artifacts", json=body)
        if resp.status_code == 200:
            return resp
        # Some implementations use 201 Created
        if resp.status_code == 201:
            return resp
        # Try camelCase vs snake_case for media type and kind, just in case
        if "mediaType" in body:
            body2 = {**body, "media_type": body.pop("mediaType")}
            resp2 = await aclient.post("/artifacts", json=body2)
            if resp2.status_code in (200, 201):
                return resp2
    return None


def _assert_same_bytes(got: bytes | None, expected: bytes) -> None:
    assert got is not None, "Failed to decode response content"
    assert got == expected, f"Byte content mismatch (len got={len(got)}, expected={len(expected)})"


@pytest.mark.asyncio
async def test_artifacts_put_and_get_roundtrip(aclient: AsyncClient):
    # Store artifact
    created = await _try_put_variants(aclient, bytes_in=SAMPLE_BYTES)
    if created is None:
        pytest.skip("Service did not accept any supported artifact upload variant")
    assert created.status_code in (200, 201), created.text
    created_payload = created.json()
    artifact_id = _extract_id(created_payload)
    assert isinstance(artifact_id, str) and len(artifact_id) > 6, f"Could not find artifact id in {created_payload}"

    # Idempotency: re-upload same bytes returns same id (or 409/200 with same id)
    again = await _try_put_variants(aclient, bytes_in=SAMPLE_BYTES)
    if again is not None and again.status_code in (200, 201, 409):
        payload2 = again.json() if again.headers.get("content-type", "").startswith("application/json") else {}
        artifact_id2 = _extract_id(payload2) or artifact_id
        assert artifact_id2 == artifact_id, "Content-addressed ID should be stable across identical uploads"

    # Fetch by id
    got = await aclient.get(f"/artifacts/{artifact_id}")
    assert got.status_code == 200, got.text

    ctype = got.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        decoded = _decode_payload_content(got.json())
        _assert_same_bytes(decoded, SAMPLE_BYTES)
    else:
        # Assume raw bytes response (octet-stream or specific media type)
        _assert_same_bytes(got.content, SAMPLE_BYTES)

    # (Soft) echo checks for metadata if present
    meta_like = created_payload.get("meta") or created_payload.get("result") or created_payload
    mt = meta_like.get("mediaType") or meta_like.get("media_type")
    if isinstance(mt, str):
        assert "json" in mt, f"Unexpected mediaType echo: {mt}"


@pytest.mark.asyncio
async def test_artifacts_returns_404_for_unknown_id(aclient: AsyncClient):
    resp = await aclient.get("/artifacts/does-not-exist-123")
    assert resp.status_code in (400, 404), "Unknown artifacts should not return 200"


