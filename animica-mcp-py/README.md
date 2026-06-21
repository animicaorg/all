# Animica MCP (Python)

**Give your AI agent an affordable brain — and let it earn by powering the network.**

An [MCP](https://modelcontextprotocol.io) server exposing [Animica](https://animica.org)'s
OpenAI-compatible AI services as tools for any MCP-aware agent (Claude Desktop, Cursor,
Windsurf, custom bots). Two-sided: **use** cheap inference *and* **mine/provide** for ANM.

## Install (one command)

```bash
uvx animica-mcp          # or: pip install animica-mcp && animica-mcp
```

Claude Desktop / Cursor / Windsurf config:

```json
{
  "mcpServers": {
    "animica": {
      "command": "uvx",
      "args": ["animica-mcp"],
      "env": { "ANIMICA_API_KEY": "anm_live_your_key" }
    }
  }
}
```

No key? It auto-provisions a **free trial key** on first use. Get more at https://pool.animica.org.

## Tools

**Use:** `animica_chat`, `animica_code`, `animica_summarize`, `animica_embed`,
`animica_agent_run`, `animica_generate_app`, `animica_search_docs`,
`animica_price_quote`, `animica_create_api_key`, `animica_check_usage`

**Mine / provide:** `animica_mining_info`, `animica_pool_stats`,
`animica_mining_status`, `animica_become_provider`

## Config

| Env | Default |
|-----|---------|
| `ANIMICA_API_KEY` | (auto free trial) |
| `ANIMICA_BASE_URL` | `https://pool.animica.org/v1` |
| `ANIMICA_MODEL` / `ANIMICA_CODE_MODEL` | `anm-fast-8b` / `anm-code-7b` |
| `MCP_TRANSPORT` | `stdio` (or `http`) |

MIT licensed · https://github.com/animicaorg/all
