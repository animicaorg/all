# Explorer Web — README

A lightweight, secure, and fast web explorer for Animica-compatible networks. It visualizes chain activity (blocks, transactions, addresses, logs) and connects directly to a node RPC (HTTP + WebSocket). No server-side signing, no secrets stored.

---

## Highlights

- **Live Heads** — auto-updating latest blocks via WS subscriptions
- **Blocks View** — height, timestamp, proposer, gas usage, PoIES/DA breakdown
- **Transaction Details** — status, fees, decoded inputs/outputs, logs, raw CBOR
- **Address Insights** — balance, nonce, recent activity, contract flag
- **Search** — by hash, height, or address with resilient fuzzy helpers
- **Contract Awareness** — links to verification artifacts (if studio-services available)
- **Responsive UI** — works well on desktop and mobile
- **Zero-Config Deploy** — static bundle (Vite), content-hashed assets, safe caching

---

## Architecture

**TypeScript + React + Vite (SPA)**

- **Data sources**
  - **Node RPC (required):** HTTP JSON-RPC for reads; WebSocket for `newHeads`.
  - **Studio Services (optional):** fetch verification/artifacts metadata if available.

**Key concepts**
- **Strict CORS:** The app is static; CORS must be allowed on the RPC/Services origins.
- **Immutable assets:** Content-hashed JS/CSS; only `index.html` should be no-store.
- **Security-first:** No private keys or server-side signing. Read-only explorer.

**Directory sketch (simplified)**

explorer-web/
src/               # React app
public/            # static files
package.json
tsconfig.json
vite.config.ts
.env.example

---

## Quickstart — Connect to Devnet

> Prereqs: Node 18+ (or 20+), pnpm 8+ (or npm/yarn), a running devnet RPC with WS.

1) **Install**
```bash
pnpm install

	2.	Configure environment

Create .env.local (copy from .env.example if present) with your devnet values:

VITE_RPC_URL=http://127.0.0.1:8545
VITE_RPC_WS=wss://127.0.0.1:8546
VITE_CHAIN_ID=1337
# Optional (only if you run studio-services for verification links):
VITE_SERVICES_URL=http://127.0.0.1:8787

	3.	Run in dev mode

pnpm dev

Vite will print a local URL. Open it in your browser; you should see live blocks if WS is reachable.

⸻

Usage Tips
	•	Search bar accepts:
	•	Block height (e.g., 12345)
	•	Transaction hash (0x…)
	•	Address (bech32 or hex, depending on network rules)
	•	Live Mode toggles WS subscription; disable if your RPC doesn’t expose WS.
	•	Decode toggles between human-readable and raw hex/CBOR for inputs/logs.

⸻

Build & Preview

pnpm build
pnpm preview

Artifacts land in dist/:
	•	index.html: no-store
	•	assets/*: public, max-age=31536000, immutable

⸻

Deployment (Static Hosting)

Any static host/CDN works (Cloudflare Pages, Netlify, Vercel, S3+CloudFront, NGINX).

Recommended headers

Cache-Control:
  - /index.html: no-store
  - /assets/*: public, max-age=31536000, immutable
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  connect-src 'self' https://<your-rpc-host> wss://<your-rpc-host> https://<your-services-host>;
  frame-ancestors 'none';
  base-uri 'self';
  object-src 'none';
  worker-src 'self';
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload

Adjust connect-src to include your RPC and Services origins (HTTPS/WSS).

⸻

Troubleshooting
	•	No live blocks: Check VITE_RPC_WS, firewall, and WS endpoint path. Some gateways require /ws.
	•	CORS errors: RPC/Services must allow your explorer’s origin. Avoid * in production; use an allowlist.
	•	404 on reload/links: Ensure SPA fallback to index.html on your host/CDN.
	•	Mixed content: Use HTTPS and WSS for all endpoints.

⸻

Roadmap
	•	Advanced filters (method selectors, topics)
	•	Address labels & tags (client-side only)
	•	Export to CSV/JSON and shareable permalinks
	•	Light client verification badges (if headers/DA proofs are provided)

⸻

License

This explorer is part of the Animica tooling stack and follows the repository’s root LICENSE.
