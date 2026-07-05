"""
Security tests for snapshot manifest integrity + bounds + strict completeness.

Cluster: snapshot (findings ANM-C11 / H02 / H05 / H06 / M03).

These tests prove BOTH:
  * fail-closed behaviour (partial restore, oversized/bomb chunk, untrusted/altered
    manifest, path-traversal chunk name are all rejected and the head is NOT set), and
  * backward compatibility (a well-formed unsigned snapshot still imports cleanly with
    no pin/signature configured, and an explicit escape hatch preserves emergency
    operator recovery).

NOTE ON THE cbor SHIM: this hardening worktree is a sparse checkout whose
``core/encoding/cbor.py`` predates ``cbor_loads_prefix`` (the live tree has it).
``core.db.snapshot`` imports that symbol at module load, so we install a faithful
shim built from the worktree's OWN ``_Buf``/``_decode`` (decode one CBOR item, return
bytes consumed) BEFORE importing the module. This does not touch any product file and
exercises the real encode/decode path end-to-end.
"""

from __future__ import annotations

import gzip
import json
import tempfile
from pathlib import Path

import pytest

# --- install cbor_loads_prefix shim on the worktree's incomplete cbor module -------
import core.encoding.cbor as _cbor

if not hasattr(_cbor, "cbor_loads_prefix"):

    def _loads_prefix(b):  # decode ONE self-delimiting CBOR item, return (obj, nbytes)
        buf = _cbor._Buf(bytes(b))
        obj = _cbor._decode(buf)
        return obj, buf.i

    _cbor.cbor_loads_prefix = _loads_prefix  # type: ignore[attr-defined]
    if not hasattr(_cbor, "loads_prefix"):
        _cbor.loads_prefix = _loads_prefix  # type: ignore[attr-defined]

# Now the module imports cleanly.
from core.db.block_db import BlockDB
from core.db.snapshot import (
    SnapshotManifest,
    _compute_manifest_digest,
    _safe_chunk_name,
    export_snapshot,
    import_snapshot,
    verify_snapshot,
)
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block, compute_txs_root_from_txs
from core.types.header import Header
from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
from core.utils.hash import sha3_256


# --------------------------------------------------------------------------- helpers
def _zero() -> bytes:
    return b"\x00" * 32


def _make_source_dbs():
    bkv, skv = SQLiteKV(":memory:"), SQLiteKV(":memory:")
    bdb, sdb = BlockDB(bkv), StateDB(skv)

    unsigned = UnsignedTx(
        version=1, chain_id=1, fork_id=None, valid_after=None, valid_until=None,
        salt=None, gas_price=1, gas_limit=21000, sender=sha3_256(b"snd"),
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=sha3_256(b"rcv"), amount=100, data=b""),
        access_list=(), nonce=0,
    )
    pq = PqSignature(alg_id=1, pubkey=b"pk" + b"x" * 30, sig=b"sig" + b"y" * 200)
    tx = Tx(unsigned=unsigned, sigs=(pq,))
    hdr = Header(
        v=1, chainId=1, height=0, parentHash=_zero(), timestamp=1000,
        stateRoot=_zero(), txsRoot=compute_txs_root_from_txs((tx,)),
        receiptsRoot=_zero(), proofsRoot=_zero(), daRoot=_zero(), mixSeed=_zero(),
        poiesPolicyRoot=_zero(), pqAlgPolicyRoot=_zero(), thetaMicro=1000,
        workType=0, nonce=0, extra=b"",
    )
    blk = Block(header=hdr, txs=(tx,), proofs=(), receipts=None)
    h = hdr.hash()
    bdb.put_header(hdr)
    bdb.put_block(blk)
    bdb.set_canonical(0, h)
    bdb.set_head(0, h)
    bdb.set_chain_id(1)
    # a couple of state entries whose keys/values contain 0x0a (newline) bytes
    sdb.kv.put(b"\x01acc\x0aount", b"bal\x0aance")
    return bdb, sdb


def _build_snapshot(dirpath: Path, compress: bool = True):
    bdb, sdb = _make_source_dbs()
    dirpath.mkdir(parents=True, exist_ok=True)
    manifest = export_snapshot(
        block_db=bdb, state_db=sdb, checkpoint_height=0,
        output_dir=dirpath, compress=compress,
    )
    return manifest


def _fresh_dst():
    kv = SQLiteKV(":memory:")
    return BlockDB(kv), StateDB(kv)


def _load_manifest_json(snapdir: Path) -> dict:
    return json.loads((snapdir / "manifest.json").read_text())


def _write_manifest_json(snapdir: Path, data: dict) -> None:
    (snapdir / "manifest.json").write_text(json.dumps(data, indent=2))


def _manifest_obj_from_data(d: dict) -> SnapshotManifest:
    """Reconstruct a SnapshotManifest exactly as import_snapshot does (for digest)."""
    return SnapshotManifest(
        version=d["version"], chain_id=d["chain_id"], network=d.get("network"),
        checkpoint_height=d["checkpoint_height"], checkpoint_hash=d["checkpoint_hash"],
        timestamp=d["timestamp"], created_at=d.get("created_at", ""),
        blocks_count=d["blocks_count"], headers_count=d["headers_count"],
        accounts_count=d["accounts_count"], storage_keys_count=d["storage_keys_count"],
        code_contracts_count=d["code_contracts_count"], db_engine=d.get("db_engine"),
        db_version=d.get("db_version"), total_size=d.get("total_size", 0),
        state_root=d.get("state_root"), compressed=d.get("compressed", True),
        chunks=d["chunks"],
    )


# --------------------------------------------------------------- backward-compat path
def test_legit_snapshot_imports_cleanly(tmp_path, monkeypatch, caplog):
    """A well-formed unsigned snapshot still imports with no pin/signature configured
    and the strict completeness gates PASS (head is advanced)."""
    for var in (
        "ANIMICA_SNAPSHOT_MANIFEST_DIGEST", "ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS",
        "ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE", "ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE",
    ):
        monkeypatch.delenv(var, raising=False)
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)

    bdb, sdb = _fresh_dst()
    m = import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert m.checkpoint_height == 0
    head = bdb.get_head()
    assert head is not None and head[0] == 0
    # imported the block back
    assert bdb.get_block_by_height(0) is not None


# --------------------------------------------------------------- completeness (H05)
def test_partial_state_restore_is_rejected(tmp_path, monkeypatch):
    """A manifest that declares MORE accounts than were actually restored (a truncated
    / partial restore — the divergence incident) is refused and the head is NOT set."""
    monkeypatch.delenv("ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE", raising=False)
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    data["accounts_count"] = int(data["accounts_count"]) + 5  # claim entries we lack
    _write_manifest_json(snapdir, data)

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="INCOMPLETE"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert bdb.get_head() is None  # head never advanced onto partial state


def test_partial_blocks_restore_is_rejected(tmp_path, monkeypatch):
    """Same protection for a partial BLOCKS restore (missing blocks/headers)."""
    monkeypatch.delenv("ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE", raising=False)
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    data["blocks_count"] = int(data["blocks_count"]) + 3
    _write_manifest_json(snapdir, data)

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="INCOMPLETE"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert bdb.get_head() is None


def test_allow_incomplete_escape_hatch(tmp_path, monkeypatch):
    """The explicit ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE escape hatch preserves emergency
    operator recovery: the same partial restore imports (with a loud error log)."""
    monkeypatch.setenv("ANIMICA_SNAPSHOT_ALLOW_INCOMPLETE", "1")
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    data["accounts_count"] = int(data["accounts_count"]) + 5
    _write_manifest_json(snapdir, data)

    bdb, sdb = _fresh_dst()
    m = import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert m.checkpoint_height == 0
    assert bdb.get_head() is not None  # forced through


# --------------------------------------------------------------- integrity (C11)
def test_manifest_digest_pin_matches(tmp_path, monkeypatch):
    """When the correct digest is pinned, import succeeds."""
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    digest = _compute_manifest_digest(_manifest_obj_from_data(data))
    monkeypatch.setenv("ANIMICA_SNAPSHOT_MANIFEST_DIGEST", digest)

    bdb, sdb = _fresh_dst()
    m = import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert m.checkpoint_height == 0


def test_manifest_digest_pin_mismatch_rejected(tmp_path, monkeypatch):
    """A pinned digest that does not match the manifest is refused (fail-closed)."""
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    monkeypatch.setenv("ANIMICA_SNAPSHOT_MANIFEST_DIGEST", "0x" + "de" * 32)

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="digest mismatch"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert bdb.get_head() is None


def test_digest_pin_detects_count_tamper(tmp_path, monkeypatch):
    """The digest binds the declared counts, so an attacker cannot silently lower a
    count (to slip past the completeness gate) without breaking the pin."""
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    good_digest = _compute_manifest_digest(_manifest_obj_from_data(data))
    # tamper a declared count -> digest no longer matches the pin
    data["accounts_count"] = 0
    _write_manifest_json(snapdir, data)
    monkeypatch.setenv("ANIMICA_SNAPSHOT_MANIFEST_DIGEST", good_digest)

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="digest mismatch"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)


def test_manifest_signature_verified(tmp_path, monkeypatch):
    """A PQ signature over the manifest digest is verified against a trusted pubkey."""
    import importlib

    alg = importlib.import_module("pq.py.algs.dilithium3")  # fast pure-python fallback
    alg_id = 4097
    pub, sec = alg.generate_keypair()

    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    digest = _compute_manifest_digest(_manifest_obj_from_data(data))
    sig = alg.sign(sec, digest.encode("utf-8"))
    data["signature"] = {"alg_id": alg_id, "sig": "0x" + sig.hex()}
    _write_manifest_json(snapdir, data)

    monkeypatch.setenv("ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS", "0x" + pub.hex())
    monkeypatch.setenv("ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE", "1")

    bdb, sdb = _fresh_dst()
    m = import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert m.checkpoint_height == 0


def test_manifest_signature_required_but_missing_rejected(tmp_path, monkeypatch):
    """require_signature with a trusted key but no signature in the manifest -> abort."""
    import importlib

    alg = importlib.import_module("pq.py.algs.dilithium3")
    pub, _sec = alg.generate_keypair()
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)  # no signature written
    monkeypatch.setenv("ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS", "0x" + pub.hex())
    monkeypatch.setenv("ANIMICA_SNAPSHOT_REQUIRE_SIGNATURE", "1")

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="signature required but missing"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)


def test_manifest_signature_wrong_key_rejected(tmp_path, monkeypatch):
    """A signature that does not match the configured trusted key is refused."""
    import importlib

    alg = importlib.import_module("pq.py.algs.dilithium3")
    alg_id = 4097
    signer_pub, signer_sec = alg.generate_keypair()
    other_pub, _ = alg.generate_keypair()

    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    digest = _compute_manifest_digest(_manifest_obj_from_data(data))
    sig = alg.sign(signer_sec, digest.encode("utf-8"))
    data["signature"] = {"alg_id": alg_id, "sig": "0x" + sig.hex()}
    _write_manifest_json(snapdir, data)
    # trust a DIFFERENT key than the one that signed
    monkeypatch.setenv("ANIMICA_SNAPSHOT_TRUSTED_PUBKEYS", "0x" + other_pub.hex())

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="did not match any trusted pubkey"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)


# --------------------------------------------------------------- resource caps (H02/H06)
def test_chunk_count_cap(tmp_path, monkeypatch):
    """A manifest that declares more chunks than the cap is refused before any I/O."""
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    # duplicate chunk entries so there are >1
    data["chunks"] = data["chunks"] + data["chunks"]
    _write_manifest_json(snapdir, data)
    monkeypatch.setenv("ANIMICA_SNAPSHOT_MAX_CHUNKS", "1")

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="exceeds the cap"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert bdb.get_head() is None


def test_gzip_bomb_decompressed_cap(tmp_path, monkeypatch):
    """A chunk that decompresses beyond the per-chunk cap is refused (OOM guard)."""
    import hashlib

    snapdir = tmp_path / "snap"
    snapdir.mkdir()
    # small on disk, large when inflated
    payload = b"A" * (2 * 1024 * 1024)
    chunk_name = "state-00000.cbor.gz"
    (snapdir / chunk_name).write_bytes(gzip.compress(payload))
    file_hash = "0x" + hashlib.sha256((snapdir / chunk_name).read_bytes()).hexdigest()

    data = {
        "version": 2, "chain_id": 1, "network": "devnet",
        "checkpoint_height": 0, "checkpoint_hash": "0x" + "00" * 32,
        "timestamp": 0, "created_at": "1970-01-01T00:00:00Z",
        "blocks_count": 0, "headers_count": 0, "accounts_count": 0,
        "storage_keys_count": 0, "code_contracts_count": 0, "compressed": True,
        "chunks": [{"name": chunk_name, "type": "state", "size": (snapdir / chunk_name).stat().st_size,
                    "sha256": file_hash, "index": 0}],
    }
    _write_manifest_json(snapdir, data)
    monkeypatch.setenv("ANIMICA_SNAPSHOT_MAX_CHUNK_DECOMPRESSED_BYTES", "4096")

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="decompression bomb|decompressed-size cap"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert bdb.get_head() is None


def test_oversized_chunk_file_cap(tmp_path, monkeypatch):
    """An on-disk chunk larger than the file cap is refused before it is read."""
    import hashlib

    snapdir = tmp_path / "snap"
    snapdir.mkdir()
    chunk_name = "state-00000.cbor.gz"
    (snapdir / chunk_name).write_bytes(gzip.compress(b"B" * 20000))
    file_hash = "0x" + hashlib.sha256((snapdir / chunk_name).read_bytes()).hexdigest()
    data = {
        "version": 2, "chain_id": 1, "network": "devnet",
        "checkpoint_height": 0, "checkpoint_hash": "0x" + "00" * 32,
        "timestamp": 0, "created_at": "x", "blocks_count": 0, "headers_count": 0,
        "accounts_count": 0, "storage_keys_count": 0, "code_contracts_count": 0,
        "compressed": True,
        "chunks": [{"name": chunk_name, "type": "state",
                    "size": (snapdir / chunk_name).stat().st_size, "sha256": file_hash, "index": 0}],
    }
    _write_manifest_json(snapdir, data)
    monkeypatch.setenv("ANIMICA_SNAPSHOT_MAX_CHUNK_BYTES", "16")  # tiny cap

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="exceeds cap"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)


# --------------------------------------------------------------- path traversal (M03)
@pytest.mark.parametrize(
    "bad",
    ["../evil", "../../etc/passwd", "/etc/passwd", "a/b", "..\\evil", "..", ".", "", "x\x00y"],
)
def test_safe_chunk_name_rejects_traversal(bad):
    with pytest.raises(ValueError, match="[Uu]nsafe snapshot chunk name"):
        _safe_chunk_name(bad)


def test_safe_chunk_name_accepts_plain():
    assert _safe_chunk_name("state-00001.cbor.gz") == "state-00001.cbor.gz"
    assert _safe_chunk_name("blocks-00000.cbor") == "blocks-00000.cbor"


def test_import_rejects_traversal_chunk_name(tmp_path, monkeypatch):
    """A manifest whose chunk name escapes the snapshot dir is refused."""
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    # plant a file OUTSIDE the snapshot dir that a traversal name would point at
    (tmp_path / "secret.bin").write_bytes(b"top-secret")
    data = _load_manifest_json(snapdir)
    data["chunks"][0]["name"] = "../secret.bin"
    _write_manifest_json(snapdir, data)

    bdb, sdb = _fresh_dst()
    with pytest.raises(ValueError, match="[Uu]nsafe snapshot chunk name"):
        import_snapshot(block_db=bdb, state_db=sdb, snapshot_dir=snapdir, verify_hashes=True)
    assert bdb.get_head() is None


def test_verify_snapshot_reports_traversal(tmp_path):
    snapdir = tmp_path / "snap"
    _build_snapshot(snapdir)
    data = _load_manifest_json(snapdir)
    data["chunks"][0]["name"] = "../evil"
    _write_manifest_json(snapdir, data)
    ok, errors = verify_snapshot(snapdir)
    assert not ok
    assert any("nsafe snapshot chunk name" in e for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
