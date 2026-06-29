"""Tests for the worker-local LLM synthesis flywheel.

Covers: synth parsing, the coordinator quality gate, staging + gated genome
growth, the synthesize_qa job lifecycle, the coordinator run-guard (no model on
the coordinator in-request), the inline submit_result landing, and the worker's
worker-local routing. All model-free (a FakeModel stands in for the miner's
local GPU/ollama), so it runs in a base install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from animica.ena import ENA
from animica.ena import datasets as ds
from animica.ena import curation, synth
from animica.ena import jobs as jobmod
from animica.ena.config import load_config
from animica.ena.errors import JobError
from animica.ena.models import ModelProviderConfig
from animica.ena.worker import ENAWorker

CORPUS = ("Animica seeds its randomness beacon with quantum entropy drawn from "
          "IDQ Quantis hardware, attested by a YubiHSM2 security module.")


class FakeModel:
    """Deterministic stand-in for a miner's local model: emits grounded JSONL."""

    cfg = ModelProviderConfig(name="fake", provider="fake-local", model="fake-1")

    def generate(self, prompt, *, system=None, history=None,
                 max_tokens=None, temperature=None):
        return (
            '{"prompt": "What hardware supplies Animica\'s quantum entropy?", '
            '"response": "Animica draws quantum entropy from IDQ Quantis hardware."}\n'
            '{"prompt": "What attests the entropy source?", '
            '"response": "A YubiHSM2 security module attests the quantum entropy."}\n'
            'not json — should be skipped\n'
            '{"prompt": "ok", "response": "tiny"}\n'  # too short -> gate rejects
        )


@pytest.fixture()
def ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    e = ENA(cfg=load_config())
    e.jobs.model = lambda provider=None: FakeModel()  # type: ignore[assignment]
    return e


# -- synth -------------------------------------------------------------------

def test_parse_pairs_tolerant():
    pairs = synth.parse_pairs('```json\n{"prompt":"a","response":"b"}\n```')
    assert pairs == [{"prompt": "a", "response": "b"}]
    assert synth.parse_pairs("garbage") == []


# -- synthesize-under-round (quantum/pseudo-quantum seal) --------------------

_BEACON = {"seed_hex": "ab" * 32, "round": 7, "attested": False}


class SeedModel(FakeModel):
    """Local model that DOES accept a sampler seed (records what it got)."""
    last_seed = None

    def generate(self, prompt, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None):
        SeedModel.last_seed = seed
        return FakeModel.generate(self, prompt, max_tokens=max_tokens, temperature=temperature)


def test_synthesize_under_round_seals_and_is_reproducible():
    from aicf.integration import quantum_seed as _qs
    pairs, meta = synth.synthesize(FakeModel(), CORPUS, beacon=_BEACON, bind_id="job-x")
    assert pairs  # still produces pairs
    seal = meta["provenance"]["quantum_seed"]
    assert seal["beacon_round"] == 7 and seal["attested"] is False
    assert seal["kind"] == "synthesized_under_round"
    assert meta["synthesized_under_round"] == 7
    # the seal is reproducible from the PUBLIC round + job_id => anyone can verify
    qs = _qs.quantum_seed_for(beacon_seed=bytes.fromhex(_BEACON["seed_hex"]),
                              beacon_round=7, job_id=seal["job_id"], attested=False)
    assert qs.seed_hex == seal["seed_hex"]
    assert _qs.verify_quantum_seed(qs, beacon_seed=bytes.fromhex(_BEACON["seed_hex"]))
    # deterministic: same round + corpus + bind => same seal
    _, meta2 = synth.synthesize(FakeModel(), CORPUS, beacon=_BEACON, bind_id="job-x")
    assert meta2["provenance"]["quantum_seed"]["seed_hex"] == seal["seed_hex"]


def test_synthesize_forwards_sampler_seed_when_supported():
    SeedModel.last_seed = None
    _, meta = synth.synthesize(SeedModel(), CORPUS, beacon=_BEACON, bind_id="job-y")
    assert SeedModel.last_seed is not None          # the quantum seed reached the sampler
    assert meta["provenance"]["quantum_seed"]["model_seed_forwarded"] is True


def test_synthesize_without_beacon_is_unsealed_backward_compatible():
    pairs, meta = synth.synthesize(FakeModel(), CORPUS)  # no beacon, no env
    assert pairs
    assert "quantum_seed" not in meta["provenance"]
    assert meta["synthesized_under_round"] is None


def test_resolve_beacon_from_env(monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_BEACON_SEED_HEX", "cd" * 32)
    monkeypatch.setenv("ANIMICA_ENA_BEACON_ROUND", "12")
    rb = synth.resolve_beacon(None)
    assert rb == {"seed_hex": "cd" * 32, "round": 12, "attested": False}
    monkeypatch.delenv("ANIMICA_ENA_BEACON_SEED_HEX")
    assert synth.resolve_beacon(None) is None


def test_synthesize_emits_pairs_and_meta():
    pairs, meta = synth.synthesize(FakeModel(), CORPUS, num_pairs=3)
    assert len(pairs) == 3  # two grounded + one tiny (gate filters tiny later)
    assert meta["provenance"]["kind"] == "worker_local_synthesis"
    assert meta["pairs_parsed"] == 3


# -- gate --------------------------------------------------------------------

def test_quality_gate_accepts_grounded_and_rejects_junk():
    good = {"prompt": "What hardware supplies the entropy?",
            "response": "Animica draws quantum entropy from IDQ Quantis hardware."}
    g = curation.quality_gate(good, source_chunk=CORPUS)
    assert g["valid"] and g["dedupe_key"]

    short = curation.quality_gate({"prompt": "ok", "response": "no"}, source_chunk=CORPUS)
    assert not short["valid"]

    ungrounded = curation.quality_gate(
        {"prompt": "Totally unrelated question about cats?",
         "response": "Cats are furry mammals that enjoy sleeping in sunbeams."},
        source_chunk=CORPUS)
    assert not ungrounded["valid"]  # zero overlap with the source


# -- staging + promotion -----------------------------------------------------

def test_stage_then_promote_grows_genome(ena, tmp_path):
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS, "num_pairs": 3})
    job = ena.jobs.run(job["job_id"], allow_worker_local=True)
    assert job["status"] == "completed"
    assert job["result"]["pair_count"] == 3

    summary = curation.stage_submission(ena.cfg, job)
    assert summary["staged"] == 2  # two grounded survive; the tiny one is rejected
    assert summary["rejected"] >= 1

    # Re-staging the same job is idempotent (content-hash dedupe).
    again = curation.stage_submission(ena.cfg, job)
    assert again["staged"] == 0 and again["duplicates"] == 2

    # Seed a live genome that already contains ONE of the two pairs (verbatim,
    # so the content-hash dedupe recognizes it).
    genome = tmp_path / "genome.jsonl"
    ds.write_jsonl(genome, [{
        "prompt": "What hardware supplies Animica's quantum entropy?",
        "response": "Animica draws quantum entropy from IDQ Quantis hardware."}])
    before = ds.row_count(genome)

    promo = curation.promote_staging(ena.cfg, genome, max_rows=10)
    assert promo["promoted"] == 1            # the duplicate is dropped, 1 novel grows
    assert promo["dataset_rows"] == before + 1
    assert promo["validation"]["valid"]
    assert ds.sha256_file(genome) == promo["dataset_sha256"]

    # Staging is drained of the promoted row.
    drained = curation.promote_staging(ena.cfg, genome, max_rows=10)
    assert drained["promoted"] == 0


def test_promote_backs_up_genome(ena, tmp_path):
    """GAP 1 (HARD-4 reversibility): promote_staging snapshots the live genome to
    a timestamped .bak BEFORE appending, and records it in the summary."""
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS, "num_pairs": 3})
    job = ena.jobs.run(job["job_id"], allow_worker_local=True)
    curation.stage_submission(ena.cfg, job)

    genome = tmp_path / "genome.jsonl"
    ds.write_jsonl(genome, [
        {"prompt": "An unrelated seed question about gardening soil?",
         "response": "Healthy garden soil needs nutrients, drainage and aeration."}])
    pre = genome.read_text(encoding="utf-8")

    promo = curation.promote_staging(ena.cfg, genome, max_rows=10)
    assert promo["promoted"] == 2  # both grounded pairs are novel vs the seed
    assert "backup_path" in promo
    bak = Path(promo["backup_path"])
    assert bak.is_file()
    # The backup is the exact pre-promote genome (so the append is reversible).
    assert bak.read_text(encoding="utf-8") == pre
    # And the genome itself actually grew past the backup.
    assert ds.row_count(genome) == 3

    # A dry-run / empty promote writes no new backup.
    again = curation.promote_staging(ena.cfg, genome, max_rows=10)
    assert again["promoted"] == 0
    assert "backup_path" not in again


def test_pool_promote_genome_grows_dataset(ena, tmp_path):
    """GAP 2: PoolService.promote_genome grows the pool's LIVE dataset from
    STAGING, updating dataset_sha256/rows while keeping dataset_id + path stable."""
    genome = tmp_path / "live.jsonl"
    ds.write_jsonl(genome, [
        {"prompt": f"Seed question {i} about gardening soil?",
         "response": f"Healthy soil needs nutrients and drainage, note {i}."}
        for i in range(3)])
    pool = ena.pool.create("tiny-local-model", str(genome), method="lora",
                           name="genome-test")
    pid = pool["pool_id"]
    dataset_id_before = pool["dataset_id"]
    dataset_path_before = pool["dataset_path"]
    rows_before = pool["dataset_rows"]

    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS, "num_pairs": 3})
    job = ena.jobs.run(job["job_id"], allow_worker_local=True)
    curation.stage_submission(ena.cfg, job)

    # Dry-run previews without mutating the pool record.
    preview = ena.pool.promote_genome(pid, max_rows=10, dry_run=True)
    assert preview["promotable"] == 2 and preview["promoted"] == 0
    assert ena.pool.get(pid)["dataset_rows"] == rows_before

    summary = ena.pool.promote_genome(pid, max_rows=10)
    assert summary["promoted"] == 2
    assert summary["pool_id"] == pid
    assert summary["dataset_id"] == dataset_id_before

    fresh = ena.pool.get(pid)
    assert fresh["dataset_id"] == dataset_id_before          # stable (orig ingest)
    assert fresh["dataset_path"] == dataset_path_before      # stable
    assert fresh["dataset_rows"] == rows_before + 2
    assert fresh["dataset_sha256"] == summary["dataset_sha256"]
    assert fresh["dataset_sha256"] == ds.sha256_file(Path(dataset_path_before))


def test_synthesis_targets_weakest_first(ena, tmp_path):
    """PART C (Helix): synthesis_targets returns weakest-first topics, each with a
    grounding corpus slice + source_sha, model-free."""
    corpus_file = tmp_path / "corpus.jsonl"
    ds.write_jsonl(corpus_file, [
        {"prompt": "What is entropy?",
         "response": "Entropy is randomness drawn from quantum hardware."},
        {"prompt": "What is the beacon?",
         "response": "The beacon publishes randomness on chain."},
        {"prompt": "What is consensus?",
         "response": "Consensus is agreement among the nodes."}])
    pool = {
        "pool_id": "enapool-helix",
        "dataset_path": str(corpus_file),
        "metadata": {
            "curriculum": {"topics_seed": ["entropy", "beacon", "consensus"]},
            "topic_stats": {
                "entropy": {"last_rate": 0.2, "mastered": False},
                "beacon": {"last_rate": 0.9, "mastered": False},
                "consensus": {"last_rate": 0.5, "mastered": False},
            },
        },
    }
    targets = ena.curriculum.synthesis_targets(pool, max_targets=3)
    assert targets, "expected at least one target"
    topics = [t["topic"] for t in targets]
    assert topics[0] == "entropy"  # weakest (0.2) first
    assert topics.index("consensus") < topics.index("beacon")  # 0.5 before 0.9
    for t in targets:
        assert t["corpus"] and t["source_sha"]

    # A mastered topic is skipped (studies the actual weak spots).
    pool["metadata"]["topic_stats"]["entropy"]["mastered"] = True
    masked = [t["topic"] for t in ena.curriculum.synthesis_targets(pool)]
    assert "entropy" not in masked


def test_promote_is_bounded(ena, tmp_path):
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS, "num_pairs": 3})
    job = ena.jobs.run(job["job_id"], allow_worker_local=True)
    curation.stage_submission(ena.cfg, job)
    genome = tmp_path / "g.jsonl"
    ds.write_jsonl(genome, [])
    promo = curation.promote_staging(ena.cfg, genome, max_rows=1)
    assert promo["promoted"] == 1 and promo["promotable"] == 1


# -- lifecycle + receipt -----------------------------------------------------

def test_synthesize_qa_receipt_and_export(ena):
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS})
    ena.jobs.run(job["job_id"], allow_worker_local=True)
    job = ena.jobs.verify(job["job_id"])
    assert job["status"] == "verified"
    receipt = ena.jobs.receipt(job["job_id"])
    assert receipt["aicf_job_kind"] == "AI"
    env = ena.jobs.export_onchain(job["job_id"])
    assert env["validation"]["valid"]


# -- HARD CONSTRAINT 1: coordinator must NOT execute the model in-request -----

def test_coordinator_run_refuses_worker_local(ena):
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS})
    with pytest.raises(JobError):
        ena.jobs.run(job["job_id"])  # no allow_worker_local -> refused
    # The refusal leaves the job untouched (no half-run side effects).
    assert ena.jobs.get(job["job_id"])["status"] == "proposed"


# -- inline submit_result auto-stages ----------------------------------------

def test_submit_result_stages(ena):
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS})
    result = {"pairs": [
        {"prompt": "What hardware supplies the entropy?",
         "response": "Animica draws quantum entropy from IDQ Quantis hardware."}],
        "pair_count": 1, "provenance": {"kind": "worker_local_synthesis"}}
    out = ena.jobs.submit_result(job["job_id"], result=result, worker_id="w1")
    assert out["status"] == "submitted"
    assert out["worker_id"] == "w1"
    assert out["curation"]["staged"] == 1


# -- worker routes worker-local jobs to local exec ---------------------------

def test_worker_local_processes_synthesize(ena):
    job = ena.jobs.create("synthesize_qa", {"corpus": CORPUS})
    worker = ENAWorker(worker_id="w-local", ena=ena, types=["synthesize_qa"],
                       poll_interval=0.0)
    env = worker.tick()
    assert env is not None and env["validation"]["valid"]
    assert worker.stats.jobs_completed == 1

    # The single-box worker path also auto-stages: synthesized pairs land in the
    # STAGING dataset (genome growth), not just the job result.
    done = ena.jobs.get(job["job_id"])
    assert done["status"] == "verified"
    assert done.get("curation", {}).get("staged") == 2
    staging = curation.staging_dir(ena.cfg) / curation.STAGING_FILE
    assert ds.row_count(staging) == 2
