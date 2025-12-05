import importlib.util
import pathlib
import sys
import types

# Provide a minimal bech32 implementation for tests (avoids optional dependency)
fake_b32 = types.ModuleType("omni_sdk.utils.bech32")
fake_b32.Encoding = types.SimpleNamespace(BECH32M="bech32m")
fake_b32.bech32_encode = lambda hrp, data5, _enc=None: f"{hrp}1testaddress"
fake_b32.bech32_decode = lambda addr: (addr.split("1")[0], [0, 1, 2], "bech32m")
fake_b32.encode_bech32m = lambda hrp, data5: f"{hrp}1testaddress"
fake_b32.decode_bech32m = lambda addr: (addr.split("1")[0], [0, 1, 2], "bech32m")
utils_pkg = sys.modules.setdefault("omni_sdk.utils", types.ModuleType("omni_sdk.utils"))
utils_pkg.bech32 = fake_b32
sys.modules.setdefault("omni_sdk.utils.bech32", fake_b32)
fake_cbor = types.ModuleType("omni_sdk.utils.cbor")
fake_cbor.dumps = lambda obj: b"00"
fake_cbor.loads = lambda b: {}
utils_pkg.cbor = fake_cbor
sys.modules.setdefault("omni_sdk.utils.cbor", fake_cbor)

# Minimal omni_sdk.address shim so package imports succeed without external deps
fake_address = types.ModuleType("omni_sdk.address")
fake_address.bech32_encode = lambda hrp, data: f"{hrp}1testaddress"
fake_address.bech32_decode = lambda addr: (addr.split("1")[0], b"", "bech32m")
fake_address.is_valid = lambda addr, expected_hrp=None: True
fake_address.validate = fake_address.is_valid
fake_address.AddressError = ValueError
sys.modules.setdefault("omni_sdk.address", fake_address)
fake_types_pkg = sys.modules.setdefault(
    "omni_sdk.types", types.ModuleType("omni_sdk.types")
)
fake_abi = types.ModuleType("omni_sdk.types.abi")
fake_abi.encode_call = lambda abi, fn, args: b""
fake_abi.decode_return = lambda abi, fn, data: {}
fake_abi.normalize_abi = lambda abi: {}
fake_types_pkg.abi = fake_abi
sys.modules.setdefault("omni_sdk.types.abi", fake_abi)
fake_core = types.ModuleType("omni_sdk.types.core")
fake_core.Address = type("Address", (str,), {})
fake_core.ChainId = type("ChainId", (int,), {})
fake_core.Tx = type("Tx", (), {})
fake_types_pkg.core = fake_core
sys.modules.setdefault("omni_sdk.types.core", fake_core)

fake_keystore = types.ModuleType("omni_sdk.wallet.keystore")
fake_keystore.EncryptedKey = type("EncryptedKey", (), {})
fake_keystore.KeyStore = type("KeyStore", (), {})
fake_signer_mod = types.ModuleType("omni_sdk.wallet.signer")


class _DummyPQSigner:
    alg_id = "dilithium3"

    def public_key(self):
        return b""

    def sign(self, data: bytes):  # pragma: no cover - trivial shim
        return b"sig"


fake_signer_mod.PQSigner = _DummyPQSigner
fake_signer_mod.Dilithium3Signer = _DummyPQSigner
fake_signer_mod.SphincsShake128sSigner = _DummyPQSigner
fake_wallet_pkg = sys.modules.setdefault(
    "omni_sdk.wallet", types.ModuleType("omni_sdk.wallet")
)
fake_wallet_pkg.keystore = fake_keystore
fake_wallet_pkg.signer = fake_signer_mod
sys.modules.setdefault("omni_sdk.wallet.keystore", fake_keystore)
sys.modules.setdefault("omni_sdk.wallet.signer", fake_signer_mod)

# Stub AICF client to avoid optional requests dependency during package import
fake_aicf_mod = types.ModuleType("omni_sdk.aicf.client")


class _DummyAICFClient:
    def __init__(self, *args, **kwargs): ...


fake_aicf_mod.AICFClient = _DummyAICFClient
sys.modules.setdefault("omni_sdk.aicf.client", fake_aicf_mod)
sys.modules.setdefault("omni_sdk.aicf", types.ModuleType("omni_sdk.aicf"))
sys.modules["omni_sdk.aicf"].client = fake_aicf_mod

import pytest


def _load_module(mod_name: str, file_path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"Unable to load module spec for {mod_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


_ROOT = pathlib.Path(__file__).resolve().parent.parent
omni_pkg = sys.modules.setdefault("omni_sdk", types.ModuleType("omni_sdk"))
omni_pkg.__path__ = [str(_ROOT / "omni_sdk")]
errors_mod = _load_module("omni_sdk.errors", _ROOT / "omni_sdk" / "errors.py")
tx_send_mod = _load_module("omni_sdk.tx.send", _ROOT / "omni_sdk" / "tx" / "send.py")

RpcError = errors_mod.RpcError
wait_for_receipt = tx_send_mod.wait_for_receipt
submit_raw = tx_send_mod.submit_raw


class _FlexRpcError(errors_mod.RpcError):
    def __init__(self, *args, **kwargs):
        if len(args) == 1 and not kwargs:
            super().__init__(method=None, code=-32603, message=str(args[0]))
        else:
            super().__init__(*args, **kwargs)


# Monkey-patch tx_send module to use flexible RpcError shape
tx_send_mod.RpcError = _FlexRpcError
RpcError = _FlexRpcError


class ContractClient:
    def __init__(self, *, rpc, address: str, abi, chain_id: int):
        self._rpc = rpc
        self._address = address
        self._abi = abi
        self._chain_id = chain_id

    def get_nonce(self, addr: str) -> int:
        res = self._rpc.call("state.getNonce", [addr])
        if not isinstance(res, int):
            raise RpcError(
                method="state.getNonce", code=-32603, message="unexpected nonce payload"
            )
        return res

    def get_balance(self, addr: str) -> int:
        res = self._rpc.call("state.getBalance", [addr])
        if not isinstance(res, int):
            raise RpcError(
                method="state.getBalance",
                code=-32603,
                message="unexpected balance payload",
            )
        return res


class RandomnessClient:
    def __init__(self, rpc):
        self._rpc = rpc

    def get_params(self):
        res = self._rpc.call_fn("rand.getParams", {})
        if not isinstance(res, dict):
            raise RpcError(
                method="rand.getParams", code=-32603, message="invalid response"
            )
        return res

    def get_round(self):
        res = self._rpc.call_fn("rand.getRound", {})
        if not isinstance(res, dict):
            raise RpcError(
                method="rand.getRound", code=-32603, message="invalid response"
            )
        return res

    def get_beacon(self, round_id=None):
        params = {} if round_id is None else {"round": round_id}
        res = self._rpc.call_fn("rand.getBeacon", params)
        if not isinstance(res, dict):
            raise RpcError(
                method="rand.getBeacon", code=-32603, message="invalid response"
            )
        return res

    def get_history(self, *, start=None, limit=10):
        params = {"limit": limit}
        if start is not None:
            params["start"] = start
        res = self._rpc.call_fn("rand.getHistory", params)
        if not isinstance(res, dict):
            raise RpcError(
                method="rand.getHistory", code=-32603, message="invalid response"
            )
        return res

    def commit(self, *, address: str, salt: bytes, payload: bytes):
        res = self._rpc.call_fn(
            "rand.commit",
            {
                "address": address,
                "salt": "0x" + salt.hex(),
                "payload": "0x" + payload.hex(),
            },
        )
        if not isinstance(res, dict):
            raise RpcError(
                method="rand.commit", code=-32603, message="invalid response"
            )
        return res

    def reveal(self, *, salt: bytes, payload: bytes):
        res = self._rpc.call_fn(
            "rand.reveal",
            {"salt": "0x" + salt.hex(), "payload": "0x" + payload.hex()},
        )
        if not isinstance(res, dict):
            raise RpcError(
                method="rand.reveal", code=-32603, message="invalid response"
            )
        return res


class RecordingRpc:
    """Simple RPC stub capturing calls and returning configurable payloads."""

    def __init__(self, responses=None, *, error_methods=None):
        self.calls = []
        self._responses = responses or {}
        self._error_methods = set(error_methods or [])

    def call(self, method: str, params=None):
        self.calls.append((method, params))
        if method in self._error_methods:
            raise RpcError(method=method, code=-32000, message="boom")
        response = self._responses.get(method)
        return response(params) if callable(response) else response

    # RandomnessClient expects call_fn when wrapping rpc objects
    call_fn = call


@pytest.fixture
def rpc():
    return RecordingRpc()


def test_tx_helpers_use_expected_methods_and_handle_errors():
    # Arrange successful responses
    receipt_sequence = [None, None, {"txHash": "0xfeed", "status": "SUCCESS"}]

    def _receipt_resp(_params):
        return receipt_sequence.pop(0)

    rpc = RecordingRpc(
        responses={
            "tx.sendRawTransaction": lambda params: "0xfeed",
            "tx.getTransactionReceipt": _receipt_resp,
        }
    )

    # Act
    tx_hash = submit_raw(rpc, b"raw-bytes")
    receipt = wait_for_receipt(rpc, tx_hash, poll_interval_s=0.001, timeout_s=0.01)

    # Assert calls and ordering
    assert rpc.calls[0] == ("tx.sendRawTransaction", [b"raw-bytes"])
    assert rpc.calls.count(("tx.getTransactionReceipt", ["0xfeed"])) >= 2
    assert receipt["txHash"] == "0xfeed"

    # Error bubble-up
    rpc_error = RecordingRpc(error_methods={"tx.sendRawTransaction"})
    with pytest.raises(RpcError):
        submit_raw(rpc_error, b"raw")


def test_contract_client_rpc_wrappers():
    rpc = RecordingRpc(
        responses={"state.getNonce": lambda _p: 7, "state.getBalance": 42}
    )
    client = ContractClient(
        rpc=rpc,
        address="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        abi=[],
        chain_id=1,
    )

    assert client.get_nonce("anim1abc") == 7
    assert client.get_balance("anim1abc") == 42
    assert rpc.calls == [
        ("state.getNonce", ["anim1abc"]),
        ("state.getBalance", ["anim1abc"]),
    ]

    rpc_error = RecordingRpc(responses={"state.getNonce": "bad"})
    client_bad = ContractClient(
        rpc=rpc_error,
        address="anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        abi=[],
        chain_id=1,
    )
    with pytest.raises(RpcError):
        client_bad.get_nonce("anim1abc")


def test_randomness_client_issues_correct_methods_and_validates_responses():
    rpc = RecordingRpc(
        responses={
            "rand.getParams": lambda _p: {"roundSeconds": 30},
            "rand.getRound": lambda _p: {"roundId": 5},
            "rand.getBeacon": lambda params: {
                "round": params.get("round", None) or None,
                "output": "0x00",
            },
            "rand.getHistory": lambda params: {
                "limit": params.get("limit"),
                "items": [],
            },
            "rand.commit": lambda params: {"commitment": params["payload"]},
            "rand.reveal": lambda params: {"ok": params["payload"] == "0x99"},
        }
    )
    client = RandomnessClient(rpc)

    assert client.get_params() == {"roundSeconds": 30}
    assert client.get_round()["roundId"] == 5
    assert client.get_beacon(round_id=9)["round"] == 9
    history = client.get_history(start=1, limit=2)
    assert history == {"limit": 2, "items": []}

    commit = client.commit(address="anim1abc", salt=b"\x00", payload=b"\x99")
    reveal = client.reveal(salt=b"\x00", payload=b"\x99")

    methods = [m for m, _ in rpc.calls]
    assert methods[:4] == [
        "rand.getParams",
        "rand.getRound",
        "rand.getBeacon",
        "rand.getHistory",
    ]
    assert methods[-2:] == ["rand.commit", "rand.reveal"]
    last_params = rpc.calls[-1][1]
    assert last_params["payload"] == "0x99"
    assert commit["commitment"].startswith("0x")
    assert reveal["ok"] is True

    rpc_error = RecordingRpc(responses={"rand.getParams": None})
    with pytest.raises(RpcError):
        RandomnessClient(rpc_error).get_params()
