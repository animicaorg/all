import binascii
import hashlib
from typing import Any, Dict, List, Tuple

from omni_sdk.randomness.client import RandomnessClient


def _unhex(x: str) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    s = str(x)
    if s.startswith(("0x", "0X")):
        s = s[2:]
    return binascii.unhexlify(s)


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


class FakeRandRpc:
    """
    Minimal JSON-RPC stub for randomness endpoints.
    Simulates a single open round with basic commit→reveal→beacon flow.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Any]] = []
        self.round_id: int = 1
        self.params = {
            "roundSeconds": 30,
            "revealGraceSeconds": 10,
            "vdf": {"iterations": 1000},
        }
        # commitment -> (addr, salt, payload)
        self._commits: Dict[str, Tuple[str, bytes, bytes]] = {}
        # revealed commitments for the round
        self._revealed: List[str] = []

    def call(self, method: str, params):
        self.calls.append((method, params))

        if method in ("rand.getParams", "randomness.getParams"):
            return self.params

        if method in ("rand.getRound", "randomness.getRound"):
            return {"roundId": self.round_id, "phase": "open"}

        if method in ("rand.commit", "randomness.commit"):
            # Accept either [addr, salt_hex, payload_hex] or dict
            if isinstance(params, (list, tuple)):
                addr = str(params[0])
                salt = _unhex(params[1])
                payload = _unhex(params[2])
            else:
                addr = str(params.get("address"))
                salt = _unhex(params.get("salt"))
                payload = _unhex(params.get("payload"))

            dom = b"animica:rand:commit|"
            commit = _hex(
                hashlib.sha3_256(dom + addr.encode("utf-8") + salt + payload).digest()
            )
            self._commits[commit] = (addr, salt, payload)
            return {"roundId": self.round_id, "commitment": commit}

        if method in ("rand.reveal", "randomness.reveal"):
            if isinstance(params, (list, tuple)):
                salt = _unhex(params[0])
                payload = _unhex(params[1])
            else:
                salt = _unhex(params.get("salt"))
                payload = _unhex(params.get("payload"))

            # We don't know address; try to match any existing commitment with same salt/payload.
            dom = b"animica:rand:commit|"
            ok = False
            matched = None
            for commit, (addr, s, p) in self._commits.items():
                if s == salt and p == payload:
                    want = _hex(
                        hashlib.sha3_256(
                            dom + addr.encode("utf-8") + salt + payload
                        ).digest()
                    )
                    if want == commit:
                        ok = True
                        matched = commit
                        break
            if ok and matched not in self._revealed:
                self._revealed.append(matched)  # mark revealed
            return {"roundId": self.round_id, "ok": ok}

        if method in ("rand.getBeacon", "randomness.getBeacon"):
            # Build a deterministic beacon from revealed commitments (order-independent)
            if not self._revealed:
                out = hashlib.sha3_256(
                    b"beacon|empty|" + str(self.round_id).encode()
                ).digest()
            else:
                acc = hashlib.sha3_256()
                acc.update(b"beacon|round|")
                acc.update(str(self.round_id).encode())
                for c in sorted(self._revealed):
                    acc.update(_unhex(c))
                out = acc.digest()
            return {"roundId": self.round_id, "output": _hex(out)}

        if method in ("rand.getHistory", "randomness.getHistory"):
            # Return minimal history: just the current round's beacon.
            beacon = self.call("rand.getBeacon", [])
            return {"items": [beacon], "next": None}

        # Unknown: return None
        return None


def test_commit_reveal_beacon_roundtrip():
    rpc = FakeRandRpc()
    rand = RandomnessClient(rpc)

    # Params & round
    p = rand.get_params()
    assert isinstance(p, dict) and "vdf" in p
    r = rand.get_round()
    assert isinstance(r, dict) and r.get("roundId") == 1

    # Commit with explicit address/salt/payload
    addr = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    salt = b"\xaa" * 32
    payload = b"hello-beacon"
    c = rand.commit(address=addr, salt=salt, payload=payload)
    assert (
        isinstance(c, dict)
        and c.get("roundId") == 1
        and str(c.get("commitment")).startswith("0x")
    )

    # Reveal must succeed
    rev = rand.reveal(salt=salt, payload=payload)
    ok = rev.get("ok")
    assert ok is True

    # Beacon should now depend on the single revealed commitment
    b = rand.get_beacon()
    assert isinstance(b, dict) and b.get("roundId") == 1
    out = b.get("output")
    assert (
        isinstance(out, str) and out.startswith("0x") and len(out) == 66
    )  # 32-byte hash

    # History returns the same beacon entry
    hist = rand.get_history()
    assert isinstance(hist, dict)
    items = hist.get("items") or hist.get("results") or []
    assert isinstance(items, list) and len(items) >= 1
    first = items[0]
    assert first.get("roundId") == 1
    assert first.get("output") == out


def test_reveal_with_wrong_payload_fails():
    rpc = FakeRandRpc()
    rand = RandomnessClient(rpc)

    addr = "anim1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    salt = b"\x11" * 32
    payload = b"correct"
    tampered = b"wrong"

    rand.commit(address=addr, salt=salt, payload=payload)
    rev = rand.reveal(salt=salt, payload=tampered)
    assert rev.get("ok") is False

    # With no successful reveals, beacon should still be well-formed but "empty"
    b = rand.get_beacon()
    assert isinstance(b.get("output"), str) and b["output"].startswith("0x")
