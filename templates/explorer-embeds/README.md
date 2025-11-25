# Explorer Embeds — Template

Reusable, copy-pasteable widgets you can drop into any website to show **live Animica chain data** (latest head, block/tx viewers, balances, and simple feeds). This template renders a tiny JS bundle (`embed.js`) that discovers placeholder `<div>`s on your page and turns them into interactive components using your configured **RPC/WS** endpoints (and optionally an Explorer REST API).

---

## What you get

After generating the template (see **Quickstart**), the project ships:

- A small, framework-free embed runtime (`dist/embed.js`) that:
  - Connects to your **RPC HTTP** and **WebSocket** endpoints.
  - Auto-reconnects WS and throttles updates to avoid jank.
  - Supports **light/dark/auto** themes, configurable globally or per widget.
  - Can be hosted on any static origin (CDN, object storage, nginx).

- A starter set of widgets (all optional):
  - **HeadTicker** — live chain height + block hash snippet (WS `newHeads`).
  - **BlockCard** — fetch a block by `number` or `hash` (RPC lookup).
  - **TxCard** — fetch tx + receipt by `hash` (polls until mined).
  - **AddressCard** — balance & nonce for a bech32m address (read-only).
  - **EventFeed** — simple new-heads / pending-txs stream preview.
  - **MiniStats** — tiny tiles (chainId, peer head, time since last block).

> The initial set is intentionally minimal and RPC-only. If you also run an Explorer REST API, the same shell can render richer aggregates (fee trends, Γ/fairness charts) by enabling `explorer_api_url`.

---

## Live data sources

- **RPC (HTTP):** read methods for blocks, txs, receipts, balances.
- **WS:** `newHeads` and `pendingTxs` subscriptions for live updates.
- **Explorer API (optional):** if configured, some widgets will call
  `GET /api/...` endpoints to display aggregates/metrics; otherwise they fall back to RPC.

---

## Requirements

- Node.js 18+ (for local build/dev).
- An Animica node (or public devnet):
  - **RPC URL** (HTTP), e.g. `https://rpc.devnet.animica.xyz/rpc`
  - **WS URL**, e.g. `wss://rpc.devnet.animica.xyz/ws`
- Chain ID (string), e.g. `"1"` for main, `"1337"` for dev.

> For production, you only need the **built** JS/CSS files. No Node runtime is required on the server.

---

## Quickstart

### 1) Generate a project from this template

Using the repo’s template engine:

```bash
python -m templates.engine.cli render \
  --template templates/explorer-embeds \
  --out ./explorer-embeds-demo \
  --vars ./templates/explorer-embeds/variables.json

You can also override variables inline:

python -m templates.engine.cli render \
  --template templates/explorer-embeds \
  --out ./explorer-embeds-demo \
  --var project_slug=explorer-embeds-demo \
  --var rpc_url=https://rpc.devnet.animica.xyz/rpc \
  --var ws_url=wss://rpc.devnet.animica.xyz/ws \
  --var chain_id=1

2) Install & build

cd explorer-embeds-demo
npm install
npm run dev     # start local preview server
npm run build   # produce dist/embed.js and dist/embed.css

3) Host the static files

Upload dist/* to your CDN or static host. You will at least use:
	•	dist/embed.js
	•	dist/embed.css (if present; you can inline CSS if you prefer)

⸻

Embedding in your site

Add the script tag once per page:

<link rel="stylesheet" href="https://cdn.example.com/animica-embeds/embed.css">
<script
  src="https://cdn.example.com/animica-embeds/embed.js"
  async
  data-rpc-url="https://rpc.devnet.animica.xyz/rpc"
  data-ws-url="wss://rpc.devnet.animica.xyz/ws"
  data-chain-id="1"
  data-theme="auto"
></script>

Then place widgets where you want them:

<!-- Live head ticker -->
<div class="animica-widget" data-widget="head-ticker"></div>

<!-- Specific block by number -->
<div class="animica-widget" data-widget="block-card" data-number="100"></div>

<!-- Transaction by hash -->
<div class="animica-widget" data-widget="tx-card" data-hash="0xabc123..."></div>

<!-- Address balance/nonce -->
<div class="animica-widget" data-widget="address-card" data-address="anim1qxyz..."></div>

<!-- Event feed (new heads) -->
<div class="animica-widget" data-widget="event-feed" data-topic="newHeads"></div>

Per-widget overrides

Any top-level data-* you put on the <script> tag becomes the global default.
You can override per widget:

<div
  class="animica-widget"
  data-widget="head-ticker"
  data-theme="dark"
  data-ws-url="wss://alt.devnet/ws"
></div>


⸻

Configuration reference

These map to templates/explorer-embeds/variables.json. You can set defaults at build time (via the template engine) or per page using data-* attributes.

Key	Where to set	Description
rpc_url	build vars / data-rpc-url	HTTP RPC endpoint used for reads.
ws_url	build vars / data-ws-url	WS endpoint for subscriptions.
explorer_api_url	build vars / data-explorer-api-url	Optional: enables richer aggregates if available.
chain_id	build vars / data-chain-id	Chain ID string; used for display/validation.
site_title	build vars	Used on the built demo/preview page.
theme	build vars / data-theme	light | dark | auto (default auto).


⸻

Project layout (after render)

explorer-embeds-demo/
  ├─ package.json
  ├─ vite.config.ts
  ├─ tsconfig.json
  ├─ src/
  │  ├─ index.ts            # entry: parses data-attrs, mounts widgets
  │  ├─ runtime/
  │  │  ├─ rpc.ts           # minimal JSON-RPC client w/ retries
  │  │  ├─ ws.ts            # ws subscribe + backoff/reconnect
  │  │  └─ dom.ts           # mount helpers, theming
  │  ├─ widgets/
  │  │  ├─ HeadTicker.ts
  │  │  ├─ BlockCard.ts
  │  │  ├─ TxCard.ts
  │  │  ├─ AddressCard.ts
  │  │  └─ EventFeed.ts
  │  └─ styles/
  │     └─ embed.css
  ├─ public/
  │  └─ demo.html           # local preview with multiple widgets
  └─ dist/                   # build output

The widget modules are small and framework-agnostic (plain TS/DOM). If you prefer React/Vue, you can swap implementations while keeping the same data-widget contract.

⸻

Security & performance notes
	•	Read-only: Widgets only make public RPC/WS calls. They never handle secrets/keys.
	•	CORS/WS: Ensure your RPC/WS endpoints allow the embedding site’s origin.
	•	Rate limits: The runtime coalesces rapid updates; you can also limit via your gateway.
	•	Sizing: Use CSS to constrain widget width/height in your page layout.
	•	Multiple instances: You can render many instances; connections are pooled.

⸻

Troubleshooting
	•	CORS error in console: Add your site origin to the node/RPC allowlist.
	•	WS fails to connect: Check wss:// URL, TLS certs, and any reverse proxy Upgrade headers.
	•	No updates arriving: Confirm the node supports newHeads over WS and that you’re on the right chain_id.
	•	Tx never resolves: Ensure the tx hash is valid for this chain; some nodes prune pending quickly.

⸻

Extending
	•	Create a new file in src/widgets/YourWidget.ts.
	•	Export a mount(el, ctx) function. The runtime passes:
	•	ctx.rpc — JSON-RPC client
	•	ctx.ws — subscribe interface
	•	ctx.config — resolved config (from script tag + per-widget overrides)
	•	ctx.theme — current theme
	•	Register your widget name in src/index.ts map:

registry["your-widget"] = mountYourWidget;



⸻

License

The template scaffold is provided under the repository’s default license. See LICENSE-THIRD-PARTY.md for any third-party notices relevant to the embed build.

