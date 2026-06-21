# Animica MCP — launch & distribution kit

Goal: maximum **compliant** discovery by bots, agents, and the developers who configure
them. No spam, no fake accounts, no review-bypass — every submission below is opt-in and
owner-reviewed (per the Animica growth ethics).

## 0. Prereqs (one-time, ~10 min) — these unlock everything
1. **Publish to npm** so `npx animica-mcp` works:
   ```bash
   cd animica-mcp && npm login && npm publish --access public
   ```
2. **Ship the gateway trial endpoint** (`POST /v1/keys/trial`) so the zero-config free
   trial works (see `../pool.animica.org-app` staged route). Until then the MCP falls
   back to a clear "get a free key" message.
3. (Optional) **Host the remote MCP** at `mcp.animica.org` (`MCP_TRANSPORT=http`, Docker)
   for the `remotes` entry in `server.json`.

## 1. Registries (where agents actually discover MCP servers)
Submit `server.json` / `smithery.yaml` (already in this repo):
- **Official MCP Registry** — `mcp-publisher publish` with `server.json` (`registry.modelcontextprotocol.io`).
- **Smithery** (smithery.ai) — connect the repo; `smithery.yaml` enables one-click install.
- **mcp.so**, **Glama.ai**, **PulseMCP**, **mcpservers.org** — submit via their "add server" forms.
- **awesome-mcp-servers** (GitHub) — open a PR adding one line under the right category.

Listing copy (reuse everywhere):
- **Title:** Animica — AI inference & agent tools for bots
- **Short:** Cheap OpenAI-compatible chat, code, embeddings & agent runs. Free trial. Bots can also earn ANM by powering the network.
- **Tags:** `ai`, `inference`, `openai-compatible`, `llm`, `embeddings`, `code-generation`, `agents`, `crypto`, `mining`
- **Category:** AI / LLM providers
- **Install:** `npx -y animica-mcp` · **Docs:** https://animica.org/developers · **Key:** https://pool.animica.org

## 2. npm / GitHub SEO
- npm `keywords` already set (mcp, openai, llm, inference, agents…).
- README leads with the agent value prop + one-line install.
- Topics on the GitHub repo: `mcp`, `model-context-protocol`, `ai`, `llm`, `openai-compatible`, `agents`.

## 3. Use-case landing pages (SEO — humans configuring bots search these)
Create static pages (Astro `website/`), each: headline, code sample, one-line install, free-trial CTA, FAQ, OG image, schema markup:
- Animica for Discord / Telegram / Slack bots
- Animica MCP server (install guide)
- OpenAI-compatible API for AI agents
- Cheap AI inference for autonomous agents
- LangChain / LiteLLM / AutoGen / CrewAI + Animica

## 4. Launch posts (value-first, Animica disclosed, no spam)
- **X/Twitter:** "Your agent shouldn't need a credit card to think. `npx -y animica-mcp` → OpenAI-compatible chat/code/embeddings with free trial credits, crypto billing, and your bot can even *earn* by powering the network. Open source."
- **Reddit (r/LocalLLaMA, r/mcp — read rules, educational):** a genuine write-up of the two-sided MCP (consume + mine), with the code, not a drive-by link.
- **Hacker News (Show HN):** "Show HN: Animica MCP — an MCP server where your agent can buy *or sell* inference."
- **Discord/communities:** announce only where on-topic and allowed; lead with the install + a real demo.

## 5. Bot integration templates (drive installs)
Ship ready-to-run templates (each: only needs a key, shows est. cost, Dockerfile, .env.example, README):
LangChain · LiteLLM config · Discord.js · Telegram · Slack · website widget · MCP client example.

## 6. Compliance guardrails (hard rules)
- No mass DMs, no fake stars/engagement, no impersonation, no review-bypass, no scraping.
- Outreach = contextual + helpful + disclosed, owner-approved before posting.
- Free-trial abuse controls live in the gateway trial endpoint (per-IP rate limit, small grant, flagged keys).
