"""Integration test for the pool's /api/rental/* HMAC-guarded endpoints.

Covers the seam the marketplace depends on (no NOWPayments needed): the HMAC
auth, assignment create/get, conflict rejection, and the rigs listing.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SECRET = "test-rental-secret-0123456789"
os.environ["POOL_RENTAL_SHARED_SECRET"] = SECRET

from fastapi.testclient import TestClient

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics
from animica.stratum_pool.api import create_app


class DummyJobManager:
    def request_refresh(self) -> None:
        pass


class DummyServer:
    def stats(self):
        return {}

    def session_snapshots(self):
        return []


OWNER = "anim1ownerxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
RENTER = "anim1renterxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def _client(tmp_path) -> TestClient:
    db = tmp_path / "pool.db"
    metrics = PoolMetrics(PoolConfig(db_url=f"sqlite:///{db}", pool_mode="pps"), DummyJobManager(), DummyServer())
    return TestClient(create_app(metrics))


def _sign(method: str, path: str, body: str = ""):
    ts = str(int(time.time()))
    msg = f"{method}\n{path}\n{ts}\n{body}".encode()
    sig = hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return {"x-rental-sig": sig, "x-rental-ts": ts, "content-type": "application/json"}


def test_rental_endpoints_require_hmac(tmp_path):
    client = _client(tmp_path)
    # No signature → 401
    assert client.get("/api/rental/rigs").status_code == 401
    # Bad signature → 401
    bad = {"x-rental-sig": "deadbeef", "x-rental-ts": str(int(time.time()))}
    assert client.get("/api/rental/rigs", headers=bad).status_code == 401
    # Valid signature → 200
    ok = client.get("/api/rental/rigs", headers=_sign("GET", "/api/rental/rigs"))
    assert ok.status_code == 200
    assert "items" in ok.json()


def test_assignment_create_get_and_conflict(tmp_path):
    client = _client(tmp_path)
    body = json.dumps({
        "rental_id": "rent-1",
        "owner_worker": "rig-01",
        "owner_address": OWNER,
        "coins": "ANM",
        "anm_mode": "pps",
        "renter_anm_address": RENTER,
        "start_ts": time.time() - 5,
        "end_ts": time.time() + 3600,
    })
    res = client.post("/api/rental/assignments", headers=_sign("POST", "/api/rental/assignments", body), content=body)
    assert res.status_code == 200, res.text
    assert res.json()["assignment"]["status"] == "active"

    # Fetch it back.
    got = client.get("/api/rental/assignments/rent-1", headers=_sign("GET", "/api/rental/assignments/rent-1"))
    assert got.status_code == 200
    assert got.json()["assignment"]["renter_anm_address"] == RENTER

    # A second active rental for the same rig → 409.
    body2 = json.dumps({
        "rental_id": "rent-2",
        "owner_worker": "rig-01",
        "owner_address": OWNER,
        "coins": "ANM",
        "anm_mode": "pps",
        "renter_anm_address": RENTER,
        "start_ts": time.time(),
        "end_ts": time.time() + 60,
    })
    dup = client.post("/api/rental/assignments", headers=_sign("POST", "/api/rental/assignments", body2), content=body2)
    assert dup.status_code == 409, dup.text

    # Cancel → then a new assignment is accepted.
    cancel = client.post("/api/rental/assignments/rent-1/cancel", headers=_sign("POST", "/api/rental/assignments/rent-1/cancel"))
    assert cancel.status_code == 200 and cancel.json()["cancelled"] is True


def test_assignment_validates_coins_and_address(tmp_path):
    client = _client(tmp_path)
    body = json.dumps({
        "rental_id": "rent-x",
        "owner_worker": "rig-01",
        "owner_address": "not-an-address",
        "coins": "ANM",
        "renter_anm_address": RENTER,
        "start_ts": time.time(),
        "end_ts": time.time() + 60,
    })
    res = client.post("/api/rental/assignments", headers=_sign("POST", "/api/rental/assignments", body), content=body)
    assert res.status_code == 400
