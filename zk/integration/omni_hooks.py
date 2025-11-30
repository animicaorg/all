# zk/integration/omni_hooks.py
"""
omni_hooks: entry points for omni/crypto/zk_verify.py

This module exposes a tiny, stable API that omni's crypto layer can call to
verify zero-knowledge proofs using the Animica zk integration stack.

Primary entry:
    zk_verify(payload: dict, policy_path: Optional[str|Path] = None) -> dict

Inputs (payload):
    {
      # Preferred: a canonical ProofEnvelope (see zk.integration.types.ProofEnvelope)
      "envelope": {...},

      # Optional fast-path: estimate cost only (no cryptographic checks)
      "meter_only": false
    }

Return shape:
    {
      "ok": true|false,
      "units": <int>,               # deterministic metered cost
      "kind": "<string>",           # verifier kind
      "circuit_id": "<string|None>",
      "error": { "code": "<str>", "message": "<str>" } | None,
      "meta": { ... }               # passthrough/derived metadata (sizes, hashes)
    }

Notes
-----
- Metering (units) is returned even when verification fails, as long as basic
  validation & policy checks passed; if not, units may be 0.
- Policy can be overridden by passing `policy_path` (JSON or YAML).
- Public inputs are **not** mutated; callers may normalize to hex their side.

This file is intentionally self-contained and narrow in what it imports from the
rest of the zk/ package to keep the plugin surface stable.
"""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

# Local deps (stable, small surface)
from zk.integration import verify as _verify_envelope_call
from zk.integration.policy import (DEFAULT_POLICY, LimitExceeded,
                                   NotAllowedCircuit, Policy, PolicyError,
                                   check_and_meter, load_policy)
from zk.integration.types import ProofEnvelope, canonical_json_bytes
from zk.registry import list_kinds as _list_kinds

# Adapter error types (optional)
try:
    from zk.adapters.omni_bridge import OmniError  # type: ignore
except Exception:  # pragma: no cover

    class OmniError(Exception):  # fallback shim
        pass


__all__ = [
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "HookErrorCode",
    "zk_verify",
    "get_supported_kinds",
]

PLUGIN_NAME = "zk.omni_hooks"
PLUGIN_VERSION = "0.1.0"


class HookErrorCode(str, Enum):
    BAD_ARGUMENTS = "BAD_ARGUMENTS"
    NOT_ALLOWED = "NOT_ALLOWED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    REGISTRY_ERROR = "REGISTRY_ERROR"
    IMPORT_FAILURE = "IMPORT_FAILURE"
    ADAPTER_ERROR = "ADAPTER_ERROR"
    VERIFY_FAILED = "VERIFY_FAILED"
    UNKNOWN = "UNKNOWN"


def get_supported_kinds() -> Dict[str, Any]:
    """
    Return a small capability descriptor for discovery / telemetry.
    """
    return {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "kinds": sorted(_list_kinds()),
    }


def _resolve_policy(policy_path: Optional[Path | str]) -> Policy:
    if policy_path is None:
        return DEFAULT_POLICY
    return load_policy(Path(policy_path))


def _resolve_circuit_id(env: ProofEnvelope) -> Optional[str]:
    meta_cid = None
    if isinstance(env.meta, dict):
        meta_cid = env.meta.get("circuit_id")
    if isinstance(meta_cid, str) and meta_cid:
        return meta_cid
    if isinstance(env.vk_ref, str) and env.vk_ref:
        return env.vk_ref
    return None


def _size_bytes(obj: Any) -> int:
    return len(canonical_json_bytes(obj))


def zk_verify(
    payload: Dict[str, Any], policy_path: Optional[str | Path] = None
) -> Dict[str, Any]:
    """
    Main hook for omni/crypto/zk_verify.py.

    Args:
        payload: dict containing either:
                 - "envelope": mapping compatible with ProofEnvelope
                 - optional "meter_only": bool (default False)
        policy_path: optional JSON/YAML policy override path

    Returns:
        dict result (see module docstring).
    """
    policy = _resolve_policy(policy_path)

    # Prepare base response
    result: Dict[str, Any] = {
        "ok": False,
        "units": 0,
        "kind": None,
        "circuit_id": None,
        "error": None,
        "meta": {},
    }

    try:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        env_raw = payload.get("envelope")
        if not isinstance(env_raw, dict):
            raise ValueError("payload['envelope'] is required and must be an object")

        # Validate/normalize via msgspec (strict field set; will coerce types where possible)
        env = ProofEnvelope(**env_raw)  # type: ignore[arg-type]

        result["kind"] = env.kind
        result["circuit_id"] = _resolve_circuit_id(env)

        # Sizes for telemetry/metering
        proof_bytes = _size_bytes(env.proof)
        vk_bytes = _size_bytes(env.vk) if env.vk is not None else 0
        num_pub = len(env.public_inputs or [])
        result["meta"] = {
            "proof_bytes": proof_bytes,
            "vk_bytes": vk_bytes,
            "num_public_inputs": num_pub,
        }

        # Policy enforcement + deterministic metering
        units = check_and_meter(
            env, policy=policy, kzg_openings_hint=payload.get("kzg_openings_hint")
        )
        result["units"] = int(units)

        # Fast path: caller only needs price quote
        if bool(payload.get("meter_only", False)):
            result["ok"] = True
            return result

        # Full verification
        ok = _verify_envelope_call(envelope=asdict(env))  # uses zk.integration.verify
        if not ok:
            result["error"] = {
                "code": HookErrorCode.VERIFY_FAILED,
                "message": "Verifier returned false",
            }
            return result

        # Success
        result["ok"] = True
        return result

    except NotAllowedCircuit as e:
        result["error"] = {"code": HookErrorCode.NOT_ALLOWED, "message": str(e)}
        return result
    except LimitExceeded as e:
        result["error"] = {"code": HookErrorCode.LIMIT_EXCEEDED, "message": str(e)}
        return result
    except PolicyError as e:
        result["error"] = {"code": HookErrorCode.BAD_ARGUMENTS, "message": str(e)}
        return result
    except OmniError as e:
        # Adapter/bridge mapping problems
        result["error"] = {"code": HookErrorCode.ADAPTER_ERROR, "message": str(e)}
        return result
    except (ImportError, ModuleNotFoundError) as e:
        result["error"] = {"code": HookErrorCode.IMPORT_FAILURE, "message": str(e)}
        return result
    except Exception as e:  # last resort, keep stable shape
        result["error"] = {"code": HookErrorCode.UNKNOWN, "message": str(e)}
        return result


# ---------------------------
# Tiny CLI for local testing
# ---------------------------


def _cli(argv: Optional[list[str]] = None) -> None:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="omni_hooks zk_verify CLI")
    ap.add_argument("payload", help="Path to JSON payload with 'envelope'")
    ap.add_argument("--policy", help="Optional policy JSON/YAML path")
    args = ap.parse_args(argv)

    with open(args.payload, "r", encoding="utf-8") as f:
        payload = json.load(f)

    res = zk_verify(payload, policy_path=args.policy)
    json.dump(res, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":  # pragma: no cover
    _cli()
