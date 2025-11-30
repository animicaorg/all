"""
omni_sdk.contracts.deployer
===========================

Deploy a Python-VM contract package (manifest + code bytes).

This module:
- Validates & normalizes the manifest (ABI normalized)
- Builds a deploy transaction (to=None, data=CBOR{manifest, code[, resources]})
- Signs with a PQ signer
- Submits via RPC and waits for the receipt
- Extracts and returns the deployed contract address (if provided by node)

Typical usage
-------------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.wallet.signer import Dilithium3Signer
    from omni_sdk.contracts.deployer import deploy_package

    rpc = HttpClient("http://127.0.0.1:8545")
    signer = Dilithium3Signer.from_mnemonic("... 24 words ...")

    addr, receipt = deploy_package(
        rpc=rpc,
        signer=signer,
        manifest=manifest_dict,
        code=code_bytes,
        chain_id=1,
        nonce=0,
        max_fee=2_000_000,
    )

Design notes
------------
* The deploy "data" payload is canonical CBOR of:
      { "manifest": <object>, "code": <bytes>, "resources": <optional map> }
  using the SDK's deterministic CBOR encoder.
* Gas estimation uses the same intrinsic+overhead heuristic as `tx.build`.
* Contract address is returned from receipt if the node provides it. If not,
  the function returns `(None, receipt)`.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

# --- SDK imports --------------------------------------------------------------

# Deterministic CBOR
try:
    from omni_sdk.utils.cbor import dumps as cbor_dumps  # type: ignore
except Exception as _e:  # pragma: no cover
    raise RuntimeError(
        "omni_sdk.utils.cbor is required for deterministic encoding"
    ) from _e

# ABI helpers (normalize & validate ABI inside manifest)
try:
    from omni_sdk.types.abi import normalize_abi  # type: ignore
except Exception as _e:  # pragma: no cover
    raise RuntimeError(
        "omni_sdk.types.abi is required to normalize manifest ABI"
    ) from _e

# Tx lifecycle
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import encode as tx_encode
from omni_sdk.tx import send as tx_send
# PQ signer
from omni_sdk.wallet.signer import PQSigner  # type: ignore

# Errors
try:
    from omni_sdk.errors import (AbiError, RpcError, TxError,  # type: ignore
                                 VerifyError)
except Exception:  # pragma: no cover

    class RpcError(RuntimeError): ...

    class TxError(RuntimeError): ...

    class AbiError(RuntimeError): ...

    class VerifyError(RuntimeError): ...


JsonDict = Dict[str, Any]


# --- Manifest helpers ---------------------------------------------------------


def normalize_manifest(manifest: Mapping[str, Any]) -> JsonDict:
    """
    Basic manifest normalization:
      - Ensure it is a dict
      - Normalize/validate embedded ABI (manifest['abi'])
    """
    if not isinstance(manifest, Mapping):
        raise AbiError("manifest must be a mapping")

    m = dict(manifest)

    # ABI is required for deploy bundles in this SDK
    if "abi" not in m:
        raise AbiError("manifest missing required 'abi' field")
    try:
        m["abi"] = normalize_abi(m["abi"])
    except Exception as e:
        raise AbiError(f"manifest.abi normalization failed: {e}") from e

    # Optional light normalization for common fields
    if "name" in m and not isinstance(m["name"], str):
        raise AbiError("manifest.name must be a string if provided")
    if "version" in m and not isinstance(m["version"], (str, int)):
        raise AbiError("manifest.version must be string or int if provided")

    return m


def make_package_bytes(
    *,
    manifest: Mapping[str, Any],
    code: bytes,
    resources: Optional[Mapping[str, bytes]] = None,
) -> bytes:
    """
    Build the deploy package bytes (CBOR) from manifest, code, and optional resources.
    """
    if not isinstance(code, (bytes, bytearray)):
        raise TypeError("code must be bytes")

    env: JsonDict = {
        "manifest": normalize_manifest(manifest),
        "code": bytes(code),
    }
    if resources:
        # Ensure bytes-like resource contents
        env["resources"] = {str(k): bytes(v) for k, v in resources.items()}
    return cbor_dumps(env)


# --- Tx builders --------------------------------------------------------------


def _have_tx_deploy() -> bool:
    try:
        # Feature-detect presence of tx.build.deploy
        _ = tx_build.deploy  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def build_deploy_tx(
    *,
    from_addr: str,
    chain_id: int,
    nonce: int,
    max_fee: int,
    package_bytes: bytes,
    gas_limit: Optional[int] = None,
):
    """
    Construct a deploy transaction object (dataclass or dict).
    """
    gl = (
        int(gas_limit)
        if gas_limit is not None
        else tx_build.suggest_gas_limit(
            kind="deploy",
            calldata_len=len(package_bytes),
        )
    )

    if _have_tx_deploy():
        # Preferred: dedicated deploy builder (to=None)
        return tx_build.deploy(  # type: ignore[attr-defined]
            from_addr=from_addr,
            nonce=int(nonce),
            gas_limit=int(gl),
            max_fee=int(max_fee),
            chain_id=int(chain_id),
            package=package_bytes,  # expected param name in our builder
        )

    # Fallback: use call() with to=None, data=package
    return tx_build.call(
        from_addr=from_addr,
        to_addr=None,
        data=package_bytes,
        nonce=int(nonce),
        gas_limit=int(gl),
        max_fee=int(max_fee),
        chain_id=int(chain_id),
        value=0,
    )


# --- Deploy orchestrator ------------------------------------------------------


def extract_contract_address(receipt: Mapping[str, Any]) -> Optional[str]:
    """
    Try to read a contract address from a receipt using common keys.
    """
    for k in ("contractAddress", "contract_address", "address"):
        v = receipt.get(k)  # type: ignore[arg-type]
        if isinstance(v, str) and v:
            return v
    # Optionally inspect logs for a creation event (implementation-specific). No-op here.
    return None


def deploy_package(
    *,
    rpc,
    signer: PQSigner,
    manifest: Mapping[str, Any],
    code: bytes,
    chain_id: int,
    nonce: int,
    max_fee: int,
    gas_limit: Optional[int] = None,
    resources: Optional[Mapping[str, bytes]] = None,
    await_receipt: bool = True,
    timeout_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> Tuple[Optional[str], JsonDict]:
    """
    Deploy a contract package and return (contract_address | None, receipt).

    Parameters
    ----------
    rpc : omni_sdk.rpc.http client (must expose .call)
    signer : PQSigner for the sender (provides .address/.alg_id/.public_key/.sign)
    manifest : mapping conforming to spec/manifest.schema.json (at minimum includes 'abi')
    code : compiled contract bytes
    chain_id : integer chain id
    nonce : sender nonce to use for deployment
    max_fee : fee cap
    gas_limit : optional explicit gas limit; if None, a suggestion is computed
    resources : optional map of name -> bytes (additional artifacts)
    await_receipt : if False, returns immediately after submit with (None, {"txHash": ...})

    Returns
    -------
    (address | None, receipt_dict)
    """
    pkg = make_package_bytes(manifest=manifest, code=code, resources=resources)
    tx_obj = build_deploy_tx(
        from_addr=signer.address,
        chain_id=int(chain_id),
        nonce=int(nonce),
        max_fee=int(max_fee),
        package_bytes=pkg,
        gas_limit=gas_limit,
    )

    signbytes = tx_encode.sign_bytes(tx_obj)
    signature = signer.sign(signbytes)
    raw = tx_encode.pack_signed(
        tx_obj,
        signature=signature,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )

    if not await_receipt:
        tx_hash = tx_send.submit_raw(rpc, raw)
        return None, {"txHash": tx_hash}

    receipt = tx_send.submit_and_wait(
        rpc,
        raw,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )
    addr = extract_contract_address(receipt)
    return addr, receipt


__all__ = [
    "normalize_manifest",
    "make_package_bytes",
    "build_deploy_tx",
    "deploy_package",
    "extract_contract_address",
]
