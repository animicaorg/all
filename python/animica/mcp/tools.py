"""animica.mcp.tools — READ/COMPUTE tool implementations for the Animica MCP server.

Each tool is a plain function returning a string (JSON text, or model output for
inference). They import cleanly WITHOUT the optional ``mcp`` SDK, so the whole set
is unit-testable by mocking :mod:`animica.mcp.seams` (the network seam) and using
the in-process pure-compute modules directly.

Tool-surface policy (enforced here + at the seam): READ + COMPUTE only.
  * AI       — ENA inference (chat), model list, job status (read).
  * Quantum  — verifiable beacon (read) + verifiable draws/verify + qDNA gene
               verification (pure compute, works fully offline).
  * Chain    — head, block, account balance/nonce (READ-ONLY; no keys, no signing).
  * Pool     — mining/pool stats + network hashrate (read).
  * Studio   — serverless compute cost estimate (compute) + deployed-function list
               (read). NO remote execution / spending is exposed.

The ordering of :data:`TOOLS` leads with the AI + quantum capabilities on purpose
(marketplace-policy framing); the wallet/chain reads are clearly labelled
read-only and never expose anything that could move funds.
"""

from __future__ import annotations

import dataclasses
import functools
import json
from typing import Any, Callable

from . import seams

ERR = "⚠️"  # warning sign prefix for friendly errors


def _j(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, sort_keys=False)


def _guard(fn: Callable[..., str]) -> Callable[..., str]:
    """Turn seam/backend errors into a friendly string the agent can act on.

    Uses :func:`functools.wraps` so the wrapper keeps ``fn``'s real signature
    (via ``__wrapped__``) — FastMCP introspects that to build each tool's input
    schema, so the parameters must survive the wrap.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except seams.SeamError as e:
            return f"{ERR} {e}"
        except Exception as e:  # noqa: BLE001 — never crash the MCP transport
            return f"{ERR} {fn.__name__} failed: {e}"

    return wrapper


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


@_guard
def animica_info() -> str:
    """What Animica is and how to use it: the OpenAI-compatible AI API, the
    verifiable quantum randomness beacon, Studio serverless compute, and where to
    get an API key. Start here to orient an agent."""
    return _j({
        "summary": ("Animica is an open AI + blockchain network. It serves cheap "
                    "OpenAI-compatible inference (ENA), a verifiable quantum "
                    "randomness beacon, qDNA training provenance, and Studio "
                    "serverless compute paid in ANM."),
        "ai_api": {
            "base_url": seams.v1_base(),
            "openai_compatible": True,
            "models_example": ["anm-fast-8b", "anm-code-7b", "anm-pro-70b"],
            "get_api_key": seams.pool_url(),
        },
        "quantum_beacon": {"base_url": seams.beacon_url() + "/beacon",
                           "verifiable": True, "hash": "sha3-256"},
        "endpoints": {"rpc": seams.rpc_url(), "pool": seams.pool_url()},
        "docs": "https://animica.org/developers",
        "surface": "This MCP server exposes READ + COMPUTE tools only "
                   "(no signing, spending, or wallet mutation).",
    })


# --------------------------------------------------------------------------- #
# AI (ENA) — lead with these
# --------------------------------------------------------------------------- #


@_guard
def animica_ai_ask(prompt: str, model: str = "", max_tokens: int = 1024,
                   temperature: float = 0.4, system: str = "") -> str:
    """Ask Animica's ENA AI a question (OpenAI-compatible inference). Cheap general
    chat/reasoning. Optionally set ``model`` (default anm-fast-8b), ``system``
    prompt, ``max_tokens`` and ``temperature``. Returns the model's answer."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    out = seams.v1_chat(messages, model=model or None,
                        max_tokens=max_tokens, temperature=temperature)
    return out or "(empty response)"


@_guard
def animica_ai_models() -> str:
    """List the AI models available on Animica's OpenAI-compatible API."""
    return _j(seams.v1_models())


@_guard
def animica_ai_job_status(job_id: str) -> str:
    """Read the status of an ENA AI request/job by id (read-only). Returns the
    job's current status and result metadata if available."""
    return _j(seams.rpc_call("ena.getRequestStatus", [job_id]))


# --------------------------------------------------------------------------- #
# Quantum randomness — verifiable, mostly pure compute (works offline)
# --------------------------------------------------------------------------- #


@_guard
def animica_quantum_beacon_latest() -> str:
    """Get the latest round of Animica's verifiable quantum randomness beacon
    (round id, value, prev, aggregate commitment). Read-only."""
    return _j(seams.beacon_latest())


@_guard
def animica_quantum_draw(kind: str, request_id: str, params_json: str = "{}",
                         round_id: int = -1, beacon_hex: str = "") -> str:
    """Compute a verifiable quantum-random draw off a beacon round.

    ``kind`` is one of: lottery | choice | weighted | shuffle | range | coin |
    dice | bytes. ``request_id`` domain-separates this draw. ``params_json`` is a
    JSON object of the kind's parameters, e.g.::

        dice:    {"sides": 6, "count": 2}
        lottery: {"entries": ["a","b","c"], "k": 1}
        range:   {"lo": 1, "hi": 100, "count": 3}

    By default it uses the latest beacon round; pass ``beacon_hex`` (+ optional
    ``round_id``) to draw off a specific, already-known beacon value. The result
    is a pure function of (beacon, request_id, params) and is self-verifying."""
    from randomness.qrng import public as P

    params = json.loads(params_json or "{}")
    if beacon_hex:
        beacon = bytes.fromhex(beacon_hex)
        rnd = int(round_id) if int(round_id) >= 0 else 0
    else:
        rec = seams.beacon_latest()
        beacon = bytes.fromhex(rec["value"])
        rnd = int(rec["round"]) if int(round_id) < 0 else int(round_id)
    result = P.compute(str(kind), beacon, rnd, str(request_id), dict(params))
    result["verified"] = P.verify_result(result)
    return _j(result)


@_guard
def animica_quantum_verify(result_json: str) -> str:
    """Verify a quantum-random draw client-side: recompute it from its declared
    inputs (beacon, round, request_id, params) and confirm the output matches.
    Pure offline compute. Returns {"verified": true|false}."""
    from randomness.qrng import public as P

    result = json.loads(result_json)
    return _j({"verified": bool(P.verify_result(result))})


@_guard
def animica_qdna_verify_gene(gene_json: str) -> str:
    """Verify a qDNA training-genome gene seal: recompute its content-address
    (gene_id) and re-derive its quantum seal from the public beacon value, and
    confirm both match (tamper-evidence). Pure offline compute."""
    from animica.ena import genome as G

    gene = json.loads(gene_json)
    expected_id = G.derive_gene_id(gene.get("prompt"), gene.get("response"),
                                   gene.get("kind", "sft"))
    return _j({
        "verified": bool(G.verify_gene_seal(gene)),
        "expected_gene_id": expected_id,
        "claimed_gene_id": gene.get("gene_id"),
        "gene_id_matches": expected_id == gene.get("gene_id"),
    })


# --------------------------------------------------------------------------- #
# Chain reads (READ-ONLY — never expose keys/signing/spending)
# --------------------------------------------------------------------------- #


@_guard
def animica_chain_head() -> str:
    """Read the chain head: current height/hash and chain id. Read-only."""
    head = seams.rpc_call("chain.getHead")
    out: dict[str, Any] = {"head": head}
    try:
        out["chain_id"] = seams.rpc_call("chain.getChainId")
    except seams.SeamError:
        pass
    return _j(out)


@_guard
def animica_chain_block(height: int = -1, hash: str = "") -> str:
    """Read a block by ``height`` or by ``hash`` (read-only). With neither, reads
    the head block."""
    if hash:
        return _j(seams.rpc_call("chain.getBlockByHash", [hash]))
    if int(height) >= 0:
        return _j(seams.rpc_call("chain.getBlockByHeight", [int(height)]))
    return _j(seams.rpc_call("chain.getHead"))


@_guard
def animica_chain_account(address: str) -> str:
    """READ-ONLY account snapshot for an ``anim1...`` address: balance + nonce.
    This tool only reads public on-chain state; it never accesses keys, signs, or
    moves funds."""
    out: dict[str, Any] = {"address": address, "read_only": True}
    out["balance"] = seams.rpc_call("state.getBalance", [address])
    try:
        out["nonce"] = seams.rpc_call("state.getNonce", [address])
    except seams.SeamError:
        pass
    return _j(out)


# --------------------------------------------------------------------------- #
# Pool / mining stats (read-only)
# --------------------------------------------------------------------------- #


@_guard
def animica_pool_stats() -> str:
    """Live mining-pool stats: pool status plus active miner count. Read-only."""
    status = seams.pool_get("/v1/pool/status")
    out: dict[str, Any] = {"status": status}
    try:
        miners = seams.pool_get("/api/miners")
        items = miners.get("items") or miners.get("data") or []
        out["miner_count"] = len(items)
    except seams.SeamError:
        pass
    return _j(out)


@_guard
def animica_network_hashrate() -> str:
    """Read the current network hashrate from the chain. Read-only."""
    return _j({"network_hashrate": seams.rpc_call("chain.getNetworkHashrate")})


# --------------------------------------------------------------------------- #
# Studio serverless compute (compute estimate + read; NO remote execution)
# --------------------------------------------------------------------------- #


@_guard
def animica_studio_estimate(seconds: float = 15.0, gpu: bool = False,
                            mem_gb: float = 0.5) -> str:
    """Estimate the ANM cost of an Animica Studio run BEFORE paying
    (resource-seconds -> ANM). Pure local quote; runs and charges nothing."""
    try:
        from animica.studio import billing
    except ImportError:
        return f'{ERR} install the Studio SDK: pip install animica'
    q = billing.local_quote(est_seconds=seconds, gpu=gpu, mem_gb=mem_gb)
    return _j({"cost_anm": q.cost_anm, "cost_nanos": q.cost_nanos,
               "units": q.units, "source": q.source})


@_guard
def animica_studio_functions() -> str:
    """List functions deployed to Animica Studio (aicf.fn.list). Read-only."""
    return _j(seams.rpc_call("aicf.fn.list"))


# --------------------------------------------------------------------------- #
# Tool registry — single source of truth for the server + the CLI listing
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[..., str]
    category: str
    summary: str


def _spec(fn: Callable[..., str], category: str) -> ToolSpec:
    doc = (fn.__doc__ or "").strip().split("\n")[0].strip()
    return ToolSpec(name=fn.__name__, fn=fn, category=category, summary=doc)


# Order matters: AI + quantum lead (marketplace-policy framing), then read-only
# chain/pool, then Studio compute.
TOOLS: list[ToolSpec] = [
    _spec(animica_info, "discovery"),
    _spec(animica_ai_ask, "ai"),
    _spec(animica_ai_models, "ai"),
    _spec(animica_ai_job_status, "ai"),
    _spec(animica_quantum_beacon_latest, "quantum"),
    _spec(animica_quantum_draw, "quantum"),
    _spec(animica_quantum_verify, "quantum"),
    _spec(animica_qdna_verify_gene, "quantum"),
    _spec(animica_chain_head, "chain"),
    _spec(animica_chain_block, "chain"),
    _spec(animica_chain_account, "chain"),
    _spec(animica_pool_stats, "pool"),
    _spec(animica_network_hashrate, "pool"),
    _spec(animica_studio_estimate, "studio"),
    _spec(animica_studio_functions, "studio"),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
