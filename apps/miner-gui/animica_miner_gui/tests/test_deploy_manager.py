import pytest

from animica_miner_gui.backend.rpc_client import RPCError
from animica_miner_gui.ide.deploy_manager import DeploymentError, DeploymentManager


class DummyRPC:
    def __init__(self, methods, *, send_result="0xabc", raise_error=None):
        self._methods = methods
        self._send_result = send_result
        self._raise_error = raise_error
        self.calls = []

    def get_rpc_methods(self):
        return list(self._methods)

    def _call(self, method, params=None):
        self.calls.append((method, params))
        if self._raise_error is not None:
            raise self._raise_error
        return self._send_result


def test_send_raw_transaction_uses_send_raw_method(tmp_path):
    rpc = DummyRPC(["tx.sendRawTransaction", "tx.getTransactionReceipt"])
    mgr = DeploymentManager(rpc, workspace=tmp_path, wallet_path=tmp_path / "wallets.json")
    tx_hash = mgr.send_raw_transaction("0xdeadbeef")
    assert tx_hash == "0xabc"
    assert rpc.calls == [("tx.sendRawTransaction", ["0xdeadbeef"])]


def test_send_raw_transaction_handles_method_not_found(tmp_path):
    rpc_error = RPCError("RPC error: Method not found", code=-32601, data={"code": -32601})
    rpc = DummyRPC(["tx.sendRawTransaction", "tx.getTransactionReceipt"], raise_error=rpc_error)
    mgr = DeploymentManager(rpc, workspace=tmp_path, wallet_path=tmp_path / "wallets.json")
    with pytest.raises(DeploymentError) as exc:
        mgr.send_raw_transaction("0xdeadbeef")
    assert "Available methods" in str(exc.value)
