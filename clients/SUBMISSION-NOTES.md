# animica-mcp — marketplace submission notes (5.1.2)

One MCP server (`animica mcp serve`, shipped in the `animica` wheel's `[mcp]`
extra) exposed to **both** ecosystems that converged on MCP:

- **Claude Code plugin** (stdio) — `clients/claude-plugin/`.
- **ChatGPT App / custom GPT** (streamable-HTTP, or `/v1` Actions) — `clients/openai/`.

We build + self-distribute + make these submission-ready. We **cannot** self-list
in the official curated directories — that needs Anthropic's / OpenAI's approval.
Build + submit is in scope; getting accepted is not.

## The one thing reviewers must see: READ + COMPUTE only

This is an AI product first; the crypto surface is strictly read-only. There is
**no** transaction signing, **no** spending/transfers, **no** wallet mutation,
**no** private-key/mnemonic access anywhere in the exposed tools. Enforced in two
layers:

1. **Tool surface** — only read/compute tools are registered (`animica.mcp.tools`).
   No tool calls a mutating operation. Studio is exposed as a *cost estimate*
   (a quote) plus a deployed-function *list* — never job submission or payment.
2. **Wire-level allow-list** — every JSON-RPC call goes through
   `animica.mcp.seams.rpc_call`, which refuses any method not on
   `RPC_READ_ALLOWLIST` (a hand-curated set of pure reads). A bug — or a
   malicious prompt — cannot reach `tx.sendRawTransaction`, `aicf.claim`,
   `pool.payout`, `wallet.sign`, `da.putBlob`, etc. Unit tests assert this.

Test proof (no network, no live node — HTTP seams mocked with `respx`,
pure-compute tools run in-process):

```
cd /root/animica && PYTHONPATH=/root/animica/python:/root/animica \
  /root/animica/.venv/bin/python -m pytest -q python/animica/mcp/tests
# 33 passed, 1 skipped  (skip = the "SDK absent" path; mcp is installed here)
```

## Marketplace-policy framing (crypto/financial risk mitigation)

Both marketplaces scrutinize crypto/financial tools. Lead every description with
the **AI** capabilities and label the rest read-only:

- **Primary value:** `animica_ai_ask` (ENA open-AI inference), verifiable
  **quantum randomness** (`animica_quantum_draw` / `_verify`, self-verifying),
  qDNA training-provenance verification, and serverless-compute estimates.
- **Secondary, read-only:** chain head/block/account (balance + nonce),
  pool/network stats. Each is labelled "READ-ONLY — never signs, spends, or
  moves funds."
- The plugin/app descriptions and the server `instructions` all state the
  boundary explicitly.

When submitting, call this out in the notes: *"No financial transactions are
initiated, signed, or settled by this connector. Wallet/chain tools are
read-only. The server cannot move funds or access keys."*

## Submit — Anthropic (Claude Code)

1. Publish `animica` 5.1.2 to PyPI (the wheel already contains `animica.mcp` and
   the `[mcp]` extra). `uvx --from "animica[mcp]" animica mcp serve` then works
   with zero manual install — this is what the plugin's `.mcp.json` uses.
2. Host `clients/claude-plugin/` as a plugin marketplace repo. It uses the proven
   marketplace-root + plugin-subdir shape (matches the installed official
   marketplace): `.claude-plugin/marketplace.json` at the root (one plugin entry,
   `source: "./animica"`, `category: "ai"`) and the plugin itself under
   `animica/` (`animica/.claude-plugin/plugin.json`, `animica/.mcp.json` running
   `uvx --from "animica[mcp]" animica mcp serve`, `animica/README.md`).
   Self-distribute today: `/plugin marketplace add <repo>` then
   `/plugin install animica@animica`.
3. Submit the plugin to the official Claude Code plugin directory (PR against the
   `claude-plugins` marketplace listing) with the read-only framing above.

## Submit — OpenAI (ChatGPT)

1. **App (Apps SDK):** run `animica mcp serve --transport http` behind HTTPS,
   register `https://<host>/mcp` (`clients/openai/app-connector.json`), and
   submit through the Apps SDK developer flow.
2. **Custom GPT (Actions):** import `clients/openai/gpt-actions-openapi.yaml`
   (AI + randomness + read-only stats over the public `/v1` SaaS). Fastest path,
   no hosting.

## Endpoints (override via env)

`ANIMICA_BASE_URL` (`/v1`), `ANIMICA_API_KEY`, `ANIMICA_RPC_URL`,
`ANIMICA_POOL_URL`, `ANIMICA_BEACON_URL`. Public defaults work from a laptop;
point them at a local node/beacon to use your own infrastructure.
