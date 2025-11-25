# pytest: integrity checks — checksums.txt matches file hashes; endpoint URL formats are sane
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHAINS_DIR = ROOT / "chains"
REGISTRY_PATH = CHAINS_DIR / "registry.json"
CHECKSUMS_PATH = CHAINS_DIR / "checksums.txt"

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HTTP_OK = ("http://", "https://")
WS_OK = ("ws://", "wss://")


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _sha256_hex(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_checksums(path: Path) -> Dict[str, str]:
    """
    Parse lines of the form:
      <sha256>  <relative/path>
    Returns a dict { "relative/path": "sha256" }.
    """
    mp: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64 and HEX64.match(parts[0]):
            relpath = " ".join(parts[1:])
            mp[relpath] = parts[0].lower()
    return mp


@pytest.fixture(scope="session")
def registry():
    assert REGISTRY_PATH.exists(), f"missing {REGISTRY_PATH}"
    return _load_json(REGISTRY_PATH)


@pytest.fixture(scope="session")
def checksums():
    assert CHECKSUMS_PATH.exists(), f"missing {CHECKSUMS_PATH}; run chains/scripts/generate_checksums.py"
    return _parse_checksums(CHECKSUMS_PATH)


def test_checksums_include_registry_and_all_entries(registry, checksums):
    """
    checksums.txt must include:
      • chains/registry.json
      • every chain JSON referenced by the registry
    """
    assert "chains/registry.json" in checksums, "checksums.txt must include chains/registry.json"

    missing: List[str] = []
    for e in registry.get("entries", []):
        path = e.get("path")
        assert isinstance(path, str) and path, f"registry entry has invalid path: {e}"
        if path not in checksums:
            missing.append(path)

    assert not missing, "checksums.txt missing entries for:\n" + "\n".join(f"- {m}" for m in missing)


def test_file_hashes_match_checksums_and_embedded(registry, checksums):
    """
    For each chain JSON referenced in the registry:
      • raw file sha256 must equal the value in checksums.txt
      • if the JSON has an embedded 'checksum', it must equal the checksums.txt value
    """
    for e in registry.get("entries", []):
        rel = e["path"]
        file_hash_expected = checksums.get(rel)
        assert file_hash_expected and HEX64.match(file_hash_expected), f"bad or missing checksum for {rel}"

        p = ROOT / rel
        assert p.exists(), f"missing chain file: {p}"
        actual = _sha256_hex(p).lower()
        assert actual == file_hash_expected, f"hash mismatch for {rel}: file={actual} list={file_hash_expected}"

        # Embedded checksum (optional but, if present, should match list)
        try:
            obj = _load_json(p)
        except Exception as exc:
            pytest.fail(f"{rel} invalid JSON: {exc}")

        embedded = obj.get("checksum")
        if isinstance(embedded, str):
            assert HEX64.match(embedded.lower()) or embedded == "<sha256-to-be-generated>", (
                f"{rel}: embedded checksum has invalid format: {embedded!r}"
            )
            if HEX64.match(embedded.lower()):
                assert embedded.lower() == file_hash_expected, (
                    f"{rel}: embedded checksum != checksums.txt (embedded={embedded}, list={file_hash_expected})"
                )


def test_endpoint_url_formats(registry):
    """
    Basic format checks (no network I/O):
      • rpc.http[] URLs must be http(s)://
      • rpc.ws[]   URLs must be ws(s)://
      • explorers[].url must be http(s)://
    """
    for e in registry.get("entries", []):
        p = ROOT / e["path"]
        data = _load_json(p)

        # rpc.http
        http_urls = (data.get("rpc") or {}).get("http") or []
        assert isinstance(http_urls, list), f"{p}: rpc.http must be an array"
        for u in http_urls:
            assert isinstance(u, str) and u, f"{p}: rpc.http contains a non-string"
            assert u.startswith(HTTP_OK), f"{p}: rpc.http URL must start with http(s):// — got {u!r}"

        # rpc.ws
        ws_urls = (data.get("rpc") or {}).get("ws") or []
        assert isinstance(ws_urls, list), f"{p}: rpc.ws must be an array"
        for u in ws_urls:
            assert isinstance(u, str) and u, f"{p}: rpc.ws contains a non-string"
            assert u.startswith(WS_OK), f"{p}: rpc.ws URL must start with ws(s):// — got {u!r}"

        # explorers
        explorers = data.get("explorers") or []
        assert isinstance(explorers, list), f"{p}: explorers must be an array"
        for ex in explorers:
            assert isinstance(ex, dict), f"{p}: explorers[] must be objects"
            url = ex.get("url")
            assert isinstance(url, str) and url, f"{p}: explorer.url must be a non-empty string"
            assert url.startswith(HTTP_OK), f"{p}: explorer.url must start with http(s):// — got {url!r}"
