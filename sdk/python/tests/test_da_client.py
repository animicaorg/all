import binascii
from typing import Any, Dict, List, Tuple

from omni_sdk.da.client import DAClient


class FakeRpc:
    """
    Minimal JSON-RPC stub for DA endpoints.
    Accepts several method-name variants to stay compatible with different mounts.
    """
    def __init__(self) -> None:
        self.calls: List[Tuple[str, Any]] = []
        self._store: Dict[str, bytes] = {}

    def call(self, method: str, params):
        self.calls.append((method, params))

        # --- Put / Post blob
        if method in ("da.putBlob", "da.postBlob", "da.blob.put"):
            # Tolerate param shapes:
            #  - [namespace:int, data_hex:str]
            #  - [{"namespace": int, "data": "0x..."}]
            if isinstance(params, (list, tuple)) and params:
                if isinstance(params[0], dict):
                    p = params[0]
                    ns = int(p.get("namespace"))
                    data_hex = str(p.get("data"))
                else:
                    ns = int(params[0])
                    data_hex = str(params[1])
            elif isinstance(params, dict):
                ns = int(params.get("namespace"))
                data_hex = str(params.get("data"))
            else:
                raise AssertionError("unexpected params for putBlob")

            if data_hex.startswith("0x") or data_hex.startswith("0X"):
                data_hex = data_hex[2:]
            blob = binascii.unhexlify(data_hex)
            commit = "0x" + binascii.sha256(blob).hexdigest()  # deterministic enough for a stub
            self._store[commit] = blob
            return {"commitment": commit, "namespace": ns, "size": len(blob)}

        # --- Get blob
        if method in ("da.getBlob", "da.blob.get"):
            # Params: [commitment:str] or {"commitment": "..."}
            if isinstance(params, (list, tuple)):
                commit = str(params[0])
            else:
                commit = str(params.get("commitment"))
            blob = self._store.get(commit, b"")
            return "0x" + blob.hex()

        # --- Get availability proof
        if method in ("da.getProof", "da.blob.getProof", "da.getAvailabilityProof"):
            # Params: [commitment:str, samples:int?] (we ignore details in the stub)
            if isinstance(params, (list, tuple)):
                commit = str(params[0])
            else:
                commit = str(params.get("commitment"))
            exists = commit in self._store
            return {
                "commitment": commit,
                "ok": exists,
                "samples": [{"index": 0, "branch": []}],
            }

        # Unknown — return None to simulate non-existent method
        return None


def test_post_get_proof_roundtrip():
    rpc = FakeRpc()
    da = DAClient(rpc)

    data = b"hello data availability"
    ns = 24

    # Post
    receipt = da.post_blob(data=data, namespace=ns)
    assert isinstance(receipt, dict)
    commit = receipt.get("commitment")
    assert isinstance(commit, str) and commit.startswith("0x")
    assert receipt.get("size") == len(data)
    assert receipt.get("namespace") == ns

    # Get
    out = da.get_blob(commit)
    assert isinstance(out, (bytes, bytearray))
    assert out == data

    # Proof
    proof = da.get_proof(commit)
    # Accept either 'ok' or 'valid' flag depending on client naming
    ok = proof.get("ok")
    if ok is None:
        ok = proof.get("valid")
    assert ok is True

    # Ensure expected methods were called
    methods = [m for (m, _p) in rpc.calls]
    assert any(m.startswith("da.") and ("put" in m or "post" in m) for m in methods)
    assert any(m.startswith("da.") and "getBlob" in m or "blob.get" in m for m in methods)
    assert any(m.startswith("da.") and ("Proof" in m or "getProof" in m) for m in methods)
