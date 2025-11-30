from __future__ import annotations

import json

from anyio import from_thread

from rpc import ws
from rpc.models import Head
from rpc.tests import new_test_client, ws_connect


def _make_head(number: int = 1) -> Head:
    return Head(
        chainId=1,
        number=number,
        hash="0x" + "11" * 32,
        parentHash="0x" + "22" * 32,
        timestamp=1_700_000_000 + number,
        thetaMicro=1_500_000,
    )


def test_ws_new_heads_roundtrip():
    """Subscribing to newHeads should deliver broadcast head snapshots."""

    client, _, _ = new_test_client()

    with ws_connect(client, path="/ws") as ws_client:
        # subscribe
        ws_client.send_json({"op": "sub", "topics": ["newHeads"]})

        head = _make_head(3)
        # Broadcast via the shared hub
        from_thread.run(ws.publish_new_head, head)

        # Drain messages until we see the head payload
        for _ in range(5):
            msg_raw = ws_client.receive_text()
            msg = json.loads(msg_raw)
            if msg.get("topic") == "newHeads":
                payload = msg.get("data", {})
                assert payload["number"] == head.number
                assert payload["hash"] == head.hash
                assert payload["parentHash"] == head.parent_hash
                break
        else:
            raise AssertionError("No newHeads payload received over WebSocket")
