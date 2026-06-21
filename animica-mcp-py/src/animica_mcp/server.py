"""Animica MCP server (Python) — install with `uvx animica-mcp` or `pip install animica-mcp`.

Exposes Animica's OpenAI-compatible AI services as MCP tools so agents can use
cheap inference/code/embeddings/agents — and discover how to MINE/provide Animica
for ANM rewards. Two-sided: consume or supply.
"""
from __future__ import annotations
import json
import os

from mcp.server.fastmcp import FastMCP

from . import client as c
from .pricing import quote, estimate_tokens, pricing

mcp = FastMCP("animica")


def _j(o) -> str:
    return json.dumps(o, indent=2, default=str)


# ---------------- use (demand side) ----------------
@mcp.tool()
def animica_chat(prompt: str = "", model: str = "", max_tokens: int = 1024, temperature: float = 0.4) -> str:
    """Chat completion via Animica (OpenAI-compatible). Cheap general inference. Default model anm-fast-8b."""
    try:
        return c.chat([{"role": "user", "content": prompt}], model or None, max_tokens, temperature) or "(empty)"
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_code(task: str, language: str = "", context: str = "", max_tokens: int = 2048) -> str:
    """Generate or fix code with Animica's code model (anm-code-7b). Returns code."""
    try:
        sys = f"You are an expert {language} engineer. Output ONLY code, idiomatic and correct."
        user = f"{task}\n\nExisting context:\n{context}" if context else task
        return c.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                      os.environ.get("ANIMICA_CODE_MODEL", "anm-code-7b"), max_tokens, 0.2) or "(empty)"
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_summarize(text: str, style: str = "paragraph", max_words: int = 0) -> str:
    """Summarize text with Animica (style: bullets | paragraph | tldr)."""
    try:
        instr = f"Summarize the following as {style}" + (f" in under {max_words} words" if max_words else "") + ". No preamble."
        return c.chat([{"role": "system", "content": instr}, {"role": "user", "content": text}], None, 1024, 0.2) or "(empty)"
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_embed(input: str, model: str = "") -> str:
    """Create embedding vectors for text (RAG/search)."""
    try:
        return _j(c.embed(input, model or None))
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_agent_run(goal: str, model: str = "", max_steps: int = 8) -> str:
    """Run an autonomous Animica agent task. Falls back to a single reasoning pass if unavailable."""
    try:
        try:
            return _j(c.gateway_post("/agent/run", {"goal": goal, "model": model or None, "max_steps": max_steps}))
        except c.AnimicaError as e:
            if e.status == 404:
                return c.chat([{"role": "system", "content": "You are an autonomous problem-solver. Plan, then give the final result."}, {"role": "user", "content": goal}], None, 2048)
            raise
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_generate_app(description: str, stack: str = "") -> str:
    """Generate a small runnable app/scaffold (file plan + code)."""
    try:
        return c.chat([
            {"role": "system", "content": "Output a minimal runnable app as files, each `### path` then a fenced code block. Include a README + run command."},
            {"role": "user", "content": f"Build: {description}" + (f"\nStack: {stack}" if stack else "")},
        ], os.environ.get("ANIMICA_CODE_MODEL", "anm-code-7b"), 4096, 0.3) or "(empty)"
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_search_docs(query: str) -> str:
    """Answer a question about Animica (API, models, pricing, MCP) with links."""
    ctx = ("Animica is an OpenAI-compatible AI API at https://pool.animica.org/v1 (chat/completions, models, embeddings). "
           "Key: https://pool.animica.org. Docs: https://animica.org/developers. Models: anm-fast-8b, anm-code-7b, anm-pro-70b, anm-flagship-moe. "
           "Billing: prepaid credits + pay-as-you-go, crypto via NOWPayments. Mine: stratum+tcp://pool.animica.org:3333.")
    try:
        return c.chat([{"role": "system", "content": f"Answer ONLY from these Animica facts, concisely, with the link:\n{ctx}"}, {"role": "user", "content": query}], None, 512, 0.1)
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_price_quote(model: str = "anm-fast-8b", input_tokens: int = 1000, output_tokens: int = 500, input_text: str = "") -> str:
    """Estimate the USD cost of a request before running it."""
    it = estimate_tokens(input_text) if input_text else input_tokens
    return _j({**quote(model, it, output_tokens), "all_models": pricing()})


@mcp.tool()
def animica_create_api_key(name: str = "mcp-generated") -> str:
    """Create a new Animica API key for your account (requires an account token)."""
    try:
        return _j(c.gateway_post("/api-keys", {"name": name}))
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_check_usage() -> str:
    """Report your Animica usage and remaining credits."""
    try:
        return _j(c.gateway_get("/usage"))
    except c.AnimicaError as e:
        return f"⚠️ {e}"


# ---------------- mine / provide (supply side) ----------------
@mcp.tool()
def animica_mining_info() -> str:
    """How to earn ANM by powering Animica: stratum endpoint, miner downloads, pool config."""
    try:
        cfg = {}
        dl = {}
        try:
            cfg = c.pool_get("/api/mining/config")
        except Exception:
            pass
        try:
            dl = c.pool_get("/api/mining/downloads")
        except Exception:
            pass
        return _j({
            "one_command": f"curl -sL https://animica.org/mine.sh | sh -s -- --address <YOUR anim1...>",
            "stratum_endpoint": c.STRATUM_URL,
            "pool_config": cfg,
            "downloads": dl,
            "docs": "https://animica.org/mine",
        })
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_pool_stats() -> str:
    """Live pool health + miner count, to evaluate whether mining is worth it."""
    try:
        status = c.pool_get("/api/mining/status")
        miners = c.pool_get("/api/miners")
        items = miners.get("items") or miners.get("data") or []
        return _j({"status": status, "miner_count": len(items), "sample": items[:3]})
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_mining_status(address: str) -> str:
    """Look up a miner/worker by anim1... address — hashrate, shares, earnings."""
    try:
        miners = c.pool_get("/api/miners")
        items = miners.get("items") or miners.get("data") or []
        mine = [m for m in items if m.get("address") == address or address in (m.get("worker_id") or "")]
        return _j(mine if mine else {"message": f"No active worker for {address}. Start one with animica_mining_info.", "total_active": len(items)})
    except c.AnimicaError as e:
        return f"⚠️ {e}"


@mcp.tool()
def animica_become_provider() -> str:
    """Earn ANM by SERVING inference/compute — the supply side. One command to start."""
    return _j({
        "summary": "The same network you buy cheap inference from also pays you to power it.",
        "one_command": "curl -sL https://animica.org/mine.sh | sh -s -- --address <YOUR anim1...>",
        "stratum_endpoint": c.STRATUM_URL,
        "docs": "https://animica.org/mine",
    })


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
