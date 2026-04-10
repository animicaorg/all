"""CLI PQ signing alignment tests."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

# Some CLI submodules import optional dependencies like requests; provide a stub to
# avoid import-time failures during focused unit tests.
import sys
import types

SDK_ROOT = Path(__file__).resolve().parents[4] / "sdk" / "python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

try:
    import requests as _requests_mod
except Exception:  # pragma: no cover - fallback for minimal envs
    _requests_mod = types.SimpleNamespace()
sys.modules.setdefault("requests", _requests_mod)

from animica.cli import tx
from animica.tx.signing import build_signable_tx_bytes
from omni_sdk.tx.build import transfer
from omni_sdk.tx.signing import sign_transaction


runner = CliRunner()


def test_cli_sign_bytes_match_sdk_helper():
    """Ensure CLI uses the exact SDK sign-bytes when signing transactions."""

    class FakeSigner:
        alg_id = 4098
        public_key = b"pkbytes"

        def __init__(self) -> None:
            self.calls: list[tuple[bytes, int]] = []

        def sign_tx(self, message: bytes, chain_id: int) -> bytes:  # type: ignore[override]
            self.calls.append((message, chain_id))
            return b"sig" + message[:4] + chain_id.to_bytes(1, "big")

    signer = FakeSigner()
    tx_obj = transfer(
        from_addr="anim1source",
        to_addr="anim1dest",
        amount=1234,
        nonce=1,
        gas_limit=21000,
        max_fee=1_000_000_000,
        chain_id=1,
    )

    signed = sign_transaction(tx_obj, signer, chain_id=1)
    expected = build_signable_tx_bytes(tx_obj)
    # Golden value for regression coverage (domain separation applied by PQ layer)
    assert expected.hex() == (
        "a862746f69616e696d31646573746464617461406466726f6d6b616e696d31736f"
        "75726365656e6f6e6365016576616c75651904d2666d61784665651a3b9aca0067"
        "636861696e496401686761734c696d6974195208"
    )

    assert signer.calls and signer.calls[0][0] == expected
    assert signed.sign_bytes == expected
    assert signed.signature.startswith(b"sig")

def test_cli_send_signature_verifies_with_pq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broadcast path should produce signatures the PQ verifier accepts."""

    from pq.py.address import address_from_pubkey
    from pq.py.sign import Signature
    from animica.tx.signing import ChainContext, pq_verify_tx
    from omni_sdk.wallet.signer import PQSigner
    import cbor2

    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_PQ_VERIFY_DEBUG", "1")

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    signer_addr = address_from_pubkey(signer.public_key, signer.alg_id)
    chain_ctx = ChainContext(
        chain_id=1,
        genesis_hash=b"\x00" * 32,
        network="test",
        fork_id=None,
        domain="tx",
        prehash="sha3-512",
    )

    rpc_url = "http://localhost:9999/rpc"

    def fake_rpc(url: str, method: str, params=None, timeout=None):  # type: ignore[no-untyped-def]
        del timeout
        assert url == rpc_url
        params = params or []

        if method == "chain.getChainIdentity":
            return {
                "chainId": 1,
                "forkId": 0,
                "network": "test",
                "genesisHash": "0x" + ("00" * 32),
            }
        if method == "chain.getChainId":
            return 1
        if method == "sync.getStatus":
            return {
                "phase": "SYNCED",
                "synchronized": True,
                "head_height": 100,
                "best_header_height": 100,
            }
        if method in {
            "mempool.getFeeQuote",
            "fee.quote",
            "tx.estimateFee",
            "tx.gasPrice",
            "gasPrice",
            "fee.getGasPrice",
        }:
            return {"limit": 21000, "price": 1_000_000_000}
        if method in {"mempool.simulateAdmission", "tx.sendRawTransaction"}:
            raw_hex = params[0]
            if raw_hex.startswith("0x"):
                raw_hex = raw_hex[2:]
            raw_bytes = bytes.fromhex(raw_hex)
            envelope = cbor2.loads(raw_bytes)
            sig_obj = envelope["sig"]
            pub = sig_obj.get("pubkey") or sig_obj.get("pk")

            signature_obj = Signature(
                alg_id=sig_obj["algId"],
                alg_name=signer.alg_name,
                domain="tx",
                prehash="sha3-512",
                sig=sig_obj["sig"],
            )
            verify_result = pq_verify_tx(
                envelope, signature_obj, pub, chain_ctx, from_addr=signer_addr
            )
            assert verify_result.ok, verify_result

            return {"tx_hash": "0xaccepted", "accepted_to_mempool": True}

        return None

    monkeypatch.setattr(tx, "_rpc", fake_rpc)

    result = runner.invoke(
        tx.app,
        [
            "send",
            "--from",
            signer_addr,
            "--to",
            signer_addr,
            "--value",
            "1",
            "--nonce",
            "0",
            "--valid-from",
            "1",
            "--valid-until",
            "100",
            "--dry-run",
            "--secret-key-hex",
            signer.secret_key.hex(),
            "--public-key-hex",
            signer.public_key.hex(),
            "--alg-id",
            str(signer.alg_id),
            "--chain-id",
            "1",
            "--rpc-url",
            rpc_url,
            "--verbose",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Invalid post-quantum signature" not in result.output
