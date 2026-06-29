"""
animica.ena.genome — qDNA: the Quantum-Sealed Genome Ledger.

A sidecar, append-only, content-addressed provenance ledger for ENA's training
genome. Each *gene* (a ``{prompt, response}`` pair that enters the genome) is:

* **content-addressed** — ``gene_id = SHA3(_GENE_ID_TAG || canonical{kind,prompt,response})``
* **quantum-sealed** — a ``QuantumSeed`` derived from the beacon round it was
  synthesized/promoted under (EXTENDS :mod:`aicf.integration.quantum_seed`,
  binding the public round to the exact gene via ``job_id='ena/gene/<id>'``).
* **provenance-stamped** — synthesizing miner, source, quality score, parents.
* **Merkle-anchored** — genes roll into a per-epoch ``gene_merkle_root`` (the
  chain's canonical KV-Merkle, :mod:`core.utils.merkle`), and epochs chain into a
  prev-root-linked ``genome_root``.

The training genome JSONL (``Pool.dataset_path``) is **never touched** — qDNA
writes ``genes.jsonl`` / ``epochs.jsonl`` sidecars next to it, so every existing
dataset/trainer reader is unaffected, and the whole ledger is **OFF** unless
``ANIMICA_ENA_QDNA`` is set (flag-off ⇒ byte-identical genome, zero regression).

**Tamper-EVIDENT:** anyone can recompute ``gene_id``, re-derive the quantum seal
from the public round, recompute the merkle root + genome_root chain, and detect
any edit. (Tamper-PROOF — on-chain anchoring of the root + ZK membership — is the
documented roadmap, deliberately kept out of the 5.1.0 core.)
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from .models import canonical_bytes, sha3_hex, now_ts

_GENE_ID_TAG = b"animica/ena.gene_id/v1"
_GENOME_DS = b"animica/ena/genome-root/v1"
_ZERO = "00" * 32


def qdna_enabled() -> bool:
    """qDNA is opt-in: nothing is written unless ANIMICA_ENA_QDNA is truthy."""
    return os.environ.get("ANIMICA_ENA_QDNA", "").strip().lower() in ("1", "true", "yes", "on")


# --- gene identity + quantum seal ------------------------------------------

def derive_gene_id(prompt: Any, response: Any, kind: str = "sft") -> str:
    """Stable content address of a training pair (independent of the lossy
    dataset dedupe key — this is the permanent ledger identity + Merkle key)."""
    return sha3_hex(_GENE_ID_TAG + canonical_bytes(
        {"kind": str(kind), "prompt": str(prompt), "response": str(response)}))


def seal_gene(gene_id: str, beacon_value_hex: str, beacon_round: int,
              attested: bool = False) -> dict[str, Any]:
    """Bind a gene to a beacon round via the EXISTING quantum-seed derivation
    (``job_id='ena/gene/<gene_id>'`` threads the content address into the seed).
    Returns a verifiable seal dict."""
    from aicf.integration import quantum_seed as _qs
    qs = _qs.quantum_seed_for(
        beacon_seed=bytes.fromhex(beacon_value_hex),
        beacon_round=int(beacon_round),
        job_id=f"ena/gene/{gene_id}",
        attested=bool(attested),
    )
    return {
        "seed_hex": qs.seed_hex,
        "beacon_round": int(beacon_round),
        "beacon_value_hex": beacon_value_hex,
        "attested": bool(attested),
        "job_id": qs.job_id,
        "domain": getattr(qs, "domain", None),
    }


def verify_gene_seal(gene: dict[str, Any]) -> bool:
    """Recompute the gene_id and re-derive the seal from the PUBLIC beacon value;
    True iff both match (so a doctored prompt/response or forged seal is caught)."""
    gid = derive_gene_id(gene.get("prompt"), gene.get("response"), gene.get("kind", "sft"))
    if gid != gene.get("gene_id"):
        return False
    seal = gene.get("seal") or {}
    bv = seal.get("beacon_value_hex")
    if not bv:
        return False
    try:
        expect = seal_gene(gid, bv, int(seal.get("beacon_round", 0)), bool(seal.get("attested", False)))
    except Exception:  # noqa: BLE001
        return False
    return expect["seed_hex"] == seal.get("seed_hex") and expect["job_id"] == seal.get("job_id")


# --- merkle + genome-root chain --------------------------------------------

# Immutable gene fields that define the Merkle leaf value (epoch/root metadata
# lives in the epoch record, never on the gene, so there is no circular hash).
_LEAF_FIELDS = ("gene_id", "kind", "prompt", "response", "seal", "provenance", "parent_genes")


def gene_record_hash(gene: dict[str, Any]) -> bytes:
    core = {k: gene.get(k) for k in _LEAF_FIELDS}
    return bytes.fromhex(sha3_hex(canonical_bytes(core)))


def gene_merkle_root(genes: list[dict[str, Any]]) -> str:
    """Canonical KV-Merkle root over {gene_id -> gene_record_hash} using the same
    construction the chain uses (core.utils.merkle), so the root is on-chain ready."""
    if not genes:
        return _ZERO
    from core.utils.merkle import kv_merkle_root
    items = {bytes.fromhex(g["gene_id"]): gene_record_hash(g) for g in genes}
    return kv_merkle_root(items).hex()


def chain_genome_root(epoch: int, prev_root_hex: str, gene_merkle_root_hex: str) -> str:
    h = hashlib.sha3_256()
    h.update(_GENOME_DS)
    h.update(int(epoch).to_bytes(8, "big"))
    h.update(bytes.fromhex(prev_root_hex or _ZERO))
    h.update(bytes.fromhex(gene_merkle_root_hex))
    return h.hexdigest()


# --- the ledger -------------------------------------------------------------

class GenomeLedger:
    """Append-only sidecar ledger (genes.jsonl + epochs.jsonl) for one genome."""

    def __init__(self, ledger_dir: str | Path):
        self.dir = Path(ledger_dir)
        self.genes_path = self.dir / "genes.jsonl"
        self.epochs_path = self.dir / "epochs.jsonl"

    @classmethod
    def for_genome(cls, genome_path: str | Path) -> "GenomeLedger":
        """The ledger that sits beside a pool's training dataset file."""
        gp = Path(genome_path)
        return cls(gp.parent / "qdna" / gp.stem)

    def _read(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def genes(self) -> list[dict[str, Any]]:
        return self._read(self.genes_path)

    def epochs(self) -> list[dict[str, Any]]:
        return self._read(self.epochs_path)

    def head(self) -> Optional[dict[str, Any]]:
        eps = self.epochs()
        return eps[-1] if eps else None

    def get_gene(self, gene_id: str) -> Optional[dict[str, Any]]:
        for g in self.genes():
            if g.get("gene_id") == gene_id:
                return g
        return None

    def lineage(self, gene_id: str) -> list[str]:
        """Walk parent_genes to the founders (best-effort; cycle-safe)."""
        by_id = {g["gene_id"]: g for g in self.genes()}
        out, seen, stack = [], set(), [gene_id]
        while stack:
            gid = stack.pop()
            if gid in seen:
                continue
            seen.add(gid)
            out.append(gid)
            g = by_id.get(gid)
            if g:
                stack.extend(g.get("parent_genes") or [])
        return out

    def record(self, pairs: list[dict[str, Any]], *, beacon: dict[str, Any],
               provenance_by_key: Optional[dict[str, Any]] = None,
               kind: str = "sft") -> dict[str, Any]:
        """Seal the NEW pairs (skipping gene_ids already in the ledger), append
        them, and commit a new epoch (recompute gene_merkle_root + chain the
        genome_root). Returns {epoch, genome_root, prev_genome_root,
        gene_merkle_root, gene_ids, added}. Idempotent: re-recording the same
        pairs adds nothing and still advances no epoch when added==0."""
        self.dir.mkdir(parents=True, exist_ok=True)
        existing = self.genes()
        have = {g["gene_id"] for g in existing}
        bv = beacon["beacon_value_hex"] if "beacon_value_hex" in beacon else beacon.get("seed_hex") or beacon.get("value")
        brnd = int(beacon.get("beacon_round", beacon.get("round", 0)))
        att = bool(beacon.get("attested", False))
        prov_map = provenance_by_key or {}

        new_genes: list[dict[str, Any]] = []
        for p in pairs:
            prompt = p.get("prompt"); response = p.get("response")
            if not prompt or not response:
                continue
            gid = derive_gene_id(prompt, response, kind)
            if gid in have:
                continue
            have.add(gid)
            prov = dict(prov_map.get(gid) or prov_map.get(p.get("_key") or "") or {})
            prov.setdefault("source", p.get("source"))
            new_genes.append({
                "gene_id": gid, "kind": kind,
                "prompt": str(prompt), "response": str(response),
                "seal": seal_gene(gid, bv, brnd, att),
                "provenance": prov,
                "parent_genes": list(p.get("parent_genes") or []),
                "status": "promoted",
                "created_at": now_ts(),
            })

        if not new_genes:
            head = self.head()
            return {"epoch": (head or {}).get("epoch", -1), "added": 0,
                    "genome_root": (head or {}).get("genome_root", _ZERO),
                    "prev_genome_root": (head or {}).get("prev_genome_root", _ZERO),
                    "gene_merkle_root": (head or {}).get("gene_merkle_root", _ZERO),
                    "gene_ids": []}

        with self.genes_path.open("a", encoding="utf-8") as fh:
            for g in new_genes:
                fh.write(json.dumps(g, sort_keys=True) + "\n")

        all_genes = existing + new_genes
        head = self.head()
        epoch = (head["epoch"] + 1) if head else 0
        prev_root = head["genome_root"] if head else _ZERO
        gmr = gene_merkle_root(all_genes)
        groot = chain_genome_root(epoch, prev_root, gmr)
        epoch_rec = {
            "epoch": epoch, "genome_root": groot, "prev_genome_root": prev_root,
            "gene_merkle_root": gmr, "gene_count": len(all_genes),
            "added_gene_ids": [g["gene_id"] for g in new_genes],
            "beacon_round": brnd, "beacon_value_hex": bv, "attested": att,
            "created_at": now_ts(),
        }
        with self.epochs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(epoch_rec, sort_keys=True) + "\n")
        return {"epoch": epoch, "added": len(new_genes), "genome_root": groot,
                "prev_genome_root": prev_root, "gene_merkle_root": gmr,
                "gene_ids": [g["gene_id"] for g in new_genes]}

    def verify(self) -> dict[str, Any]:
        """Full ledger audit: every gene seal valid, every epoch's merkle root +
        chained genome_root recomputes, and the prev-root chain is unbroken."""
        genes = self.genes()
        checks: list[dict[str, Any]] = []
        ok = True
        bad_seals = [g["gene_id"] for g in genes if not verify_gene_seal(g)]
        checks.append({"check": "gene_seals", "passed": not bad_seals,
                       "bad": bad_seals[:10], "n": len(genes)})
        ok = ok and not bad_seals
        added_at: dict[str, int] = {}
        for e in self.epochs():
            for gid in (e.get("added_gene_ids") or []):
                added_at.setdefault(gid, e["epoch"])
        prev = _ZERO
        for ep in self.epochs():
            cum = [g for g in genes if added_at.get(g["gene_id"], 1 << 62) <= ep["epoch"]]
            gmr = gene_merkle_root(cum)
            groot = chain_genome_root(ep["epoch"], prev, gmr)
            good = (gmr == ep.get("gene_merkle_root")
                    and groot == ep.get("genome_root")
                    and ep.get("prev_genome_root") == prev)
            checks.append({"check": f"epoch_{ep['epoch']}", "passed": good})
            ok = ok and good
            prev = ep.get("genome_root")
        return {"valid": ok, "checks": checks,
                "epochs": len(self.epochs()), "genes": len(genes)}

    def anchor_envelope(self) -> Optional[dict[str, Any]]:
        """A local, chain-ready JSON commitment of the current head (no RPC).
        Routing this through the AICF export rail / a contract = roadmap."""
        head = self.head()
        if not head:
            return None
        onchain = {k: head[k] for k in ("epoch", "genome_root", "prev_genome_root",
                                        "gene_merkle_root", "gene_count",
                                        "beacon_round", "beacon_value_hex", "attested")}
        return {"schema": "ena.genome.v1", "onchain": onchain,
                "credit_event": {"kind": "aicf.ena.genome.epoch",
                                 "event_hash": sha3_hex(canonical_bytes(onchain))},
                "exported_at": now_ts()}


def record_for_genome(genome_path: str | Path, pairs: list[dict[str, Any]], *,
                      beacon: Optional[dict[str, Any]] = None,
                      provenance_by_key: Optional[dict[str, Any]] = None,
                      kind: str = "sft") -> dict[str, Any]:
    """Seal+record promoted pairs into the genome's sidecar ledger. Resolves a
    beacon (passed → env → software pseudo-quantum) so a seal is ALWAYS recorded.
    Best-effort: callers wrap this so qDNA can never break genome growth."""
    from . import synth
    rb = None
    if beacon and (beacon.get("beacon_value_hex") or beacon.get("seed_hex") or beacon.get("value")):
        rb = {"beacon_value_hex": beacon.get("beacon_value_hex") or beacon.get("seed_hex") or beacon.get("value"),
              "beacon_round": int(beacon.get("beacon_round", beacon.get("round", 0))),
              "attested": bool(beacon.get("attested", False))}
    else:
        env = synth.resolve_beacon(None)
        if env:
            rb = {"beacon_value_hex": env["seed_hex"], "beacon_round": env["round"],
                  "attested": env["attested"]}
        else:
            rb = {"beacon_value_hex": os.urandom(32).hex(), "beacon_round": 0, "attested": False}
    return GenomeLedger.for_genome(genome_path).record(
        pairs, beacon=rb, provenance_by_key=provenance_by_key, kind=kind)
