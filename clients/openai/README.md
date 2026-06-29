# Animica — OpenAI (ChatGPT Apps SDK + custom GPT)

OpenAI's Apps SDK is **MCP-based**, so the *same* Animica MCP server that powers
the Claude Code plugin also powers a ChatGPT App — just over the HTTP transport
instead of stdio. A custom GPT can alternatively call Animica's
OpenAI-compatible `/v1` API directly via **Actions** (see `gpt-actions-openapi.yaml`).

> **Security boundary (non-negotiable):** READ + COMPUTE only. No signing, no
> spending/transfers, no wallet mutation, no private-key access. Wallet/chain
> tools read public state only; the RPC seam enforces a read-only allow-list.

## Option A — ChatGPT App via the Apps SDK (MCP over HTTP)

Run the same server with the streamable-HTTP transport and expose it over HTTPS:

```bash
pip install "animica[mcp]"
animica mcp serve --transport http --host 0.0.0.0 --port 8765
# MCP endpoint: https://<your-host>/mcp   (put TLS / a tunnel in front)
```

Register that endpoint as the App's MCP connector (`app-connector.json`):

```json
{
  "name": "animica",
  "transport": "streamable-http",
  "url": "https://animica-mcp.example.com/mcp",
  "description": "Animica AI: ENA inference, verifiable quantum randomness, qDNA provenance, serverless-compute estimates, and read-only chain/pool stats."
}
```

All 15 READ/COMPUTE tools (AI + quantum first) are then available to the App.

## Option B — Custom GPT with Actions on the `/v1` API

If you only need the AI + randomness surface, a custom GPT can call Animica's
public, OpenAI-compatible API directly — no server to host. Import
`gpt-actions-openapi.yaml` as the GPT's Action schema. It exposes:

- `POST /v1/chat/completions` — ask the ENA AI network (lead capability).
- `GET  /v1/models` — list models.
- `GET  /beacon/latest` — latest verifiable quantum randomness round.
- `GET  /v1/pool/stats` — read-only mining/pool stats.

Auth: set the GPT Action's API key (Bearer) if your Animica org requires one;
the beacon and pool reads are public.

## Which to ship

- **ChatGPT App (Apps SDK / Option A)** when you want the full tool surface and
  in-chat rendering — this is the path to the OpenAI app directory.
- **Custom GPT (Option B)** for the fastest, hosting-free integration of the AI
  and verifiable-randomness features.
