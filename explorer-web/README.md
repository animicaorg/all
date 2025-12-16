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

### Allowed Hosts Configuration

The development server is configured with `allowedHosts` to prevent DNS rebinding attacks. By default, it allows:
- `explorer.animica.org` (production domain)
- `localhost`, `127.0.0.1`, `::1` (local development)

To add additional domains, edit `vite.config.ts` and add them to the `server.allowedHosts` array.

### Configuration Options

**Required Environment Variables**:
- `VITE_RPC_URL` - HTTP JSON-RPC endpoint (e.g., `https://rpc.animica.org/rpc`)
- `VITE_CHAIN_ID` - Numeric chain ID (e.g., `659658` for mainnet, `1337` for devnet)

**Optional Environment Variables**:
- `VITE_RPC_WS` - WebSocket endpoint for live updates (e.g., `wss://rpc.animica.org/ws`)
- `VITE_SERVICES_URL` - Studio services URL for contract verification (e.g., `http://localhost:8090`)

**Quick Setup Examples**:

```bash
# Mainnet
VITE_RPC_URL=https://rpc.animica.org/rpc
VITE_RPC_WS=wss://rpc.animica.org/ws
VITE_CHAIN_ID=659658

# Local Development
VITE_RPC_URL=http://localhost:8545
VITE_RPC_WS=ws://localhost:8546
VITE_CHAIN_ID=1337

# Testnet (if available)
VITE_RPC_URL=https://rpc.testnet.animica.org/rpc
VITE_RPC_WS=wss://rpc.testnet.animica.org/ws
VITE_CHAIN_ID=2
```

**Testing Your Configuration**:

```bash
# Test RPC connectivity
curl -X POST $VITE_RPC_URL \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'

# Should return: {"jsonrpc":"2.0","id":1,"result":659658}
```

**Troubleshooting**: If you see "Unable to fetch blockchain data", check:
1. RPC node is running and accessible
2. CORS is configured on the RPC server
3. Firewall allows connections to RPC port
4. `.env.local` has correct values and you've restarted the dev server

For detailed troubleshooting, see [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md).

---

## UI Features

### Modern, Responsive Design
- **Clean Layout**: Card-based design with improved spacing and visual hierarchy
- **Dark/Light Theme**: Automatically detects system preference, can be toggled manually
- **Responsive**: Optimized for desktop, tablet, and mobile devices
- **Loading States**: Animated loaders and skeleton screens while fetching data
- **Empty States**: Clear messaging when no data is available or node is disconnected

### Navigation & Pages
- **Home**: Network overview with live statistics, performance metrics, and PoIES analytics
- **Blocks**: Paginated list with filters (height range, producer, empty blocks)
- **Transactions**: Search and browse transactions with detailed views
- **Addresses**: Account information, balances, and transaction history
- **Contracts**: Deployed contracts with verification status
- **AICF**: AI/Quantum compute job dashboard
- **Data Availability**: DA proofs and blob information
- **Randomness**: Beacon rounds and VDF verification
- **Network**: Peer connections and network health

### Connection Status
The explorer displays real-time connection status:
- **Green dot**: Connected to node, receiving live updates
- **Red dot**: Disconnected, will attempt to reconnect
- Chain ID and RPC latency visible in the top navigation bar

---

## Usage Tips
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
