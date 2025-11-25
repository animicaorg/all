# SPDX-License-Identifier: MIT
"""
Shared pytest fixtures:
- Temporary workspace (per-session & per-test)
- Minimal JSON-RPC client with retries
- Funded keypairs loaded from sdk/test-harness/fixtures/accounts.json
- Genesis config helper aligned with the 18,000,000 premine split
- Common CLI options: --rpc-url, --ws-url, --chain-id
"""
from __future__ import annotations

import dataclasses
import json
import os
import random
import time
import typing as t
from pathlib import Path
from urllib import request
from urllib.error import URLError, HTTPError

import pytest


# ---------- CLI OPTIONS ----------

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--rpc-url",
        action="store",
        default=os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545"),
        help="JSON-RPC endpoint for node (env: ANIMICA_RPC_URL)",
    )
    parser.addoption(
        "--ws-url",
        action="store",
        default=os.getenv("ANIMICA_WS_URL", "ws://127.0.0.1:8546"),
        help="WebSocket endpoint for node (env: ANIMICA_WS_URL)",
    )
    parser.addoption(
        "--chain-id",
        action="store",
        default=os.getenv("ANIMICA_CHAIN_ID", "animica-devnet"),
        help="Chain ID to use when building/signing (env: ANIMICA_CHAIN_ID)",
    )


# ---------- TEMP WORKSPACES ----------

@pytest.fixture(scope="session")
def session_tmp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A single temp directory for the whole test session."""
    return tmp_path_factory.mktemp("animica-tests")


@pytest.fixture
def workdir(session_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Per-test working directory under the session temp.
    Also sets a few environment variables many tests rely on.
    """
    d = session_tmp / f"case-{int(time.time()*1e6)}-{random.randrange(1<<16):04x}"
    d.mkdir(parents=True, exist_ok=True)

    # Propagate defaults for SDKs & apps
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(d))
    monkeypatch.setenv("ANIMICA_CACHE_DIR", str(d / "cache"))
    (d / "cache").mkdir(exist_ok=True)

    return d


# ---------- JSON-RPC CLIENT ----------

class JsonRpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: t.Any | None = None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.data = data


class JsonRpcClient:
    """
    Tiny, dependency-free JSON-RPC client with basic retries and timeouts.
    """

    def __init__(self, url: str, timeout: float = 10.0, retries: int = 2, backoff: float = 0.25):
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._counter = 0

    def call(self, method: str, params: t.Any = None) -> t.Any:
        self._counter += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._counter, "method": method, "params": params or []}).encode()
        req = request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        attempt = 0
        last_err: Exception | None = None
        while attempt <= self.retries:
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                payload = json.loads(raw.decode("utf-8"))
                if "error" in payload and payload["error"] is not None:
                    err = payload["error"]
                    raise JsonRpcError(err.get("code", -32000), err.get("message", "Unknown error"), err.get("data"))
                return payload.get("result")
            except (URLError, HTTPError, TimeoutError) as e:
                last_err = e
                if attempt == self.retries:
                    break
                time.sleep(self.backoff * (2 ** attempt))
                attempt += 1
        assert last_err is not None
        raise last_err


@pytest.fixture(scope="session")
def rpc_url(pytestconfig: pytest.Config) -> str:
    return t.cast(str, pytestconfig.getoption("--rpc-url"))


@pytest.fixture(scope="session")
def ws_url(pytestconfig: pytest.Config) -> str:
    return t.cast(str, pytestconfig.getoption("--ws-url"))


@pytest.fixture(scope="session")
def chain_id(pytestconfig: pytest.Config) -> str:
    return t.cast(str, pytestconfig.getoption("--chain-id"))


@pytest.fixture(scope="session")
def rpc(rpc_url: str) -> JsonRpcClient:
    return JsonRpcClient(rpc_url)


# ---------- KEYPAIRS / FUNDED ACCOUNTS ----------

@dataclasses.dataclass(frozen=True)
class Keypair:
    """
    Generic key container used by tests. 'scheme' is informational and may
    reflect 'pq-dilithium3', 'pq-sphincs+', 'ed25519', etc.
    """
    address: str
    private_key: bytes | None = None
    public_key: bytes | None = None
    scheme: str = "unknown"


def _as_bytes(s: str | bytes | None) -> bytes | None:
    if s is None:
        return None
    if isinstance(s, bytes):
        return s
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    try:
        return bytes.fromhex(s)
    except ValueError:
        return s.encode("utf-8")


def _load_accounts_json() -> list[dict[str, t.Any]] | None:
    """
    Attempts to load funded accounts from known fixture locations.
    Returns None if not found.
    """
    candidates = [
        Path("sdk/test-harness/fixtures/accounts.json"),
        Path("tests/fixtures/accounts.json"),
        Path("fixtures/accounts.json"),
    ]
    for p in candidates:
        if p.is_file():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
    return None


def _normalize_account(obj: dict[str, t.Any]) -> Keypair | None:
    """
    Accepts multiple shapes, e.g.:
    - {"address": "...", "privateKey": "0x...", "publicKey": "0x...", "scheme": "pq-dilithium3"}
    - {"addr":"...","sk":"0x...","pk":"0x..."}
    - {"address":"..."}  # no keys (RPC-only tests)
    """
    address = obj.get("address") or obj.get("addr") or obj.get("bech32") or obj.get("hex")
    if not address:
        return None
    scheme = obj.get("scheme") or obj.get("type") or "unknown"
    pk = obj.get("privateKey") or obj.get("priv") or obj.get("sk")
    pub = obj.get("publicKey") or obj.get("pub") or obj.get("pk")
    return Keypair(
        address=str(address),
        private_key=_as_bytes(pk),
        public_key=_as_bytes(pub),
        scheme=str(scheme),
    )


@pytest.fixture(scope="session")
def funded_keypairs() -> list[Keypair]:
    """
    Funded accounts for tests. If fixtures are missing, returns an empty list.
    """
    raw = _load_accounts_json()
    if not raw:
        return []
    keys: list[Keypair] = []
    for entry in raw:
        kp = _normalize_account(entry)
        if kp:
            keys.append(kp)
    return keys


@pytest.fixture
def any_funded(funded_keypairs: list[Keypair]) -> Keypair | None:
    """Convenience fixture: return the first funded keypair, or None."""
    return funded_keypairs[0] if funded_keypairs else None


# ---------- GENESIS / PREMINE HELPERS ----------

DECIMALS = int(os.getenv("ANIMICA_DECIMALS", "9"))  # base units (default 9)
TOTAL_PREMINE = 18_000_000  # constant


def to_base_units(amount_whole: int | float) -> int:
    return int(round(float(amount_whole) * (10 ** DECIMALS)))


def premine_distribution() -> dict[str, int]:
    """
    Updated premine split (dev_reserve and treasury revised):
      treasury:    8,800,000
      aicf:        4,500,000
      foundation:  1,800,000
      faucet:        720,000
      dev_reserve: 2,180,000
    Returns values in base units (10^DECIMALS).
    """
    return {
        "treasury": to_base_units(8_800_000),
        "aicf": to_base_units(4_500_000),
        "foundation": to_base_units(1_800_000),
        "faucet": to_base_units(720_000),
        "dev_reserve": to_base_units(2_180_000),
    }


@pytest.fixture(scope="session")
def genesis_config() -> dict[str, t.Any]:
    """
    Minimal genesis config shape used by tests that need monetary constants.
    """
    return {
        "chainId": os.getenv("ANIMICA_CHAIN_ID", "animica-devnet"),
        "decimals": DECIMALS,
        "premine": premine_distribution(),
        "totalPremine": to_base_units(TOTAL_PREMINE),
    }


# ---------- OPTIONAL: ADDRESS VALIDATION (if omni_sdk is available) ----------

try:
    # Best-effort import; tests that don't need it won't fail if unavailable.
    from omni_sdk.address import validate_address  # type: ignore
except Exception:  # pragma: no cover
    def validate_address(addr: str) -> bool:  # fallback
        # Basic check: bech32-ish or 0x-hex; real validation lives in omni_sdk.address
        if not isinstance(addr, str) or len(addr) < 8:
            return False
        if addr.startswith(("0x", "0X")) and all(c in "0123456789abcdefABCDEF" for c in addr[2:]):
            return True
        return any(addr.startswith(hrp + "1") for hrp in ("an", "animica", "am"))


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch: pytest.MonkeyPatch, rpc_url: str, ws_url: str, chain_id: str) -> None:
    """
    Autouse: ensure well-known environment variables exist for tests.
    """
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    monkeypatch.setenv("ANIMICA_WS_URL", ws_url)
    monkeypatch.setenv("ANIMICA_CHAIN_ID", chain_id)


# ---------- HEALTH CHECK UTILS ----------

@pytest.fixture
def rpc_health(rpc: JsonRpcClient) -> t.Callable[[], bool]:
    """
    Returns a callable that tries a lightweight health probe.
    Your node may expose different methods; we try a few common ones.
    """
    def _probe() -> bool:
        candidates = [
            ("animica_health", []),
            ("system_health", []),
            ("net_version", []),
            ("chain_id", []),
            ("animica_chainId", []),
            ("web3_clientVersion", []),
        ]
        for m, p in candidates:
            try:
                _ = rpc.call(m, p)
                return True
            except Exception:
                continue
        return False
    return _probe
