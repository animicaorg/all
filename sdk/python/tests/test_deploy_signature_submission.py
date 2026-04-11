from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cbor2
import pytest

from animica.tx.signing import ChainContext, pq_verify_tx, tx_signing_preimage
from core.utils.tx import normalize_tx_envelope
from omni_sdk.contracts.deployer import build_deploy_tx, deploy_package, make_package_bytes
from omni_sdk.errors import RpcError
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import signing as tx_signing
from omni_sdk.wallet.signer import PQSigner
from pq.py.sign import Signature


CHAIN_ID = 1
FORK_ID = 7
GENESIS_HASH = bytes.fromhex("11" * 32)
NETWORK = "sdk-signing-test"
ALG_NAME_BY_ID = {
    4097: "dilithium3",
    4098: "sphincs_shake_128s",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _counter_manifest_and_code() -> tuple[dict[str, Any], bytes]:
    root = _repo_root()
    manifest = json.loads(
        (root / "vm_py" / "examples" / "counter" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    code = (root / "vm_py" / "examples" / "counter" / "contract.py").read_bytes()
    return manifest, code


class _VerifyingRpc:
    """
    Fake RPC endpoint that applies the same signing-preimage verification path
    as the node before accepting tx.sendRawTransaction.
    """

    def __init__(self) -> None:
        self.raw_txs: list[bytes] = []
        self.tx_hashes: list[str] = []

    def _chain_identity(self) -> dict[str, Any]:
        return {
            "chainId": CHAIN_ID,
            "forkId": FORK_ID,
            "genesisHash": "0x" + GENESIS_HASH.hex(),
            "network": NETWORK,
        }

    def _verify_submission_raw(self, raw: bytes) -> None:
        env = cbor2.loads(raw)
        sig_map = env.get("sig", {})
        pub = sig_map.get("pubkey") or sig_map.get("pk") or sig_map.get("pub")
        sig_bytes = sig_map.get("sig")
        alg_id = int(sig_map.get("algId"))

        sig_env = Signature(
            alg_id=alg_id,
            alg_name=ALG_NAME_BY_ID.get(alg_id, f"alg_{alg_id}"),
            domain="tx",
            prehash="sha3-512",
            sig=bytes(sig_bytes),
        )
        ctx = ChainContext(
            chain_id=CHAIN_ID,
            genesis_hash=GENESIS_HASH,
            network=NETWORK,
            fork_id=FORK_ID,
            domain="tx",
            prehash="sha3-512",
        )
        normalized = normalize_tx_envelope(raw)
        verify = pq_verify_tx(normalized, sig_env, bytes(pub), ctx)
        if not verify.ok:
            raise RpcError(
                code=-32012,
                message="Invalid post-quantum signature: verification failed",
                method="tx.sendRawTransaction",
                data={"sign_hash": verify.sign_hash_hex, "reason": verify.reason},
            )

    def request(self, method: str, params: list[Any] | None = None) -> Any:
        params = params or []
        if method == "chain.getChainIdentity":
            return self._chain_identity()
        if method == "chain.getHead":
            return {"height": 123}
        if method == "tx.sendRawTransaction":
            raw = params[0]
            if isinstance(raw, str):
                raw = bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)
            raw = bytes(raw)
            self._verify_submission_raw(raw)
            tx_hash = "0x" + hashlib.sha3_256(raw).hexdigest()
            self.raw_txs.append(raw)
            self.tx_hashes.append(tx_hash)
            return tx_hash
        if method == "tx.getTransactionReceipt":
            tx_hash = params[0]
            return {
                "txHash": tx_hash,
                "status": "ok",
                "contractAddress": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j",
            }
        raise AssertionError(f"unexpected RPC method: {method}")


@pytest.fixture(autouse=True)
def _enable_test_pq_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")


@pytest.mark.parametrize("alg_name", ["sphincs_shake_128s", "dilithium3"])
def test_deploy_package_submission_signature_verifies_node_path(alg_name: str) -> None:
    """
    Regression test: deploy-package signatures must pass node preimage verify.

    Before the fix, deploy_package signed legacy body bytes and this test raised
    RpcError(-32012). After the fix, the fake node accepts the transaction.
    """

    signer = PQSigner.from_seed(alg_name, seed=bytes(range(32)))
    rpc = _VerifyingRpc()
    manifest, code = _counter_manifest_and_code()

    _addr, result = deploy_package(
        rpc=rpc,
        signer=signer,
        manifest=manifest,
        code=code,
        chain_id=CHAIN_ID,
        nonce=2,
        max_fee=1,
        await_receipt=False,
    )

    assert isinstance(result.get("txHash"), str)
    assert len(rpc.raw_txs) == 1
    body = cbor2.loads(rpc.raw_txs[0]).get("body", {})
    assert "validAfter" in body
    assert "validUntil" in body
    assert "salt" in body
    assert "nonce" not in body
    assert int(body["validUntil"]) > int(body["validAfter"])
    payload = body.get("payload", {})
    assert payload.get("t") == 1
    payload_v = payload.get("v", {})
    assert isinstance(payload_v.get("code"), (bytes, bytearray))
    assert isinstance(payload_v.get("manifest"), (bytes, bytearray))
    assert len(payload_v["code"]) > 0
    assert len(payload_v["manifest"]) > 0


@pytest.mark.parametrize("tx_kind", ["transfer", "deploy"])
def test_submission_preimage_matches_after_envelope_normalization(tx_kind: str) -> None:
    """
    Ensure body shape is not effectively mutated after signing/submission.

    We assert the preimage signed by SDK equals the preimage reconstructed from
    the serialized envelope using node normalization rules.
    """

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    rpc = _VerifyingRpc()
    ctx = tx_signing.resolve_signing_context(rpc, chain_id=CHAIN_ID)

    if tx_kind == "transfer":
        tx = tx_build.transfer(
            from_addr=signer.address or "anim1sender",
            to_addr=signer.address or "anim1receiver",
            amount=1,
            nonce=0,
            gas_limit=21_000,
            max_fee=1,
            chain_id=CHAIN_ID,
        )
    else:
        manifest, code = _counter_manifest_and_code()
        package = make_package_bytes(manifest=manifest, code=code)
        tx = build_deploy_tx(
            from_addr=signer.address or "anim1sender",
            chain_id=CHAIN_ID,
            nonce=2,
            max_fee=1,
            package_bytes=package,
        )

    signed = tx_signing.sign_transaction_for_submission(tx, signer, context=ctx)
    normalized = normalize_tx_envelope(signed.raw_tx)

    pre_sign = tx_signing.build_submission_sign_bytes(tx, ctx)
    pre_verify = tx_signing_preimage(
        normalized,
        chain_id=CHAIN_ID,
        genesis=GENESIS_HASH,
        network=NETWORK,
    )
    assert pre_sign == pre_verify

    # Also validate full signature verify on the normalized envelope.
    sig_map = cbor2.loads(signed.raw_tx)["sig"]
    sig_env = Signature(
        alg_id=int(sig_map["algId"]),
        alg_name="sphincs_shake_128s",
        domain="tx",
        prehash="sha3-512",
        sig=bytes(sig_map["sig"]),
    )
    verify = pq_verify_tx(
        normalized,
        sig_env,
        bytes(sig_map["pubkey"]),
        ChainContext(
            chain_id=CHAIN_ID,
            genesis_hash=GENESIS_HASH,
            network=NETWORK,
            fork_id=FORK_ID,
            domain="tx",
            prehash="sha3-512",
        ),
    )
    assert verify.ok, verify
