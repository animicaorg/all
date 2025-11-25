"""
Animica Chains — Python bindings & verification helpers.

Usage:
  pip install pydantic jsonschema  # (jsonschema is optional but recommended)
  from animica_chains import load_chain, load_registry, verify_against_checksums

CLI:
  python -m animica_chains check chains/checksums.txt
  python -m animica_chains show animica-testnet
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, ConfigDict, ValidationError
except Exception as e:  # pragma: no cover
    raise RuntimeError("pydantic>=2 is required: pip install pydantic") from e

# jsonschema is optional; we validate via Pydantic by default and can double-check with jsonschema if present
try:  # pragma: no cover
    import jsonschema  # type: ignore
    _HAS_JSONSCHEMA = True
except Exception:
    _HAS_JSONSCHEMA = False


# ----------------------------- Pydantic Models (mirror chains/schemas/*.json) -----------------------------

class NativeCurrency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=32)
    symbol: str
    decimals: int = Field(ge=0, le=255)


class RpcCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http: List[str]
    ws: List[str]


class Explorer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str


class Faucet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str


class P2P(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocols: List[str]
    port: int = Field(ge=1, le=65535)
    seeds: List[str]
    bootnodes: List[str]


class Addresses(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["bech32m"]
    hrp: Literal["am"]
    pubkeyTypes: List[Literal["ed25519", "secp256k1", "dilithium3", "sphincs+"]]


class PQCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sigDefault: Literal["ed25519", "secp256k1", "dilithium3", "sphincs+"]
    kex: Literal["kyber-768", "ntru-hps-509"]
    policyVersion: str  # YYYY-MM


class VMCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lang: Literal["python"]
    version: str
    gasModel: str


class DACfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maxBlobSizeBytes: int = Field(ge=1)
    nmtNamespaceBytes: int = Field(ge=4, le=64)
    rsRate: str  # e.g., "10/16"


class RandomnessCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["vdf+qrng", "commit-reveal", "drand", "contract"]
    contract: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class GenesisCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hash: str
    timestamp: str
    initialHeight: int = Field(ge=0)


class GovernanceCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: Optional[str] = None
    votingPeriodDays: int = Field(ge=1, le=30)


class LinksCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    website: Optional[str] = None
    studio: Optional[str] = None
    explorer: Optional[str] = None
    docs: Optional[str] = None


class Chain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: str
    name: str
    chainId: int = Field(ge=1)
    network: Literal["mainnet", "testnet", "localnet"]
    status: Literal["planned", "active", "dev", "deprecated"]
    testnet: bool

    nativeCurrency: NativeCurrency
    rpc: RpcCfg
    explorers: List[Explorer]
    faucets: Optional[List[Faucet]] = None
    p2p: P2P
    addresses: Addresses
    pq: PQCfg
    vm: VMCfg
    da: DACfg
    randomness: Optional[RandomnessCfg] = None
    genesis: GenesisCfg
    governance: GovernanceCfg
    links: Optional[LinksCfg] = None
    features: List[Literal["poies", "pq", "ai", "quantum", "da", "vm_py"]]

    checksum: str  # lowercase sha256


class Icons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    svg: Optional[str] = None
    svgDark: Optional[str] = None
    png64: Optional[str] = None
    png128: Optional[str] = None


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    name: str
    chainId: int = Field(ge=1)
    network: Literal["mainnet", "testnet", "localnet"]
    status: Literal["planned", "active", "dev", "deprecated"]
    testnet: bool
    path: str
    checksum: str  # or "<sha256-to-be-generated>" but we keep as str
    icons: Optional[Icons] = None


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: str
    generatedAt: str
    entries: List[RegistryEntry]


# ----------------------------- Helpers: JSON IO, hashing, checksums ----------------------------------------------

def read_json(path: os.PathLike[str] | str) -> Any:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON at {p}: {e}") from e


def sha256_hex(path: os.PathLike[str] | str | bytes) -> str:
    h = hashlib.sha256()
    if isinstance(path, (str, os.PathLike)):
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    else:
        h.update(path)
    return h.hexdigest()


def parse_checksums_file(checksums_path: os.PathLike[str] | str) -> Dict[str, str]:
    """
    Parse 'chains/checksums.txt' lines formatted as:
      <sha256>  <path>
    Returns a dict { path -> sha256 }.
    """
    mp: Dict[str, str] = {}
    for raw in Path(checksums_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            # Join the rest back to path in case of spaces (shouldn't be, but be robust)
            mp[" ".join(parts[1:])] = parts[0].lower()
    return mp


# ----------------------------- Schema validation (optional extra guard) ------------------------------------------

def _maybe_jsonschema_validate(instance: Any, schema_path: Path) -> None:
    if not _HAS_JSONSCHEMA:
        return
    schema = read_json(schema_path)
    try:
        jsonschema.validate(instance=instance, schema=schema)  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ValueError(f"jsonschema validation failed for {schema_path.name}: {e}") from e


# ----------------------------- Public API: load/validate ----------------------------------------------------------

def load_chain(file_path: os.PathLike[str] | str) -> Chain:
    """
    Load and validate a single chain JSON.
    Validates with Pydantic (required) and optionally jsonschema if installed.
    """
    p = Path(file_path)
    data = read_json(p)
    # Optional extra guard against schema drift:
    schema_path = Path("chains/schemas/chain.schema.json")
    if schema_path.exists():
        _maybe_jsonschema_validate(data, schema_path)
    try:
        return Chain.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"{p} failed Pydantic validation:\n{e}") from e


def load_registry(file_path: os.PathLike[str] | str) -> Registry:
    p = Path(file_path)
    data = read_json(p)
    schema_path = Path("chains/schemas/registry.schema.json")
    if schema_path.exists():
        _maybe_jsonschema_validate(data, schema_path)
    try:
        return Registry.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"{p} failed Pydantic validation:\n{e}") from e


def resolve_from_registry(registry_path: os.PathLike[str] | str, key: str) -> Tuple[RegistryEntry, Chain]:
    reg = load_registry(registry_path)
    entry = next((e for e in reg.entries if e.key == key), None)
    if not entry:
        raise KeyError(f"registry key not found: {key}")
    chain = load_chain(entry.path)
    return entry, chain


# ----------------------------- Checksums verification -------------------------------------------------------------

@dataclass
class CheckResult:
    path: str
    ok: bool
    reason: Optional[str]
    file_hash: str
    list_hash: Optional[str]
    embedded: Optional[str]


def verify_against_checksums(
    checksums_path: os.PathLike[str] | str,
    files: Optional[List[str]] = None
) -> List[CheckResult]:
    """
    Verify that each file's sha256 equals the signed list, and (if present)
    that the JSON's embedded "checksum" equals the list value.
    """
    checksums = parse_checksums_file(checksums_path)
    targets = files if files else list(checksums.keys())
    results: List[CheckResult] = []

    for path in targets:
        p = Path(path)
        if not p.exists():
            results.append(CheckResult(str(p), False, "missing file", "", checksums.get(str(p)), None))
            continue

        file_hash = sha256_hex(p)
        list_hash = checksums.get(str(p))
        embedded: Optional[str] = None
        try:
            obj = read_json(p)
            embedded = obj.get("checksum") if isinstance(obj, dict) else None  # type: ignore[assignment]
        except Exception:
            embedded = None

        if not list_hash:
            results.append(CheckResult(str(p), False, "no entry in checksums.txt", file_hash, None, embedded))
            continue

        ok = (file_hash.lower() == list_hash.lower()) and (embedded is None or embedded.lower() == list_hash.lower())
        reason = None if ok else "hash mismatch (file and/or embedded)"
        results.append(CheckResult(str(p), ok, reason, file_hash, list_hash, embedded))

    return results


# ----------------------------- __all__ & CLI ---------------------------------------------------------------------

__all__ = [
    "Chain",
    "Registry",
    "RegistryEntry",
    "load_chain",
    "load_registry",
    "resolve_from_registry",
    "verify_against_checksums",
    "parse_checksums_file",
    "sha256_hex",
]

def _cli() -> int:
    args = sys.argv[1:]
    if not args:
        print("Usage:\n  python -m animica_chains check <chains/checksums.txt>\n  python -m animica_chains show <registry-key>", file=sys.stderr)
        return 2

    cmd = args[0]
    try:
        if cmd == "check":
            checksums = args[1] if len(args) > 1 else "chains/checksums.txt"
            results = verify_against_checksums(checksums)
            bad = [r for r in results if not r.ok]
            for r in results:
                print(f"{'OK  ' if r.ok else 'FAIL'} {r.path} file={r.file_hash} list={r.list_hash or '-'} embedded={r.embedded or '-'}{'' if r.ok else f'  # {r.reason}'}")
            return 1 if bad else 0
        elif cmd == "show":
            key = args[1] if len(args) > 1 else "animica-testnet"
            entry, chain = resolve_from_registry("chains/registry.json", key)
            print(json.dumps({"entry": entry.model_dump(), "chain": chain.model_dump()}, indent=2))
            return 0
        else:
            print("Unknown command. Use 'check' or 'show'.", file=sys.stderr)
            return 2
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
