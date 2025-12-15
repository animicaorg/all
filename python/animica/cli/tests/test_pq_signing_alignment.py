"""CLI PQ signing alignment tests."""

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

# Some CLI submodules import optional dependencies like requests; provide a stub to
# avoid import-time failures during focused unit tests.
import sys
import types

SDK_ROOT = Path(__file__).resolve().parents[4] / "sdk" / "python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

sys.modules.setdefault("requests", types.SimpleNamespace())

from animica.cli import tx
from animica.tx.signing import build_signable_tx_bytes
from omni_sdk.tx.build import transfer
from omni_sdk.tx.encode import unpack_signed
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

@respx.mock
def test_cli_send_signature_verifies_with_pq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broadcast path should produce signatures the PQ verifier accepts."""

    from pq.py.sign import Signature
    from pq.py.verify import verify_detached
    from omni_sdk.wallet.signer import PQSigner
    from rpc.methods import tx as rpc_tx

    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_PQ_VERIFY_DEBUG", "1")

    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    wallet_file = tmp_path / "wallets.json"
    wallet_entry = {
        "label": "alice",
        "address": signer.address
        or "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
        "alg_id": signer.alg_id,
        "alg_name": signer.alg_name,
        "public_key_hex": signer.public_key.hex(),
        "secret_key_hex": signer.secret_key.hex(),
        "created_at": "2025-01-01T00:00:00Z",
    }
    wallet_file.write_text(json.dumps({"version": 1, "wallets": [wallet_entry]}, indent=2))

    rpc_url = "http://localhost:9999/rpc"

    # Ensure the RPC layer expects the same chain ID the CLI will sign with
    monkeypatch.setattr(rpc_tx.deps, "get_chain_id", lambda: 1)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "chain.getChainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": 1})
        if method == "state.getTransactionCount":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": 0})
        if method == "state.suggestGasPrice":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": "1000000000"})
        if method == "tx.sendRawTransaction":
            raw_hex = payload["params"][0]
            if raw_hex.startswith("0x"):
                raw_hex = raw_hex[2:]
            raw_bytes = bytes.fromhex(raw_hex)
            envelope = unpack_signed(raw_bytes)

            # The RPC verifier must accept the CLI's signature bytes
            message = build_signable_tx_bytes(envelope)
            signature_obj = Signature(
                alg_id=envelope["sig"]["algId"],
                alg_name=signer.alg_name,
                domain="tx",
                prehash="sha3-512",
                sig=envelope["sig"]["sig"],
            )

            assert verify_detached(
                message, signature_obj, envelope["sig"]["pubkey"], chain_id=1
            )

            # RPC helper should also pass (no exception)
            rpc_tx._verify_pq_signature(envelope, envelope, chain_id=1)

            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": "0xaccepted"},
            )

        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload.get("id", 0), "result": None})

    respx.post(rpc_url).mock(side_effect=handler)

    result = runner.invoke(
        tx.app,
        [
            "send",
            "--wallet-file",
            str(wallet_file),
            "--from",
            wallet_entry["label"],
            "--to",
            wallet_entry["address"],
            "--value",
            "1",
            "--chain-id",
            "1",
            "--rpc-url",
            rpc_url,
            "--verbose",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Invalid post-quantum signature" not in result.output

