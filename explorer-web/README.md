# Explorer Web — README

A lightweight, secure, and fast web explorer for Animica-compatible networks. It visualizes chain activity (blocks, transactions, addresses, logs) and connects directly to a node RPC (HTTP + WebSocket). No server-side signing, no secrets stored.

---

## Highlights

- **Live Heads** — auto-updating latest blocks via WS subscriptions
- **Persistent Cache** — IndexedDB-based local caching for offline operation and improved performance
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
- **Local caching:** IndexedDB cache stores blockchain data locally for offline access and improved performance. See [CACHING.md](docs/CACHING.md) for details.

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

## Production hosting (static)

- Build once with `pnpm build` and serve the generated `dist/` directory from a static server (nginx, caddy, S3+CDN).  
  **Do not run `pnpm dev` or expose the Vite HMR client in production.**
- Prefer the built-in same-origin proxy path `/rpc` to avoid CORS issues; override via `VITE_RPC_URL` or `?rpc=` only for debugging.

Example nginx snippet for `explorer.animica.org`:

```nginx
server {
  listen 80;
  server_name explorer.animica.org;

  location / {
    root /var/www/explorer/dist;
    try_files $uri /index.html;
    add_header Cache-Control "public, max-age=31536000, immutable";
  }

  location = /index.html {
    add_header Cache-Control "no-store";
  }

  location /rpc {
    proxy_pass https://rpc.animica.org/rpc;
    proxy_set_header Host rpc.animica.org;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Connection "";

    if ($request_method = OPTIONS) {
      add_header 'Access-Control-Allow-Origin' $http_origin;
      add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
      add_header 'Access-Control-Allow-Headers' '*';
      add_header 'Access-Control-Max-Age' 1728000;
      return 204;
    }
  }
}
```

## Troubleshooting RPC Connectivity

### Common Issues and Solutions

#### "Unable to Connect to RPC Node"

If the explorer displays a disconnected status or shows "Unable to Connect to RPC Node", follow these steps:

**1. Verify RPC Node is Running**
```bash
# Check if the RPC server is accessible
curl -X POST $VITE_RPC_URL \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'

# Expected response: {"jsonrpc":"2.0","id":1,"result":"1"}
# (or your actual chain ID)
```

**2. Check Browser Console for Detailed Errors**
- Open browser DevTools (F12)
- Look for `[network]` prefixed logs showing connection attempts
- Common errors and their meanings:
  - `Network error` / `fetch failed`: RPC server is not reachable (check URL, firewall)
  - `CORS error`: RPC server needs to allow your origin (see CORS section below)
  - `Chain ID mismatch`: Configured chain ID doesn't match the node's chain ID
  - `HTTP 404/500`: RPC endpoint path is incorrect or server has issues

**3. Verify CORS Configuration**
The explorer runs in the browser and requires CORS headers from the RPC server:

```python
# Example: Python/FastAPI RPC server CORS setup
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://explorer.animica.org"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**4. Check Environment Variables**
- Ensure `.env.local` exists and has correct values
- After changing `.env.local`, restart the dev server
- Verify VITE_RPC_URL doesn't have trailing slashes
- Confirm VITE_CHAIN_ID matches your node's chain ID

**5. Test with Alternative URLs**
```bash
# For local development, try multiple formats:
VITE_RPC_URL=http://localhost:8545      # IPv4 localhost
VITE_RPC_URL=http://127.0.0.1:8545      # Explicit IPv4
VITE_RPC_URL=http://[::1]:8545          # IPv6 localhost

# If using Docker, use host.docker.internal:
VITE_RPC_URL=http://host.docker.internal:8545
```

**6. Verify Network Connectivity**
```bash
# Test basic connectivity
ping 127.0.0.1

# Check if port is open and listening
netstat -an | grep 8545
# or
lsof -i :8545

# Try telnet to test port connectivity
telnet localhost 8545
```

### Debugging Tips

**Enable Verbose Logging**
The explorer logs detailed connection information to the browser console:
- `[network] Creating RPC client with URL:` - Shows the URL being used
- `[network] Connecting to RPC:` - Connection attempt started
- `[network] RPC client created successfully` - Client initialized
- `[network] Fetching chain ID...` - Attempting to fetch chain ID
- `[network] Chain ID: X` - Successfully retrieved chain ID
- `[network] Connection established successfully` - Full connection succeeded

**Connection Status Indicator**
The top bar shows a colored dot indicating connection status:
- 🟢 **Green**: Connected and receiving live updates
- 🟡 **Yellow**: Connecting, attempting to establish connection
- 🔴 **Red**: Disconnected, unable to reach RPC node

### Known Issues

**Issue**: Chain ID Mismatch
```
Error: Chain ID mismatch: expected 1, got 659658
```
**Solution**: Update VITE_CHAIN_ID in `.env.local` to match your node's chain ID

**Issue**: WebSocket Connection Failed (but HTTP works)
- The explorer will fall back to HTTP polling automatically
- Live updates may be delayed (4-second polling interval)
- Check if WebSocket port (8546) is accessible

**Issue**: Mixed Content (HTTPS page loading HTTP RPC)
- Modern browsers block HTTP requests from HTTPS pages
- Either use HTTPS for RPC or access explorer via HTTP (localhost only)

### Getting Help

If you're still experiencing issues:
1. Check the [GitHub Issues](https://github.com/animicaorg/all/issues) for similar problems
2. Share your browser console logs (with `[network]` entries)
3. Include your `.env.local` configuration (remove sensitive values)
4. Mention your OS, browser, and Node.js version

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

### Local Caching & Offline Mode
The explorer includes persistent local caching for improved performance and offline operation:
- **Automatic Caching**: Blockchain data is automatically cached in IndexedDB
- **Background Sync**: Continuously synchronizes with RPC in the background
- **Offline Access**: Browse cached blocks and transactions when RPC is unavailable
- **Cache Status**: View cache statistics and sync progress in the footer
- **Cache Management**: Clear cache if needed via the cache status panel

For more details, see [CACHING.md](docs/CACHING.md).

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
