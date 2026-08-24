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
  * x402     — the public paid-API catalog (read). It reports what the paid
               endpoints cost and whether they are available; it never signs or
               settles a payment — that needs an x402-capable HTTP client.

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
# x402 paid-API catalog (READ-ONLY — reports prices, never pays)
# --------------------------------------------------------------------------- #


@_guard
def animica_x402_products() -> str:
    """Discover Animica's pay-per-request x402 APIs (USDC on Base): price, availability.

    Use this before spending anything on Animica: when a task might be served by
    a paid machine API (verifiable randomness, bulk Animica L1 chain data,
    priority AI inference), when you need a price up front, or when an Animica
    endpoint answered HTTP 402 and you need its payment terms. Returns the public
    catalog from ``/.well-known/x402`` verbatim — every product carries its path,
    decimal price, currency and a live ``available`` flag (plus the reason when
    it is false), so an agent can decide *before* paying.

    Read-only. This server holds no keys, signs nothing and pays nothing."""
    note = ("Prices are per request in USDC on Base (x402 protocol); paying "
            "requires an x402-capable HTTP client that signs locally — this MCP "
            "server only reports the catalog and never signs or pays.")
    url = seams.x402_catalog_url()
    try:
        catalog = seams.x402_catalog()
    except seams.SeamError as e:
        return _j({
            "catalog_url": url,
            "available": False,
            "error": f"could not fetch the x402 catalog: {e}",
            "hint": f"Retry later or read {url} directly. Nothing was charged.",
            "note": note,
        })
    return _j({"catalog_url": url, "catalog": catalog, "note": note})


# --------------------------------------------------------------------------- #
# PAID TOOLS.
#
# The old x402 story in MCP was "here is a catalog, go build an x402 client" —
# which is a two-hour integration to evaluate a half-cent API, so nobody did.
# These tools do the WORK instead. An agent with a prepaid credit token just
# calls them; an agent without one gets a structured `payment_required` answer
# carrying the price and where to get a token, which it can act on.
#
# The server still holds no chain keys and signs nothing. A credit token is a
# BEARER instrument the caller supplies (or the operator configures) and this
# server forwards as a header — exactly what the caller could do themselves.
# --------------------------------------------------------------------------- #


@_guard
def animica_web_fetch(url: str, credits: str = "", max_chars: int = 0) -> str:
    """PAID (~$0.005, or free with a credit token): fetch a public web page as clean text.

    Strips navigation, scripts and styling and returns readable text plus the
    title and metadata — the form a model can actually use. Reaches the public
    internet only: the hostname is resolved and every redirect hop re-checked
    against private, loopback, link-local and CGNAT ranges, so it cannot read
    internal services or cloud metadata.

    Pass `credits` (an anmc_… token) to pay from prepaid balance — no wallet,
    no gas, no x402 client. Without one you get the price and how to get a
    token, and nothing is charged."""
    body: dict[str, Any] = {"url": url}
    if max_chars > 0:
        body["max_chars"] = max_chars
    return _j(seams.x402_paid_call("/x402/web/fetch", body, credits=credits))


@_guard
def animica_web_ask(url: str, question: str, credits: str = "") -> str:
    """PAID (~$0.007): answer a question about ONE web page, WITH its sources.

    Fetches the page, chunks and embeds it, retrieves the closest passages and
    answers from those only — returning the passages alongside the answer so
    you can check it rather than trust it. When nothing on the page is relevant
    it DECLINES (`grounded: false`) instead of inventing an answer, which is
    the property that makes it worth paying for. Nothing is stored.

    Pass `credits` (anmc_… token) to pay from prepaid balance."""
    return _j(seams.x402_paid_call(
        "/x402/web/ask", {"url": url, "question": question}, credits=credits))


@_guard
def animica_embed(texts_json: str, credits: str = "") -> str:
    """PAID (~$0.005 per BATCH): embed up to 256 texts in one call (384-dim).

    all-MiniLM-L6-v2, unit-normalised so cosine similarity is a plain dot
    product. Sold per batch rather than per string because one settlement
    amortised across many vectors is the only honest shape for it.

    `texts_json` is a JSON array of strings. Pass `credits` to pay from balance."""
    try:
        texts = json.loads(texts_json)
    except Exception as e:  # noqa: BLE001
        return _j({"error": f"texts_json must be a JSON array of strings: {e}"})
    if not isinstance(texts, list) or not texts:
        return _j({"error": "texts_json must be a non-empty JSON array of strings"})
    return _j(seams.x402_paid_call("/x402/embed", {"texts": texts}, credits=credits))


@_guard
def animica_agent_index(query: str, limit: int = 10, max_price: float = 0.0,
                        callable_only: bool = False, credits: str = "") -> str:
    """PAID (~$0.006): search the agent-payable web — OUR index, not a reseller's.

    Full text over what ~18,000 machine-payable services actually publish:
    their descriptions, input schemas, PARAMETER NAMES, prices and settlement
    networks. Built from their own 402 payment challenges, which a general
    crawler discards as a dead page — so a query for `wallet_address` finds a
    service whose one-line blurb says only "token balance".

    Filter with `max_price` (USD ceiling; a service whose price is unknown is
    excluded, because unknown is not free) and `callable_only` (services that
    publish a call spec). Ranked by BM25 — no model, so the same query ranks
    the same way twice.

    Every response states how many documents were searched and how stale the
    oldest is. Use this to find WHAT TO CALL; use animica_web_search for the
    general web.

    Pass `credits` (an anmc_… token) to pay from prepaid balance."""
    body: dict[str, Any] = {"query": query}
    if limit and limit != 10:
        body["limit"] = limit
    if max_price and max_price > 0:
        body["max_price"] = max_price
    if callable_only:
        body["callable_only"] = True
    return _j(seams.x402_paid_call("/x402/index", body, credits=credits))


@_guard
def animica_web_search(query: str, limit: int = 10, credits: str = "") -> str:
    """PAID (~$0.010): search the live web and get ranked results.

    Titles, URLs and snippets, deduplicated by URL across every engine that
    answered — and the engines that answered are NAMED in the response, so you
    can judge the coverage you actually received rather than assume it was
    complete. `degraded: true` means some index did not respond.

    If no engine answers, the call FAILS and nothing is charged. An empty
    result set is never sold as an answer.

    Pass `credits` (an anmc_… token) to pay from prepaid balance — no wallet,
    no gas, no x402 client."""
    body: dict[str, Any] = {"query": query}
    if limit and limit != 10:
        body["limit"] = limit
    return _j(seams.x402_paid_call("/x402/search", body, credits=credits))


@_guard
def animica_web_research(query: str, pages: int = 4, max_chars: int = 0,
                         credits: str = "") -> str:
    """PAID (~$0.020): search the web AND read the top pages, in one call.

    The whole "look this up" loop — search, fetch the top N, strip the
    navigation off each — as a single call instead of a search plus N fetches.
    Returns each page's readable text with its title and source URL.

    Every page requested is accounted for: the ones that could not be read come
    back in `pages_failed` WITH the reason (403, timeout, paywall, no
    extractable text). A thin web and a broken fetcher look identical
    otherwise, and they need opposite responses from you.

    No model is called and nothing is summarised — you get the evidence, not an
    answer to trust. If nothing could be read, nothing is charged."""
    body: dict[str, Any] = {"query": query}
    if pages and pages != 4:
        body["pages"] = pages
    if max_chars > 0:
        body["max_chars"] = max_chars
    return _j(seams.x402_paid_call("/x402/research", body, credits=credits))


@_guard
def animica_web_contents(urls_json: str, max_chars: int = 0,
                         credits: str = "") -> str:
    """PAID (~$0.007 per BATCH): up to 10 URLs in, readable text of each out.

    The half of a retrieval pipeline you already have links for — search
    results, a sitemap, an RSS feed, a reading list. One call and one payment
    for the batch rather than one per page.

    `urls_json` is a JSON array of absolute http(s) URLs. Every URL is
    accounted for: unreadable ones come back in `failed` with the reason, and a
    hostname that does not resolve is reported as `dns_failure` rather than
    lumped in with addresses we refuse on purpose. If not one URL could be
    read, nothing is charged."""
    try:
        urls = json.loads(urls_json)
    except Exception as e:  # noqa: BLE001
        return _j({"error": f"urls_json must be a JSON array of URLs: {e}"})
    if not isinstance(urls, list) or not urls:
        return _j({"error": "urls_json must be a non-empty JSON array of URLs"})
    body: dict[str, Any] = {"urls": urls}
    if max_chars > 0:
        body["max_chars"] = max_chars
    return _j(seams.x402_paid_call("/x402/contents", body, credits=credits))


@_guard
def animica_notarize(digest: str, memo: str = "", credits: str = "") -> str:
    """PAID (~$0.006): anchor a SHA-256 digest on-chain with a verifiable proof.

    Commits the digest into the Animica data-availability layer and returns a
    commitment plus a Merkle inclusion proof, alongside the chain head observed
    at the time. VERIFICATION IS FREE AND PERMANENT at
    /x402/notarize/verify/{commitment} — a proof only the seller can check
    would be worth nothing.

    What it proves: this exact record is committed in the DA tree under the
    returned commitment. What it does NOT prove: a timestamp signed by the
    whole consensus set. Both are stated in the response rather than blurred."""
    body: dict[str, Any] = {"digest": digest}
    if memo:
        body["memo"] = memo
    return _j(seams.x402_paid_call("/x402/notarize", body, credits=credits))


@_guard
def animica_pq_verify(alg_id: int, message_hex: str, signature_hex: str,
                      public_key_hex: str, credits: str = "") -> str:
    """PAID (~$0.005): verify a post-quantum signature (ML-DSA-65 / Dilithium3 / SPHINCS+).

    Uses the same verifier the Animica chain runs to admit transactions, so an
    agent can check a PQ-signed attestation without shipping a post-quantum
    library. A signature that does not verify is a SUCCESSFUL call answering
    `ok: false` — "invalid" is the answer you paid for. Scheme 4099 (0x1003,
    ML-DSA-65) is the only one that can actually spend on Animica."""
    return _j(seams.x402_paid_call("/x402/pq/verify", {
        "alg_id": alg_id, "message": message_hex,
        "signature": signature_hex, "public_key": public_key_hex,
    }, credits=credits))


@_guard
def animica_credit_balance(credits: str = "") -> str:
    """FREE: balance, expiry and spend history for a prepaid credit token.

    Free by design — an agent must be able to check what it can afford BEFORE
    committing to a call, and charging for that would defeat the point."""
    tok = (credits or seams.x402_credits()).strip()
    if not tok:
        return _j({
            "error": "no credit token supplied",
            "hint": "pass `credits` (an anmc_… token) or set ANIMICA_X402_CREDITS on this server",
            "get_one": "https://animica.dev/x402/credits/buy (or ask ai@3vdc.com)",
        })
    return _j(seams.x402_paid_call("/x402/credits/balance", None,
                                   credits=tok, method="GET"))


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
    _spec(animica_x402_products, "x402"),
    # Paid tools: an agent doing real work calls these directly instead of
    # reading a catalog and deciding to "buy an API".
    _spec(animica_web_fetch, "x402-paid"),
    _spec(animica_web_ask, "x402-paid"),
    # Retrieval. An agent that can fetch one URL but cannot search has half a
    # pipeline; these were live on the gateway for a day before anything here
    # could reach them.
    # Our OWN index first: it answers "what should I call", which is the
    # question an agent has before it has a URL to fetch.
    _spec(animica_agent_index, "x402-paid"),
    _spec(animica_web_search, "x402-paid"),
    _spec(animica_web_research, "x402-paid"),
    _spec(animica_web_contents, "x402-paid"),
    _spec(animica_embed, "x402-paid"),
    _spec(animica_notarize, "x402-paid"),
    _spec(animica_pq_verify, "x402-paid"),
    _spec(animica_credit_balance, "x402-paid"),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
