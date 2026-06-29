"""qDNA — Quantum-Sealed Genome Ledger tests (all GPU-free / model-free)."""

from __future__ import annotations

import json

import pytest

from animica.ena import genome as g

_BEACON = {"beacon_value_hex": "ab" * 32, "beacon_round": 9, "attested": False}


def test_gene_id_is_content_address():
    a = g.derive_gene_id("What is ANM?", "The native token.")
    assert a == g.derive_gene_id("What is ANM?", "The native token.")          # stable
    assert a != g.derive_gene_id("What is ANM?", "A different answer.")          # content-bound
    assert a != g.derive_gene_id("What is ANM?", "The native token.", kind="dpo")
    assert len(bytes.fromhex(a)) == 32


def test_seal_verify_and_tamper():
    gid = g.derive_gene_id("q", "a")
    gene = {"gene_id": gid, "kind": "sft", "prompt": "q", "response": "a",
            "seal": g.seal_gene(gid, _BEACON["beacon_value_hex"], 9, False)}
    assert g.verify_gene_seal(gene) is True
    # tamper the response -> gene_id mismatch -> seal invalid
    bad = dict(gene); bad["response"] = "a-tampered"
    assert g.verify_gene_seal(bad) is False
    # forge the seed -> seal invalid
    bad2 = json.loads(json.dumps(gene)); bad2["seal"]["seed_hex"] = "00" * 32
    assert g.verify_gene_seal(bad2) is False


def test_merkle_root_order_independent():
    genes = []
    for i in range(4):
        gid = g.derive_gene_id(f"q{i}", f"a{i}")
        genes.append({"gene_id": gid, "kind": "sft", "prompt": f"q{i}",
                      "response": f"a{i}", "seal": g.seal_gene(gid, _BEACON["beacon_value_hex"], 9, False)})
    r1 = g.gene_merkle_root(genes)
    r2 = g.gene_merkle_root(list(reversed(genes)))
    assert r1 == r2 and r1 != g._ZERO


def test_record_grows_epochs_and_chains(tmp_path):
    led = g.GenomeLedger(tmp_path / "qdna")
    r0 = led.record([{"prompt": "q1", "response": "a1"}, {"prompt": "q2", "response": "a2"}],
                    beacon=_BEACON)
    assert r0["epoch"] == 0 and r0["added"] == 2 and r0["genome_root"] != g._ZERO
    # idempotent: same pairs add nothing, no new epoch
    again = led.record([{"prompt": "q1", "response": "a1"}], beacon=_BEACON)
    assert again["added"] == 0 and again["epoch"] == 0
    # second batch -> epoch 1, chained on epoch 0's root
    r1 = led.record([{"prompt": "q3", "response": "a3"}], beacon=_BEACON)
    assert r1["epoch"] == 1 and r1["added"] == 1
    assert r1["prev_genome_root"] == r0["genome_root"] and r1["genome_root"] != r0["genome_root"]
    assert led.verify()["valid"] is True


def test_verify_detects_tampered_gene_file(tmp_path):
    led = g.GenomeLedger(tmp_path / "qdna")
    led.record([{"prompt": "q1", "response": "a1"}], beacon=_BEACON)
    assert led.verify()["valid"] is True
    # edit a gene's response on disk without re-sealing -> audit fails
    genes = led.genes_path.read_text().splitlines()
    rec = json.loads(genes[0]); rec["response"] = "secretly changed"
    led.genes_path.write_text(json.dumps(rec) + "\n")
    v = led.verify()
    assert v["valid"] is False
    assert any(c["check"] == "gene_seals" and not c["passed"] for c in v["checks"])


def test_lineage_and_anchor(tmp_path):
    led = g.GenomeLedger(tmp_path / "qdna")
    led.record([{"prompt": "root", "response": "r"}], beacon=_BEACON)
    root_id = led.genes()[0]["gene_id"]
    led.record([{"prompt": "child", "response": "c", "parent_genes": [root_id]}], beacon=_BEACON)
    child_id = next(x["gene_id"] for x in led.genes() if x["prompt"] == "child")
    lin = led.lineage(child_id)
    assert child_id in lin and root_id in lin
    env = led.anchor_envelope()
    assert env["schema"] == "ena.genome.v1" and env["onchain"]["epoch"] == 1
    assert env["onchain"]["genome_root"] == led.head()["genome_root"]


def test_record_for_genome_resolves_pseudo_beacon(tmp_path, monkeypatch):
    monkeypatch.delenv("ANIMICA_ENA_BEACON_SEED_HEX", raising=False)
    gp = tmp_path / "pool" / "animica-knowledge.jsonl"
    gp.parent.mkdir(parents=True)
    gp.write_text('{"prompt":"x","response":"y"}\n')   # the genome stays untouched
    res = g.record_for_genome(gp, [{"prompt": "q", "response": "a"}])   # no beacon -> pseudo
    assert res["added"] == 1 and res["genome_root"] != g._ZERO
    # ledger lives in a sidecar, the genome file is unchanged
    assert gp.read_text() == '{"prompt":"x","response":"y"}\n'
    assert (gp.parent / "qdna" / "animica-knowledge" / "genes.jsonl").is_file()
    assert g.GenomeLedger.for_genome(gp).verify()["valid"] is True


def test_qdna_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ANIMICA_ENA_QDNA", raising=False)
    assert g.qdna_enabled() is False
    monkeypatch.setenv("ANIMICA_ENA_QDNA", "1")
    assert g.qdna_enabled() is True
