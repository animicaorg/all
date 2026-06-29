# Animica — Claude Code plugin

One Model Context Protocol (MCP) server that gives Claude **READ + COMPUTE** access
to Animica's AI and blockchain network. The server ships *inside the `animica`
Python wheel* (`animica mcp serve`); this directory is just the thin Claude Code
plugin wrapper around it.

> **Security boundary (non-negotiable):** every exposed tool is READ or COMPUTE
> only. The plugin **never** signs a transaction, spends/transfers funds, mutates
> a wallet, or reads a private key. Wallet/chain tools read public on-chain state
> only, and the JSON-RPC seam enforces a hard allow-list of read methods.

## What Claude can do with it

Lead capabilities (AI + verifiable randomness):

- `animica_ai_ask` — ask the Animica **ENA** open-AI network (OpenAI-compatible inference).
- `animica_ai_models` — list available models.
- `animica_quantum_draw` / `animica_quantum_verify` — generate and verify
  **quantum-random** draws (lottery, dice, shuffle, range, coin, bytes, …) off a
  publicly verifiable beacon; every result is self-verifying (pure compute).
- `animica_quantum_beacon_latest` — read the latest verifiable randomness round.
- `animica_qdna_verify_gene` — verify qDNA AI-training provenance (tamper-evidence).
- `animica_studio_estimate` / `animica_studio_functions` — price and list
  serverless compute (a quote only; nothing is submitted or paid).

Read-only chain / network context:

- `animica_chain_head`, `animica_chain_block`, `animica_chain_account` (balance + nonce, read-only),
- `animica_pool_stats`, `animica_network_hashrate`,
- `animica_info` — orient an agent (endpoints, where to get an API key).

## Install

The plugin runs the server via `uvx` (no manual install needed). The bundled
`.mcp.json` uses the Claude Code plugin format (server name at the top level):

```jsonc
// animica/.mcp.json
{
  "animica": {
    "command": "uvx",
    "args": ["--from", "animica[mcp]", "animica", "mcp", "serve"],
    "env": { "ANIMICA_BASE_URL": "https://pool.animica.org/v1" }
  }
}
```

If you already `pip install "animica[mcp]"`, you can instead add it directly:

```bash
claude mcp add animica -- animica mcp serve
```

### Self-distribute (until accepted into the official directory)

```bash
# point Claude Code at this folder (or the published repo subdir)
/plugin marketplace add animicaorg/all          # or a local path to clients/claude-plugin
/plugin install animica@animica
```

## Configuration (all optional, env vars)

| Variable             | Default                          | Purpose                              |
|----------------------|----------------------------------|--------------------------------------|
| `ANIMICA_BASE_URL`   | `https://pool.animica.org/v1`    | OpenAI-compatible AI API base        |
| `ANIMICA_API_KEY`    | *(none)*                         | API key for the AI API (if required) |
| `ANIMICA_RPC_URL`    | `https://rpc.animica.org/rpc`    | Node JSON-RPC (read-only)            |
| `ANIMICA_POOL_URL`   | `https://pool.animica.org`       | Mining-pool stats                    |
| `ANIMICA_BEACON_URL` | `https://pool.animica.org`       | Verifiable quantum beacon            |

Point any of these at a local node/beacon to use your own infrastructure.
