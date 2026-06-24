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


# ---------------- Animica Studio (serverless compute) ----------------
# These tools let an agent RUN and DEPLOY Python compute on Animica Studio. They
# reuse the Studio SDK shipped with `pip install animica`; in local mode they run
# with zero infrastructure, in remote mode they escrow ANM and dispatch to the
# fleet (ANIMICA_STUDIO_MODE=remote + a wallet).


def _studio_run_dynamic(code: str, entry: str, args, kwargs, gpu: str, pip: str):
    import importlib.util
    import sys
    import tempfile

    import animica.studio as studio  # raises ImportError if animica isn't installed

    tmpdir = tempfile.mkdtemp(prefix="studio_agent_")
    path = os.path.join(tmpdir, "agent_job.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    spec = importlib.util.spec_from_file_location("agent_job", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_job"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    fn = getattr(mod, entry, None)
    if fn is None or not callable(fn):
        raise ValueError(f"function {entry!r} not found in submitted code")

    img = studio.Image.debian_slim()
    if pip:
        img = img.pip_install(*[p.strip() for p in pip.split(",") if p.strip()])
    app = studio.App("agent")
    wrapped = app.function(image=img, gpu=gpu or None)(fn)
    return wrapped.remote(*args, **kwargs), app.config.mode


@mcp.tool()
def studio_run(code: str, entry: str = "main", args_json: str = "[]",
               kwargs_json: str = "{}", gpu: str = "", pip: str = "",
               mode: str = "auto") -> str:
    """Run a Python function on Animica Studio serverless compute.

    Submit `code` (Python source), name the function to call via `entry`, and pass
    `args_json`/`kwargs_json` (JSON). Optionally request a `gpu` (e.g. "A100") and
    `pip` dependencies (comma-separated). `mode`: auto|local|remote. Returns the
    function's result. In local mode this runs in a sandbox with no chain; in
    remote mode it pays in ANM and runs on the fleet.
    """
    try:
        if mode:
            os.environ["ANIMICA_STUDIO_MODE"] = mode
        args = json.loads(args_json or "[]")
        kwargs = json.loads(kwargs_json or "{}")
        result, used_mode = _studio_run_dynamic(code, entry, args, kwargs, gpu, pip)
        return _j({"result": result, "mode": used_mode})
    except ImportError:
        return "⚠️ Studio SDK not installed. Run `pip install animica` to enable serverless compute."
    except Exception as e:  # noqa: BLE001 - report the function/dispatch error to the agent
        return f"⚠️ studio_run failed: {e}"


@mcp.tool()
def studio_estimate(seconds: float = 15.0, gpu: bool = False, mem_gb: float = 0.5) -> str:
    """Estimate the ANM cost of a Studio run before paying (resource-seconds → ANM)."""
    try:
        from animica.studio import billing
        q = billing.local_quote(est_seconds=seconds, gpu=gpu, mem_gb=mem_gb)
        return _j({"cost_anm": q.cost_anm, "cost_nanos": q.cost_nanos, "units": q.units, "source": q.source})
    except ImportError:
        return "⚠️ `pip install animica` to estimate Studio costs."


@mcp.tool()
def studio_list() -> str:
    """List functions deployed to Animica Studio (aicf.fn.list) on the configured node."""
    try:
        from animica.studio.client import BrokerClient
        from animica.studio.config import StudioConfig
        cfg = StudioConfig.from_env()
        client = BrokerClient(cfg.rpc_url, timeout=10.0)
        try:
            return _j(client.fn_list())
        finally:
            client.close()
    except ImportError:
        return "⚠️ `pip install animica` to use Studio."
    except Exception as e:  # noqa: BLE001
        return f"⚠️ registry unavailable: {e}"


@mcp.tool()
def studio_info() -> str:
    """What Animica Studio is and how an agent can use it (serverless compute, paid in ANM)."""
    return _j({
        "summary": "Animica Studio runs Python functions on a decentralized GPU/CPU fleet, paid in ANM. Modal-style serverless.",
        "use_from_agent": "Call studio_run(code, entry, args_json, gpu, pip). studio_estimate quotes cost; studio_list shows deployed functions.",
        "sdk": "pip install animica  →  import animica.studio as studio",
        "cli": "animica studio run app.py::fn  •  animica studio deploy app.py",
        "modes": "ANIMICA_STUDIO_MODE=local (no chain) | remote (pays ANM, runs on fleet)",
        "site": "https://studio.animica.org",
        "llms_txt": "https://studio.animica.org/llms.txt",
    })


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
