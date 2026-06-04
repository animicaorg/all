"""Phase-4 gate: XMR share credit redirects to the renter during a rental."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.xmr_stratum import XmrStratumServer, XmrSession


class _Writer:
    def write(self, _data):
        pass

    async def drain(self):
        pass


class _CapturingValidator:
    def __init__(self):
        self.seen_miner_id = None

    async def validate(self, *, miner_id, job_id, nonce_hex, result_hex):
        self.seen_miner_id = miner_id
        return False, "test-stop", None  # short-circuit before the ledger


OWNER = "anim1ownerxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
RENTER = "anim1renterxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _server(validator, resolver):
    return XmrStratumServer(
        host="127.0.0.1", port=0, job_manager=None, validator=validator,
        ledger=None, rental_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_xmr_credit_redirects_to_renter():
    validator = _CapturingValidator()
    rental = {"coins": "XMR", "renter_xmr_anim_address": RENTER}
    server = _server(validator, lambda worker, addr: rental)
    sess = XmrSession(session_id="s1", writer=_Writer(), miner_address=OWNER, worker="rig-01")
    await server._handle_submit(sess, 1, {"job_id": "j", "nonce": "00", "result": "ab"})
    assert validator.seen_miner_id == RENTER


@pytest.mark.asyncio
async def test_xmr_no_rental_keeps_owner():
    validator = _CapturingValidator()
    server = _server(validator, lambda worker, addr: None)
    sess = XmrSession(session_id="s1", writer=_Writer(), miner_address=OWNER, worker="rig-01")
    await server._handle_submit(sess, 1, {"job_id": "j", "nonce": "00", "result": "ab"})
    assert validator.seen_miner_id == OWNER


@pytest.mark.asyncio
async def test_xmr_anm_only_rental_does_not_redirect():
    validator = _CapturingValidator()
    rental = {"coins": "ANM", "renter_anm_address": RENTER}
    server = _server(validator, lambda worker, addr: rental)
    sess = XmrSession(session_id="s1", writer=_Writer(), miner_address=OWNER, worker="rig-01")
    await server._handle_submit(sess, 1, {"job_id": "j", "nonce": "00", "result": "ab"})
    assert validator.seen_miner_id == OWNER, "ANM-only rental must not divert XMR credit"
