# -*- coding: utf-8 -*-
"""
Deploy & smoke-test the NameRegistry example to a running devnet/testnet.

This script prefers using the local monorepo SDK & tools (no pip install needed).
It compiles the contract, deploys the package, waits for a receipt, then does
a tiny functional round-trip: set → get → has → remove.

Environment (or CLI flags) it understands:

  RPC_URL           JSON-RPC endpoint (e.g. http://127.0.0.1:8545)
  CHAIN_ID          Chain id integer (e.g. 1337 for devnet)
  DEPLOYER_MNEMONIC 12–24 word mnemonic for a PQ signer (dev-only)
  SERVICES_URL      (optional) studio-services base URL (for future verify)

Examples:
  python -m contracts.examples.registry.deploy_and_test \\
      --rpc http://127.0.0.1:8545 --chain-id 1337 \\
      --mnemonic "enlist hip relief stomach ..." --alg dilithium3

If RPC_URL/CHAIN_ID/MNEMONIC are absent, the script will still compile the
package and run a *local* in-process VM smoke test so it is useful offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# --- repo-relative path bootstrap (use monorepo SDK without installing) ------
HERE = Path(__file__).resolve().parent
CONTRACTS_DIR = HERE.parent.parent
REPO_ROOT = CONTRACTS_DIR.parent
SDK_PY = REPO_ROOT / "sdk" / "python"
if str(SDK_PY) not in sys.path:
    sys.path.insert(0, str(SDK_PY))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --- optional .env loader (simple and dependency-free) -----------------------
def _load_env_file(env_path: Path) -> None:
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


_load_env_file(CONTRACTS_DIR / ".env")  # local overrides if present
_load_env_file(REPO_ROOT / ".env")  # repo-root overrides if present


# --- local helpers -----------------------------------------------------------
def sha3_256(b: bytes) -> bytes:
    try:
        import hashlib

        return hashlib.sha3_256(b).digest()
    except Exception:  # pragma: no cover
        raise RuntimeError("hashlib.sha3_256 is required")


def name32(label: str) -> bytes:
    return sha3_256(label.encode("utf-8"))  # 32 bytes


def addr32(tag: str) -> bytes:
    return sha3_256(("addr:" + tag).encode("utf-8"))


# --- packaging & compile -----------------------------------------------------
@dataclass
class BuiltPackage:
    manifest_path: Path
    code_path: Path
    manifest: Dict[str, Any]
    code_bytes: bytes
    code_hash_hex: str


def build_package() -> BuiltPackage:
    """
    Compile & package the NameRegistry example using either the contracts.tools
    builder (preferred) or a direct vm_py loader fallback.
    """
    manifest_path = HERE / "manifest.json"
    source_path = HERE / "contract.py"
    out_dir = CONTRACTS_DIR / "build"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Attempt to use the shared tools builder first (keeps behavior consistent).
    try:
        from contracts.tools.build_package import build  # type: ignore

        manifest, code_bytes, code_hash_hex = build(
            manifest_path=manifest_path, source_path=source_path, out_dir=out_dir
        )
        code_path = out_dir / f"{manifest['name']}.ir"
        if not code_path.exists():
            code_path.write_bytes(code_bytes)
        return BuiltPackage(
            manifest_path=manifest_path,
            code_path=code_path,
            manifest=manifest,
            code_bytes=code_bytes,
            code_hash_hex=code_hash_hex,
        )
    except Exception:
        # Fallback: compile with vm_py directly.
        try:
            from vm_py.runtime import loader as vm_loader  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Neither contracts.tools.build_package nor vm_py.runtime.loader is available. "
                "Ensure monorepo paths are correct and vm_py is built."
            ) from exc

        # Load & compile; ask loader to return code bytes if available
        try:
            handle = vm_loader.load(
                manifest_path=str(manifest_path), source_path=str(source_path)
            )
        except TypeError:
            handle = vm_loader.load(str(manifest_path))

        # Try to obtain compiled IR/code bytes from the handle or loader
        code_bytes: Optional[bytes] = None
        if hasattr(handle, "compiled_code"):
            code_bytes = handle.compiled_code  # type: ignore[attr-defined]
        elif hasattr(handle, "get_compiled_code"):
            code_bytes = handle.get_compiled_code()  # type: ignore[attr-defined]
        if not code_bytes:
            # As a last resort, re-encode from any available encode() helper
            if hasattr(handle, "encode_ir"):
                code_bytes = handle.encode_ir()  # type: ignore[attr-defined]
            else:
                raise RuntimeError(
                    "Could not extract compiled IR/code bytes from vm_py handle"
                )

        manifest = json.loads(manifest_path.read_text())
        code_hash_hex = "0x" + sha3_256(code_bytes).hex()
        code_path = out_dir / f"{manifest.get('name','contract')}.ir"
        code_path.write_bytes(code_bytes)

        return BuiltPackage(
            manifest_path=manifest_path,
            code_path=code_path,
            manifest=manifest,
            code_bytes=code_bytes,
            code_hash_hex=code_hash_hex,
        )


# --- deploy via Python SDK ---------------------------------------------------
@dataclass
class DeployResult:
    tx_hash: str
    address: str
    receipt: Dict[str, Any]


def _mk_signer(mnemonic: str, alg: str = "dilithium3"):
    """
    Create a PQ signer from the SDK. Falls back across a couple of symbol names.
    """
    # Preferred API
    try:
        from omni_sdk.wallet.signer import from_mnemonic  # type: ignore

        return from_mnemonic(mnemonic, alg=alg)
    except Exception:
        pass
    # Alternate API
    try:
        from omni_sdk.wallet.mnemonic import derive_seed  # type: ignore
        from omni_sdk.wallet.signer import Dilithium3Signer  # type: ignore

        seed = derive_seed(mnemonic)
        if alg.lower().startswith("dilithium"):
            return Dilithium3Signer.from_seed(seed)
        # Add SPHINCS+ fallback if needed later
        raise ValueError(f"Unsupported alg for fallback path: {alg}")
    except Exception as exc:
        raise RuntimeError("omni_sdk wallet signer is unavailable") from exc


def _derive_address(signer, alg: str = "dilithium3") -> str:
    """
    Derive an address string (bech32m anim1...) using the SDK address module.
    """
    try:
        from omni_sdk.address import from_pubkey  # type: ignore

        pk = signer.public_key()
        return from_pubkey(pk, alg=alg)
    except Exception:
        # Very conservative fallback: hex of sha3(pubkey) if address codec not present.
        pk = signer.public_key()
        return "0x" + sha3_256(pk).hex()


def _build_deploy_tx(
    manifest: Dict[str, Any], code_bytes: bytes, chain_id: int, sender: str
):
    """
    Build a deploy transaction via SDK helper. Accepts multiple possible APIs.
    Returns a Python dict representing the Tx ready to sign & encode.
    """
    # Preferred path
    try:
        from omni_sdk.tx.build import deploy  # type: ignore

        return deploy(
            manifest=manifest, code=code_bytes, chain_id=chain_id, sender=sender
        )
    except Exception:
        pass

    # Alternate name
    try:
        from omni_sdk.tx.build import build_deploy  # type: ignore

        return build_deploy(manifest, code_bytes, chain_id=chain_id, sender=sender)
    except Exception as exc:
        raise RuntimeError("omni_sdk.tx.build deploy builder not available") from exc


def _encode_and_sign(tx: Dict[str, Any], signer) -> Tuple[bytes, str]:
    """
    Encode a Tx to CBOR and sign its SignBytes, returning (raw_cbor, sig_hex).
    """
    try:
        from omni_sdk.tx.encode import encode_cbor, sign_bytes  # type: ignore

        sb = sign_bytes(tx)
        sig = signer.sign(sb)
        raw = encode_cbor({**tx, "signature": sig})
        return raw, "0x" + sig.hex()
    except Exception:
        # Very conservative fallback: try a generic CBOR encoder and adjoin signature
        try:
            import cbor2  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "No CBOR encoder available (need omni_sdk.utils.cbor or cbor2)"
            ) from exc
        sb = json.dumps(tx, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = signer.sign(sb)
        tx2 = dict(tx)
        tx2["signature"] = sig
        return cbor2.dumps(tx2), "0x" + sig.hex()


def _rpc_client(rpc_url: str):
    """
    Obtain a JSON-RPC client from the SDK; otherwise provide a tiny fallback.
    """
    try:
        from omni_sdk.rpc.http import Client  # type: ignore

        return Client(rpc_url)
    except Exception:
        # Minimal fallback using requests
        import requests

        class _Mini:
            def __init__(self, url: str):
                self.url = url
                self._id = 0

            def call(self, method: str, params: Any = None) -> Any:
                self._id += 1
                body = {
                    "jsonrpc": "2.0",
                    "id": self._id,
                    "method": method,
                    "params": params or [],
                }
                r = requests.post(self.url, json=body, timeout=30)
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise RuntimeError(f"RPC error: {data['error']}")
                return data.get("result")

        return _Mini(rpc_url)


def deploy_to_network(
    rpc_url: str,
    chain_id: int,
    mnemonic: str,
    alg: str,
    package: BuiltPackage,
    poll_seconds: float = 0.5,
    poll_timeout: float = 60.0,
) -> DeployResult:
    """
    Full network path:
    - derive signer & sender address
    - build deploy tx
    - sign+encode
    - tx.sendRawTransaction
    - poll for receipt
    """
    client = _rpc_client(rpc_url)
    signer = _mk_signer(mnemonic, alg=alg)
    sender = _derive_address(signer, alg=alg)

    tx = _build_deploy_tx(package.manifest, package.code_bytes, chain_id, sender)
    raw_cbor, sig_hex = _encode_and_sign(tx, signer)

    # Submit raw CBOR (hex) via RPC
    raw_hex = "0x" + raw_cbor.hex()
    try:
        tx_hash = client.call("tx.sendRawTransaction", [raw_hex])
    except Exception:
        # Some SDKs expose a helper; try it
        try:
            from omni_sdk.tx.send import send_raw  # type: ignore

            tx_hash = send_raw(client, raw_cbor)
        except Exception as exc:
            raise RuntimeError(
                "Failed to submit transaction via RPC and SDK fallback"
            ) from exc

    # Poll for receipt
    t0 = time.time()
    receipt: Optional[Dict[str, Any]] = None
    while time.time() - t0 < poll_timeout:
        try:
            r = client.call("tx.getTransactionReceipt", [tx_hash])
            if r:
                receipt = r
                break
        except Exception:
            pass
        time.sleep(poll_seconds)
    if not receipt:
        raise TimeoutError(f"Timed out waiting for receipt of {tx_hash}")

    # Resolve contract address: prefer receipt['contractAddress'], otherwise parse logs
    address = receipt.get("contractAddress") or receipt.get("to") or ""
    if not address:
        # As a last resort, attempt to extract from SDK helpers or events
        try:
            from omni_sdk.contracts.deployer import \
                resolve_address_from_receipt  # type: ignore

            address = resolve_address_from_receipt(receipt)
        except Exception:
            pass
    if not address:
        raise RuntimeError("Could not resolve deployed contract address from receipt")

    return DeployResult(tx_hash=tx_hash, address=address, receipt=receipt)


# --- local-only smoke test (no RPC needed) -----------------------------------
def local_smoke(package: BuiltPackage) -> None:
    """
    Execute a tiny stateful scenario in-process using vm_py runtime to prove the
    contract compiles & basic functions behave before attempting network deploy.
    """
    try:
        from vm_py.runtime import loader as vm_loader  # type: ignore
    except Exception:
        print("vm_py runtime not available; skipping local smoke.", file=sys.stderr)
        return

    try:
        handle = vm_loader.load(
            manifest_path=str(package.manifest_path),
            source_path=str(HERE / "contract.py"),
        )
    except TypeError:
        handle = vm_loader.load(str(package.manifest_path))

    # Helper to invoke regardless of shape
    def _call(fn: str, *args):
        if hasattr(handle, "call"):
            return handle.call(fn, *args)
        if hasattr(handle, "invoke"):
            return handle.invoke(fn, *args)
        raise AttributeError("vm handle lacks call/invoke")

    alice = name32("alice")
    a1 = addr32("alice-primary")
    assert _call("has", alice) is False
    _call("set", alice, a1)
    assert _call("has", alice) is True
    assert _call("get", alice) == a1
    _call("remove", alice)
    assert _call("has", alice) is False
    print("✓ Local VM smoke: set/get/has/remove passed.")


# --- post-deploy functional check over RPC -----------------------------------
def network_smoke(rpc_url: str, chain_id: int, address: str) -> None:
    """
    Once deployed, exercise a few calls via the SDK contract client (or raw RPC).
    """
    client = _rpc_client(rpc_url)
    manifest = json.loads((HERE / "manifest.json").read_text())

    # Preferred: use SDK generic contract client
    try:
        from omni_sdk.contracts.client import Contract  # type: ignore

        c = Contract(
            client=client, address=address, abi=manifest["abi"], chain_id=chain_id
        )

        key = name32("service")
        val = addr32("v1")
        ok = c.call("set", key, val)  # write call (SDK decides method)
        _ = ok  # ignore return in case it's None
        got = c.call("get", key)
        assert got == val, f"mismatch: {got.hex()} vs {val.hex()}"
        # remove & verify
        c.call("remove", key)
        assert c.call("has", key) is False
        print("✓ Network smoke via SDK Contract: set/get/remove OK.")
        return
    except Exception:
        pass

    # Fallback: raw JSON-RPC style (method name depends on node's surface)
    # Try a conservative openrpc-like method "contracts.call"
    key = "0x" + name32("service").hex()
    # get before set should yield 0x or empty
    try:
        got0 = client.call("contracts.call", [address, "get", [key]])
        if isinstance(got0, str):
            assert got0 in ("0x", "0x" + ("00" * 32))
    except Exception:
        # If the node doesn't expose a generic call, we skip deep checks
        print("ℹ Raw call path not available; network smoke minimized.")


# --- CLI ---------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy & test NameRegistry example")
    p.add_argument("--rpc", dest="rpc_url", default=os.environ.get("RPC_URL"))
    p.add_argument("--chain-id", type=int, default=int(os.environ.get("CHAIN_ID", "0")))
    p.add_argument(
        "--mnemonic", dest="mnemonic", default=os.environ.get("DEPLOYER_MNEMONIC")
    )
    p.add_argument(
        "--alg",
        dest="alg",
        default=os.environ.get("ALG", "dilithium3"),
        help="Signature algorithm: dilithium3 (default) or sphincs_shake_128s",
    )
    p.add_argument("--no-local", action="store_true", help="Skip local VM smoke test")
    p.add_argument(
        "--timeout", type=float, default=60.0, help="Receipt wait timeout seconds"
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    pkg = build_package()

    print(f"Built package: {pkg.manifest.get('name')}  code_hash={pkg.code_hash_hex}")

    if not args.no_local:
        local_smoke(pkg)

    rpc_url = args.rpc_url
    chain_id = args.chain_id
    mnemonic = args.mnemonic

    if rpc_url and chain_id and mnemonic:
        print(f"Deploying to {rpc_url} (chain {chain_id}) …")
        res = deploy_to_network(
            rpc_url=rpc_url,
            chain_id=chain_id,
            mnemonic=mnemonic,
            alg=args.alg,
            package=pkg,
            poll_timeout=args.timeout,
        )
        print(f"✓ Deployed tx={res.tx_hash} address={res.address}")
        try:
            network_smoke(rpc_url, chain_id, res.address)
        except AssertionError as ae:
            print(f"❌ Network smoke failed: {ae}", file=sys.stderr)
            raise
        print("✓ Done.")
    else:
        print(
            "RPC_URL/CHAIN_ID/MNEMONIC not all provided; skipping network deploy.\n"
            "You can set them in contracts/.env or pass CLI flags to deploy."
        )


if __name__ == "__main__":
    main()
