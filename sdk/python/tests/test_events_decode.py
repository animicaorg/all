import hashlib
from typing import Any, Dict, List, Optional

import pytest
from omni_sdk.contracts.events import EventDecoder


def _sig_topic0(signature: str) -> str:
    """
    Animica specs consistently use SHA3 (NIST) in several places. We compute
    topic0 as sha3_256(event_signature).hex() and prefix with 0x.

    If your EventDecoder internally uses the same, the hashes will match.
    If it uses Keccak-256 instead, update this helper accordingly or have the
    decoder accept logs with a provided topic0.
    """
    return "0x" + hashlib.sha3_256(signature.encode("utf-8")).hexdigest()


def _topic_int(n: int) -> str:
    return "0x" + n.to_bytes(32, "big").hex()


def _first_named(obj: Any, key: str, default: Any = None) -> Any:
    """
    Access helper tolerant to different decoded item shapes:
      - dict with keys 'event', 'args'
      - object with attributes .event / .args / .name / .values
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    val = getattr(obj, key, None)
    if val is not None:
        return val
    # Some decoders might use .name instead of .event
    if key == "event":
        return getattr(obj, "name", default)
    return default


def test_decode_single_indexed_uint64_event():
    """
    ABI: event Tick(uint64 indexed n)
    Log: topics = [topic0, n], data empty
    Expect: one decoded entry with event='Tick' and args['n'] == value
    """
    abi = [
        {
            "type": "event",
            "name": "Tick",
            "inputs": [
                {"name": "n", "type": "uint64", "indexed": True},
            ],
        }
    ]

    dec = EventDecoder(abi)

    value = 7
    signature = "Tick(uint64)"
    topic0 = _sig_topic0(signature)
    topics = [topic0, _topic_int(value)]
    data = "0x"

    raw_log = {
        "address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        "topics": topics,
        "data": data,
    }

    decoded = dec.decode_logs([raw_log])
    assert isinstance(decoded, list) and len(decoded) == 1

    item = decoded[0]
    assert _first_named(item, "event") == "Tick"

    # Args may be a dict or an attribute on the item
    args: Optional[Dict[str, Any]] = None
    if isinstance(item, dict):
        args = item.get("args")
    else:
        args = getattr(item, "args", None) or getattr(item, "values", None)

    assert isinstance(args, dict), "decoded args should be a dict-like"
    assert args.get("n") == value


def test_decoder_ignores_non_matching_topic0():
    """
    If topic0 does not match any event in the ABI, decoder should return empty.
    """
    abi = [
        {
            "type": "event",
            "name": "Tick",
            "inputs": [{"name": "n", "type": "uint64", "indexed": True}],
        }
    ]
    dec = EventDecoder(abi)

    # Non-matching topic0
    bad_topic0 = _sig_topic0("Other(uint64)")
    topics = [bad_topic0, _topic_int(1)]
    raw_log = {"address": "anim1xxx", "topics": topics, "data": "0x"}

    out = dec.decode_logs([raw_log])
    assert isinstance(out, list)
    assert len(out) == 0
