# Animica MCP server

**Give your AI agent an affordable brain — and let it earn by powering the network.**

`animica-mcp` exposes [Animica](https://animica.org)'s OpenAI‑compatible AI services
as [Model Context Protocol](https://modelcontextprotocol.io) tools, so any MCP‑aware
agent (Claude Desktop, Cursor, Windsurf, custom bots) can **discover and use Animica**
for cheap inference — and **discover how to mine/provide** Animica for ANM rewards.

It's two‑sided on purpose: the same network your agent buys cheap inference from
also **pays it to supply** inference. Demand and supply through one tool server.

## Tools

**Use (demand side)**
| Tool | What it does |
|------|--------------|
| `animica_chat` | Chat completion (OpenAI‑compatible). Cheap general inference. |
| `animica_code` | Code generation/fix with `anm-code-7b`. |
| `animica_summarize` | Summarize text (bullets / paragraph / tl;dr). |
| `animica_embed` | Embedding vectors for RAG/search. |
| `animica_agent_run` | Run an autonomous agent task. |
| `animica_generate_app` | Scaffold a small runnable app. |
| `animica_search_docs` | Answer questions about Animica with links. |
| `animica_price_quote` | Estimate USD cost before running a request. |
| `animica_create_api_key` | Mint a new Animica API key for your account. |
| `animica_check_usage` | Usage + remaining credits for your key. |

**Mine / provide (supply side)**
| Tool | What it does |
|------|--------------|
| `animica_mining_info` | How to start earning ANM: stratum endpoint, downloads, pool config. |
| `animica_pool_stats` | Live pool health + miner count (is it worth it?). |
| `animica_mining_status` | Look up a miner/worker by `anim1…` address. |
| `animica_become_provider` | Join as an AICF/ENA inference provider — get paid per request. |

## Quick start (local — Claude Desktop / Cursor / Windsurf)

1. Get an API key at **https://pool.animica.org**.
2. Add to your MCP config (`claude_desktop_config.json`, or Cursor/Windsurf MCP settings):

```json
{
  "mcpServers": {
    "animica": {
      "command": "npx",
      "args": ["-y", "animica-mcp"],
      "env": { "ANIMICA_API_KEY": "anm_live_your_key_here" }
    }
  }
}
```

That's it — restart the app and your agent can call `animica_chat`, `animica_code`,
`animica_mining_info`, etc. (Cursor: Settings → MCP; Windsurf: `~/.codeium/windsurf/mcp_config.json`
— same `mcpServers` block.)

## Hosted remote MCP

Run it as an HTTP MCP endpoint others can point at:

```bash
MCP_TRANSPORT=http PORT=8765 ANIMICA_API_KEY=anm_live_... npx animica-mcp
# → POST http://host:8765/mcp   (health: GET /healthz)
```

Or Docker:

```bash
docker build -t animica-mcp .
docker run -p 8765:8765 -e ANIMICA_API_KEY=anm_live_... animica-mcp
```

## Build from source

```bash
npm install && npm run build && ANIMICA_API_KEY=anm_live_... node dist/server.js
```

## Config

| Env | Default | |
|-----|---------|--|
| `ANIMICA_API_KEY` | — | Bearer key (https://pool.animica.org) |
| `ANIMICA_BASE_URL` | `https://pool.animica.org/v1` | OpenAI‑compatible inference API |
| `ANIMICA_MODEL` / `ANIMICA_CODE_MODEL` | `anm-fast-8b` / `anm-code-7b` | defaults |
| `ANIMICA_POOL_URL` / `ANIMICA_STRATUM_URL` | `https://pool.animica.org` / `stratum+tcp://pool.animica.org:3333` | mining side |
| `MCP_TRANSPORT` / `PORT` | `stdio` / `8765` | transport |

Errors are explicit (bad key, out of credits, rate limit) so agents can react.
MIT licensed.
