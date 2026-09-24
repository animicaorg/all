"""`animica tx send --emit-raw` — sign without submitting.

This is the flag that makes the x402 ANM lane usable from the CLI at all. That
lane ("exact-anm") wants a SIGNED but UNSUBMITTED transfer: the payer signs, the
gateway submits. If --emit-raw ever falls through to the submit call, an agent
paying for a resource would broadcast the transfer itself and then have nothing
left to present as payment — paid out, and still owing payment. So the ordering
below is the property worth locking, not the output formatting.
"""
from __future__ import annotations

import inspect
import re

from animica.cli import tx as tx_cli


def _send_source() -> str:
    return inspect.getsource(tx_cli.send)


def test_emit_raw_option_exists():
    params = inspect.signature(tx_cli.send).parameters
    assert "emit_raw" in params, "--emit-raw was removed from `animica tx send`"


def test_emit_raw_returns_before_any_submit():
    src = _send_source()
    guard = src.find("if emit_raw:")
    assert guard != -1, "the --emit-raw guard is gone"
    # NOT a plain search for "tx.sendRawTransaction": that string also appears
    # far earlier as the method name passed to guard_bootstrap_rpc, which is a
    # permission check rather than a submission.
    submit = src.find("send_result = _rpc(rpc, send_method")
    assert submit != -1, "expected the real submit call (_rpc(rpc, send_method, ...)) in send()"
    assert guard < submit, (
        "--emit-raw must short-circuit BEFORE the transaction is submitted; "
        "otherwise signing for an x402 payment also spends it"
    )


def test_emit_raw_block_exits_rather_than_falling_through():
    src = _send_source()
    block = src[src.find("if emit_raw:"):]
    # Only the guard's own body matters: up to the next line at the same indent.
    body = block[: block.find("\n            # Submit")]
    assert "typer.Exit" in body, "the --emit-raw branch must exit, not fall through to submit"
    assert "raw_transaction" in body, "the branch must actually emit the signed transaction"
    assert '"submitted": False' in body, "callers rely on submitted:false to know it was not broadcast"


def test_alg_id_help_names_the_live_scheme_not_the_stranded_one():
    src = _send_source()
    m = re.search(r'"--alg-id",\s*help=\((.*?)\),\s*\n', src, re.S)
    assert m, "could not read the --alg-id help text"
    help_text = m.group(1)
    assert "4099" in help_text, "help must name 4099 (0x1003, ML-DSA-65), the scheme actually in use"
    # 4098 may be mentioned, but only as the legacy/stranded one.
    if "4098" in help_text:
        assert "stranded" in help_text or "legacy" in help_text, (
            "4098 is SPHINCS+ and is stranded at consensus; help must not present it as usable"
        )
